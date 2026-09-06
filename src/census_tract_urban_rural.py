"""
src/census_tract_urban_rural.py
================================
Extracts Census tract boundaries for one U.S. state and joins them to
2020 Census Demographic and Housing Characteristics (DHC) urban/rural
housing-unit counts, producing a per-tract percent-urban and
percent-rural share.

DATA SOURCES:
1. Census Bureau TIGER/Line Shapefiles, TRACT layer (geometry only):
   - Landing page:
     https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html
   - Direct download pattern (one ZIP per state, per TIGER/Line vintage
     year):
     https://www2.census.gov/geo/tiger/TIGER{year}/TRACT/tl_{year}_{state_fips}_tract.zip
     e.g. https://www2.census.gov/geo/tiger/TIGER2023/TRACT/tl_2023_44_tract.zip
2. Census Bureau 2020 Decennial Census, Demographic and Housing
   Characteristics File (DHC), table H2 (attribute data, via API):
   - API documentation / variable list:
     https://www.census.gov/data/developers/data-sets/decennial-census.html
   - Base endpoint used by this script:
     https://api.census.gov/data/2020/dec/dhc
   - Variables pulled: H2_001N (total housing units), H2_002N (housing
     units in urban areas), H2_003N (housing units in rural areas).

FORMAT: Geometry and attributes come from two different sources and are
joined locally by this script -- they are not available pre-joined
anywhere. Tract geometry is one ZIP per state (see URL pattern above).
Attribute data comes from the Census API, which does NOT support
requesting all of a state's tracts in a single call; it requires an
explicit county parameter per request. This script derives the list of
counties present in the state's own tract shapefile (via the COUNTYFP
column) and then loops over that list, issuing one API call per county,
concatenating the results, and joining them back to the tract geometry
on GEOID. This "get geometry first, then loop counties for attributes"
pattern is what makes the extractor work for any state without the
county list needing to be hardcoded or supplied separately.

SPECIAL CONSIDERATIONS:
- Requires a Census API key. Pass one explicitly via census_api_key=, or
  set it as the CENSUS_API_KEY environment variable (e.g. in a .env
  file loaded before this module runs). Request a free key at
  https://api.census.gov/data/key_signup.html if one is not already
  available. Without a key, the function raises immediately rather than
  attempting an unauthenticated request.
- state_fips is coerced with str(state_fips).zfill(2), so passing either
  44 (int) or "44" (str) produces the correct two-digit code used in
  both the TIGER/Line URL and the Census API "in=" clause.
- The API is queried once per county (not once per state), with a short
  delay between calls (time.sleep(0.25)) to avoid hammering the
  endpoint; a state with many counties will make that many sequential
  API requests.
- H2_001N/H2_002N/H2_003N are coerced to numeric with errors="coerce",
  so any unexpected non-numeric API response for a tract becomes NaN
  rather than raising.
- pct_urban and pct_rural divide by total_units with 0 replaced by
  pd.NA first, avoiding a division-by-zero result for tracts with no
  housing units.
- Source tract geometry is NAD83 (EPSG:4269, TIGER/Line's native CRS);
  this script reprojects to EPSG:4326 before saving.
- The temporary per-run extraction folder (data/raw/_tmp_tracts_
  {state_fips}_{tiger_year}/) is removed automatically after each run,
  including when an error occurs, so no manual cleanup is needed.

OUTPUT: data/raw/census_tract_urban_rural_{STATE_ABBR}.parquet --
GeoParquet, EPSG:4326, one row per Census tract, with geoid, county_fips,
total_units, urban_units, rural_units, pct_urban, pct_rural, and
geometry.

SINGLE ENTRY POINT: extract_tract_urban_rural() is the only function
meant to be called from outside this module.

USAGE:
Interactive:
    from census_tract_urban_rural import extract_tract_urban_rural
    tracts_gdf = extract_tract_urban_rural()

Headless CLI:
    Default:
        python src/census_tract_urban_rural.py
    Specify state:
        python src/census_tract_urban_rural.py --state-fips 25 --state-abbr MA
"""

from __future__ import annotations

import io
import os
import time
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

import shutil

from config import (
    DEFAULT_STATE_ABBR,
    DEFAULT_STATE_FIPS,
    RAW_DIR,
)

HEADERS = {"User-Agent": "census-tract-urban-rural-extractor/1.0 (research use)"}


def _get(url: str, *, timeout: int = 90, attempts: int = 3, **kwargs) -> requests.Response:
    """Request a URL with retries and an identifying User-Agent."""
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
    raise RuntimeError(f"Request failed: {url}. Last error: {error}")


