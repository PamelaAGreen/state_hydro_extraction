"""
src/usace_nsi_structures.py
============================
Extracts point-level structure records from the USACE National
Structure Inventory (NSI) for every county in one U.S. state.

DATA SOURCE: USACE National Structure Inventory (NSI) API.
- Documentation:
  https://www.hec.usace.army.mil/confluence/nsi
- API base endpoint used by this script:
  https://nsi.sec.usace.army.mil/nsiapi/structures

FORMAT: The NSI API does not support requesting an entire state's
structures in a single call -- it is queried per county, using a
five-digit county FIPS code passed as the fips parameter (fmt=fc
requests results as a GeoJSON FeatureCollection). This script obtains
the full list of county FIPS codes for the requested state by calling
extract_county_boundaries() from census_counties.py, then loops over
that list, issuing one NSI API request per county, and concatenating
all returned structure records into one combined output. Running this
script requires census_counties.py to be importable and working, 
since deriving a valid county list is a runtime prerequisite.

SPECIAL CONSIDERATIONS:
- NSI attribute values such as valstruct (structure value) and valcont
  (contents value) are MODELED ESTIMATES produced by USACE, not
  county-assessed values or real market valuations.
- Coverage and attribute completeness can be lower in rural or heavily
  wooded areas, where source imagery/data used to build NSI may be
  sparser.
- The FEMA flood-zone attribute (firmzone), where present, is checked
  for its null rate after extraction, and that percentage is printed.
- Records missing longitude/latitude are dropped before building the
  output GeoDataFrame (structures.dropna(subset=["longitude",
  "latitude"])).
- If the NSI API returns zero structure records across every county in
  the state, this raises a flag.
- A short delay (time.sleep(0.25)) is added between each county's
  request to avoid querying the NSI API too rapidly.
- No API key or authentication is required.
- force=True only rebuilds this script's own NSI output; the
  underlying county-boundary file from census_counties.py is reused
  from its own cache.

OUTPUT: data/raw/nsi_structures_{STATE_ABBR}.parquet -- GeoParquet,
EPSG:4326, one row per structure point, with NSI's original attribute
fields (valstruct, valcont, sqft, found_type, num_story, firmzone,
ground_elv, etc.) plus county_fips, county_name, longitude, and
latitude.

SINGLE ENTRY POINT: extract_nsi_structures() is the only function
meant to be called from outside this module.

USAGE:
Interactive:
    from usace_nsi_structures import extract_nsi_structures
    nsi_gdf = extract_nsi_structures()

Headless CLI:
    Default:
        python src/usace_nsi_structures.py
    Specify state:
        python src/usace_nsi_structures.py --state-fips 25 --state-abbr MA
"""

from __future__ import annotations

import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from census_counties import extract_county_boundaries
from config import DEFAULT_STATE_ABBR, DEFAULT_STATE_FIPS, RAW_DIR

HEADERS = {"User-Agent": "usace-nsi-structure-extractor/1.0 (research use)"}


def _get(url: str, *, timeout: int = 120, attempts: int = 3, **kwargs) -> requests.Response:
    """Request the NSI API with retries."""
    error = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            error = exc
            if attempt < attempts - 1:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"NSI request failed: {url}. Last error: {error}")


def extract_nsi_structures(
    state_fips: str | int = DEFAULT_STATE_FIPS,
    state_abbr: str = DEFAULT_STATE_ABBR,
    output_dir: str | Path = RAW_DIR,
    *,
    tiger_year: int = 2023,
    force: bool = False,
) -> gpd.GeoDataFrame:
    """Return NSI structure records for all counties in a selected state.

    Parameters
    ----------
    state_fips
        Two-digit state FIPS code, such as ``44`` for Rhode Island.
    state_abbr
        Two-letter postal abbreviation, used in the cached filename.
    output_dir
        Folder for the output and the county-boundary dependency output.
    tiger_year
        TIGER/Line vintage supplied to ``extract_county_boundaries``.
    force
        If True, refresh an existing NSI output. The county boundary file will
        still use its cached version unless it is removed separately.

    Notes
    -----
    NSI values such as ``valstruct`` and ``valcont`` are modeled estimates,
    not assessed values or market valuations. Coverage and attribute quality
    can be less complete in rural or heavily wooded areas.
    """
    state_abbr = state_abbr.upper()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outpath = output_dir / f"nsi_structures_{state_abbr}.parquet"

    if outpath.exists() and not force:
        return gpd.read_parquet(outpath)

    counties = extract_county_boundaries(
        state_fips,
        state_abbr,
        output_dir,
        tiger_year=tiger_year,
    ).copy()

    required = {"STATEFP", "COUNTYFP"}
    missing = required.difference(counties.columns)
    if missing:
        raise KeyError(f"County boundary data are missing expected fields: {sorted(missing)}")

    counties["county_fips"] = counties["STATEFP"] + counties["COUNTYFP"]
    county_name_field = "NAME" if "NAME" in counties.columns else None
    county_names = {
        row["county_fips"]: row[county_name_field] if county_name_field else row["county_fips"]
        for _, row in counties.iterrows()
    }

    records: list[dict] = []
    api_url = "https://nsi.sec.usace.army.mil/nsiapi/structures"

    for county_fips in sorted(counties["county_fips"].unique()):
        county_name = county_names[county_fips]
        print(f"Querying NSI: {county_name} ({county_fips})")

        response = _get(
            api_url,
            params={"fips": county_fips, "fmt": "fc"},
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"NSI returned non-JSON for {county_name} ({county_fips}): "
                f"{response.text[:400]}"
            ) from exc

        for feature in payload.get("features", []):
            properties = feature.get("properties", {}).copy()
            coordinates = feature.get("geometry", {}).get("coordinates", [None, None])
            properties["longitude"] = coordinates[0] if len(coordinates) > 0 else None
            properties["latitude"] = coordinates[1] if len(coordinates) > 1 else None
            properties["county_fips"] = county_fips
            properties["county_name"] = county_name
            records.append(properties)

        time.sleep(0.25)

    if not records:
        raise RuntimeError(
            f"The NSI API returned no structure records for {state_abbr}."
        )

    structures = pd.DataFrame(records)
    structures = structures.dropna(subset=["longitude", "latitude"]).copy()
    output = gpd.GeoDataFrame(
        structures,
        geometry=gpd.points_from_xy(structures["longitude"], structures["latitude"]),
        crs="EPSG:4326",
    )

    if "firmzone" in output.columns:
        null_rate = output["firmzone"].isna().mean() * 100
        print(f"firmzone null rate: {null_rate:.1f}%")

    output.to_parquet(outpath, index=False)
    return output


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract USACE National Structure Inventory records for one state."
    )
    parser.add_argument("--state-fips", default=DEFAULT_STATE_FIPS)
    parser.add_argument("--state-abbr", default=DEFAULT_STATE_ABBR)
    parser.add_argument("--output-dir", default=str(RAW_DIR))
    parser.add_argument("--tiger-year", type=int, default=2023)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = extract_nsi_structures(
        state_fips=args.state_fips,
        state_abbr=args.state_abbr,
        output_dir=args.output_dir,
        tiger_year=args.tiger_year,
        force=args.force,
    )
    print(f"Saved {len(result):,} NSI structure records.")
