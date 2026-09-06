"""
src/extract_nfhl_msc.py
========================
Extracts FEMA's current effective National Flood Hazard Layer (NFHL)
flood-hazard-area polygons for one state, via the FEMA Map Service
Center (MSC) portal's advanceSearch/downloadProduct flow.

DATA SOURCE: FEMA Map Service Center (MSC), NFHL_STATE_DATA product.
- Portal (manual browsing equivalent of what this script automates):
  https://msc.fema.gov/portal/advanceSearch
- This script does not use a single fixed download URL. Instead, it
  calls three MSC endpoints in sequence to discover the CURRENT
  statewide NFHL package name, then downloads it:
  1. GET  https://msc.fema.gov/portal/advanceSearch?getCommunity={county_fips}&state={state_fips}
  2. POST https://msc.fema.gov/portal/advanceSearch  (form-encoded search)
  3. GET  https://msc.fema.gov/portal/downloadProduct?productTypeID=NFHL&productSubTypeID=NFHL_STATE_DATA&productID={product_NAME}

FORMAT: FEMA does not publish a fixed, predictable URL for a state's
current NFHL package -- the exact product identifier (e.g.
"NFHL_44_20260622") includes a date suffix and changes roughly every two
weeks as FEMA republishes. This script therefore looks the current
product name up fresh on every run. The downloaded product is a ZIP 
containing a file geodatabase (.gdb), from which this script reads 
the S_FLD_HAZ_AR (flood-hazard area polygon) feature class specifically 
-- other layers in the same geodatabase (e.g. base flood elevations, 
cross-sections) are not extracted by this script.

SPECIAL CONSIDERATIONS:
- FEMA's search form requires a county FIPS code and a FEMA "community"
  ID as required form fields, even though the result returned --
  NFHL_STATE_DATA -- is the same statewide package regardless of which
  specific county/community was used to reach it. This script obtains
  a county FIPS code by calling extract_county_boundaries() from
  census_counties.py and using the first county returned. Running this 
  script therefore requires census_counties.py to be importable and working, 
  since a valid county FIPS code is a runtime prerequisite for reaching FEMA's
  statewide product.
- The FEMA community ID is likewise obtained by calling MSC's
  getCommunity endpoint for that same arbitrarily-chosen county and
  using the first community returned.
- Reading the downloaded file geodatabase requires Fiona (with the
  OpenFileGDB driver) to be installed; this script raises an
  ImportError with an explicit `pip install fiona` instruction if missing.
- If FEMA does not publish a combined NFHL_STATE_DATA product for a
  given state (some states/territories may only have
  NFHL_COUNTY_DATA), this script raises an error.
- If DFIRM_ID is present in the flood-hazard layer's attributes, a
  county_fips column is derived by taking its first five characters;
  this is not guaranteed to be present for every FEMA product vintage.
- Output CRS is whatever the source geodatabase itself declares (FEMA
  NFHL data is typically EPSG:4269 / NAD83).
- The per-run temporary extraction folder
  (data/raw/tmp_nfhl_msc_{STATE_ABBR}/) is removed automatically after
  each run, including when an error occurs.
- No API key is required, but this script depends on FEMA's MSC portal
  responding in the same JSON/form shape; a change to FEMA's portal 
  implementation could break the community-ID or product-lookup steps.

OUTPUT: data/raw/nfhl_flood_zones_{STATE_ABBR}_MSC.parquet -- one row
per flood-hazard-area polygon, with FEMA's original S_FLD_HAZ_AR
attribute columns (FLDZONE, ZONESUBTY, SFHATF, DFIRM_ID, etc.) plus a
derived county_fips column when DFIRM_ID is available.

SINGLE ENTRY POINT: extract_nfhl_flood_zones_msc() is the only function
meant to be called from outside this module.

USAGE:
Interactive:
    from extract_nfhl_msc import extract_nfhl_flood_zones_msc
    nfhl_gdf = extract_nfhl_flood_zones_msc()

Headless CLI:
    Default:
        python src/extract_nfhl_msc.py
    Specify state:
        python src/extract_nfhl_msc.py --state-fips 25 --state-abbr MA
"""