def extract_tract_urban_rural(
    state_fips: str | int = DEFAULT_STATE_FIPS,
    state_abbr: str = DEFAULT_STATE_ABBR,
    output_dir: str | Path = RAW_DIR,
    *,
    census_api_key: str | None = None,
    tiger_year: int = 2023,
    force: bool = False,
) -> gpd.GeoDataFrame:
    """Return tracts joined to 2020 DHC H2 urban/rural housing-unit values.

    H2 variables used:
    - ``H2_001N``: Total housing units.
    - ``H2_002N``: Housing units in urban areas.
    - ``H2_003N``: Housing units in rural areas.

    The Census API requires one explicit county per tract-level request. This
    function derives the county list from the state tract file and loops over
    it, so it can be used for any state.
    """
    state_fips = str(state_fips).zfill(2)
    state_abbr = state_abbr.upper()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outpath = output_dir / f"census_tract_urban_rural_{state_abbr}.parquet"

    if outpath.exists() and not force:
        return gpd.read_parquet(outpath)

    api_key = census_api_key or os.getenv("CENSUS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Provide census_api_key= or set the CENSUS_API_KEY environment variable."
        )

    tract_url = (
        f"https://www2.census.gov/geo/tiger/TIGER{tiger_year}/TRACT/"
        f"tl_{tiger_year}_{state_fips}_tract.zip"
    )
    response = _get(tract_url)

    temp_dir = output_dir / f"_tmp_tracts_{state_fips}_{tiger_year}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(temp_dir)

    shapefiles = list(temp_dir.glob("*.shp"))
    if not shapefiles:
        raise RuntimeError("The Census ZIP did not contain a tract shapefile.")

    tracts = gpd.read_file(shapefiles[0]).to_crs("EPSG:4326")
    tracts = tracts.rename(columns={"GEOID": "geoid", "COUNTYFP": "countyfp"})
    tracts = tracts[["geoid", "countyfp", "geometry"]].copy()

    frames: list[pd.DataFrame] = []
    api_url = "https://api.census.gov/data/2020/dec/dhc"

    for county_fips in sorted(tracts["countyfp"].unique()):
        params = {
            "get": "H2_001N,H2_002N,H2_003N",
            "for": "tract:*",
            "in": f"state:{state_fips} county:{county_fips}",
            "key": api_key,
        }
        response = _get(api_url, params=params)
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Census API returned non-JSON for county {county_fips}: "
                f"{response.text[:400]}"
            ) from exc

        if len(payload) < 2:
            raise RuntimeError(
                f"Census API returned no tract data for county {county_fips}."
            )
        frames.append(pd.DataFrame(payload[1:], columns=payload[0]))
        time.sleep(0.25)

    values = pd.concat(frames, ignore_index=True)
    values["geoid"] = values["state"] + values["county"] + values["tract"]

    for field in ["H2_001N", "H2_002N", "H2_003N"]:
        values[field] = pd.to_numeric(values[field], errors="coerce")

    values = values.rename(
        columns={
            "H2_001N": "total_units",
            "H2_002N": "urban_units",
            "H2_003N": "rural_units",
        }
    )[["geoid", "total_units", "urban_units", "rural_units"]]

    result = tracts.merge(values, on="geoid", how="left")
    result["pct_urban"] = result["urban_units"] / result["total_units"].replace(0, pd.NA)
    result["pct_rural"] = result["rural_units"] / result["total_units"].replace(0, pd.NA)
    result["county_fips"] = result["geoid"].str[:5]

    output = gpd.GeoDataFrame(result, geometry="geometry", crs="EPSG:4326")

    try:
        output.to_parquet(outpath, index=False)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return output

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract 2020 DHC H2 tract urban/rural housing-unit shares."
    )
    parser.add_argument("--state-fips", default=DEFAULT_STATE_FIPS)
    parser.add_argument("--state-abbr", default=DEFAULT_STATE_ABBR)
    parser.add_argument("--output-dir", default=str(RAW_DIR))
    parser.add_argument("--tiger-year", type=int, default=2023)
    parser.add_argument("--census-api-key", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output = extract_tract_urban_rural(
        state_fips=args.state_fips,
        state_abbr=args.state_abbr,
        output_dir=args.output_dir,
        census_api_key=args.census_api_key,
        tiger_year=args.tiger_year,
        force=args.force,
    )

    print(f"Saved {len(output):,} Census tract records.")