from __future__ import annotations
import argparse
import io
import shutil
import time
import zipfile
from pathlib import Path
import geopandas as gpd
import requests
from config import DEFAULT_HEADERS, DEFAULT_STATE_ABBR, DEFAULT_STATE_FIPS, RAW_DIR
from census_counties import extract_county_boundaries

HEADERS = DEFAULT_HEADERS
MSC_BASE = "https://msc.fema.gov/portal"
MSC_SEARCH_URL = f"{MSC_BASE}/advanceSearch"
MSC_DOWNLOAD_URL = f"{MSC_BASE}/downloadProduct"
NFHL_POLYGON_LAYER_NAME = "S_FLD_HAZ_AR"

def _request(method, url, *, timeout, attempts=3, **kwargs):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(method, url, headers=HEADERS, timeout=timeout, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                print(f" Attempt {attempt} failed ({exc}); retrying...")
                time.sleep(3 * attempt)
    raise RuntimeError(f"Request failed after {attempts} attempts: {url}. Last error: {last_error}")

def _get_any_county_fips(state_fips, state_abbr, output_dir, tiger_year):
    counties = extract_county_boundaries(
        state_fips=state_fips,
        state_abbr=state_abbr,
        output_dir=output_dir,
        tiger_year=tiger_year,
    )
    if counties.empty:
        raise RuntimeError(f"No county boundaries were available for {state_abbr}.")
    return (counties["STATEFP"].astype(str) + counties["COUNTYFP"].astype(str)).iloc[0]

def _get_any_community_id(county_fips, state_fips):
    response = _request(
        "GET", MSC_SEARCH_URL,
        params={"getCommunity": county_fips, "state": state_fips},
        timeout=60,
    )
    try:
        communities = response.json()
    except ValueError as exc:
        raise RuntimeError(f"getCommunity did not return JSON: {response.text[:500]}") from exc
    if not communities:
        raise RuntimeError(f"No FEMA communities were returned for county {county_fips}.")
    community_id = communities[0].get("value")
    if community_id is None:
        raise KeyError(f"Expected 'value' in FEMA response; found {list(communities[0].keys())}.")
    return str(community_id)

def _lookup_current_state_product(state_fips, county_fips, community_id):
    form_data = {
        "utf8": "✓", "affiliate": "fema", "query": "", "selstate": state_fips,
        "selcounty": county_fips, "selcommunity": community_id,
        "jurisdictionkey": "", "jurisdictionvalue": "", "searchedCid": community_id,
        "searchedDateStart": "", "searchedDateEnd": "", "txtstartdate": "",
        "txtenddate": "", "method": "search",
    }
    response = _request("POST", MSC_SEARCH_URL, data=form_data, timeout=90)
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"MSC advanceSearch did not return JSON: {response.text[:500]}") from exc
    state_data = payload.get("EFFECTIVE", {}).get("NFHL_STATE_DATA", [])
    if not state_data:
        raise RuntimeError(
            f"No EFFECTIVE.NFHL_STATE_DATA entry was returned for state FIPS {state_fips}."
        )
    record = state_data[0]
    if not record.get("product_NAME"):
        raise KeyError("The MSC product record did not include product_NAME.")
    print(
        f" Current state product: {record['product_NAME']} "
        f"(effective {record.get('product_EFFECTIVE_DATE_STRING')}, "
        f"posted {record.get('product_POSTING_DATE_STRING')}, "
        f"size {record.get('product_FILE_SIZE')})"
    )
    return record

def _download_product(product_name, product_size):
    print(f" Downloading {product_name}.zip from MSC (reported size: {product_size})...")
    response = _request(
        "GET", MSC_DOWNLOAD_URL,
        params={"productTypeID": "NFHL", "productSubTypeID": "NFHL_STATE_DATA", "productID": product_name},
        timeout=300,
    )
    content_type = response.headers.get("Content-Type", "").lower()
    if "zip" not in content_type and len(response.content) < 100_000:
        raise RuntimeError(
            f"MSC did not return a ZIP. Content-Type: {content_type}; "
            f"size: {len(response.content)} bytes."
        )
    return response.content

def _read_flood_hazard_polygons(zip_bytes, temp_dir):
    temp_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        archive.extractall(temp_dir)
    geodatabases = list(temp_dir.rglob("*.gdb"))
    if not geodatabases:
        raise RuntimeError("No file geodatabase was found after extracting the FEMA ZIP.")
    gdb_path = geodatabases[0]
    print(f" Found geodatabase: {gdb_path.name}")
    try:
        import fiona
        layers = fiona.listlayers(str(gdb_path))
    except ImportError as exc:
        raise ImportError("Install Fiona to read the FEMA file geodatabase: pip install fiona") from exc
    if NFHL_POLYGON_LAYER_NAME not in layers:
        raise KeyError(f"Expected layer {NFHL_POLYGON_LAYER_NAME!r}; available: {layers}")
    print(f" Loading layer {NFHL_POLYGON_LAYER_NAME}...")
    return gpd.read_file(gdb_path, layer=NFHL_POLYGON_LAYER_NAME)

def extract_nfhl_flood_zones_msc(
    state_fips=DEFAULT_STATE_FIPS,
    state_abbr=DEFAULT_STATE_ABBR,
    output_dir=RAW_DIR,
    *,
    tiger_year=2023,
    force=False,
):
    """Extract FEMA MSC's current effective flood-hazard-area polygons for one state."""
    state_fips = str(state_fips).zfill(2)
    state_abbr = state_abbr.strip().upper()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"nfhl_flood_zones_{state_abbr}_MSC.parquet"
    if out_path.exists() and not force:
        print(f" [skip] {out_path.name} already exists")
        return gpd.read_parquet(out_path)
    print(f" Getting a valid county FIPS for {state_abbr}...")
    county_fips = _get_any_county_fips(state_fips, state_abbr, output_dir, tiger_year)
    print(f" Using county FIPS: {county_fips}")
    print(f" Looking up a FEMA community ID for county {county_fips}...")
    community_id = _get_any_community_id(county_fips, state_fips)
    print(f" Using community ID: {community_id}")
    print(f" Looking up the current statewide NFHL package for {state_abbr}...")
    product_record = _lookup_current_state_product(state_fips, county_fips, community_id)
    zip_bytes = _download_product(product_record["product_NAME"], product_record.get("product_FILE_SIZE"))
    print(f" Downloaded {len(zip_bytes) / 1_000_000:.1f} MB. Extracting...")
    temp_dir = output_dir / f"tmp_nfhl_msc_{state_abbr}"
    try:
        flood_zones = _read_flood_hazard_polygons(zip_bytes, temp_dir)
        print(f" Loaded {len(flood_zones):,} flood-hazard polygons (CRS: {flood_zones.crs}).")
        if "DFIRM_ID" in flood_zones.columns:
            flood_zones["county_fips"] = flood_zones["DFIRM_ID"].astype(str).str[:5]
        flood_zones.to_parquet(out_path, index=False)
        print(f" Saved {len(flood_zones):,} NFHL flood-zone polygons to {out_path}")
        return flood_zones
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract FEMA MSC NFHL flood-hazard polygons.")
    parser.add_argument("--state-fips", default=DEFAULT_STATE_FIPS)
    parser.add_argument("--state-abbr", default=DEFAULT_STATE_ABBR)
    parser.add_argument("--output-dir", default=str(RAW_DIR))
    parser.add_argument("--tiger-year", type=int, default=2023)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    extract_nfhl_flood_zones_msc(
        state_fips=args.state_fips,
        state_abbr=args.state_abbr,
        output_dir=args.output_dir,
        tiger_year=args.tiger_year,
        force=args.force,
    )
