"""
src/census_counties.py
=======================
Extracts Census TIGER/Line county boundary polygons for one U.S. state.

DATA SOURCE: Census Bureau TIGER/Line Shapefiles, COUNTY layer.
- Landing page / documentation:
  https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html
- Direct download pattern used by this script (one national ZIP per
  TIGER/Line vintage year, not split by state):
  https://www2.census.gov/geo/tiger/TIGER{year}/COUNTY/tl_{year}_us_county.zip
  e.g. https://www2.census.gov/geo/tiger/TIGER2023/COUNTY/tl_2023_us_county.zip

FORMAT: The ZIP contains a single national county shapefile (.shp/.shx/
.dbf/.prj) covering all 50 states, DC, and territories -- there is no
per-state download option at the source. This script downloads the full
national ZIP, extracts it to a temporary folder, loads the shapefile with
geopandas, and THEN filters rows down to the selected state using the
two-digit STATEFP field (e.g. "44" for Rhode Island). No county-by-county
fetching or stitching is required for this particular dataset, since the
source file already contains every county nationwide in one shapefile.
The temporary extracted shapefile is not deleted automatically; it
accumulates under data/raw/_tmp_counties_{tiger_year}/ across repeated
runs using the same tiger_year, which is harmless but can be cleaned up
manually if disk space matters.

SPECIAL CONSIDERATIONS:
- STATEFP is a two-digit, zero-padded string (e.g. "06" for California,
  not "6"). state_fips is coerced with str(state_fips).zfill(2) before
  filtering, so passing either 44 (int) or "44" (str) works correctly.
- Source geometry is NAD83 (EPSG:4269, TIGER/Line's native CRS); this
  script reprojects to EPSG:4326 before saving.
- A combined 5-digit county FIPS code is NOT provided as a single field
  by TIGER/Line -- if one is needed downstream, it must be built by
  concatenating this file's STATEFP + COUNTYFP columns.
- No API key or authentication is required; this is a direct,
  unauthenticated file download from the Census Bureau's public
  FTP-style mirror.

OUTPUT: data/raw/counties_{STATE_ABBR}.parquet -- GeoParquet, EPSG:4326,
one row per county polygon in the selected state, with the full set of
original TIGER/Line attribute columns (STATEFP, COUNTYFP, NAME, GEOID,
ALAND, AWATER, geometry, etc.) preserved.

SINGLE ENTRY POINT: extract_county_boundaries() is the only function
meant to be called from outside this module.

USAGE:
Interactive:
    from census_counties import extract_county_boundaries
    counties_gdf = extract_county_boundaries()

Headless CLI:
    Default:
        python src/census_counties.py
    Specify state:
        python src/census_counties.py --state-fips 25 --state-abbr MA
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import geopandas as gpd
import requests

from config import DEFAULT_STATE_ABBR, DEFAULT_STATE_FIPS, RAW_DIR

HEADERS = {"User-Agent": "state-county-boundary-extractor/1.0 (research use)"}


def _download(url: str, *, timeout: int = 90, attempts: int = 3) -> requests.Response:
    """Download a file with retries and an identifying User-Agent."""
    error = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            error = exc
            if attempt < attempts - 1:
                import time
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Could not download {url}. Last error: {error}")


def extract_county_boundaries(
    state_fips: str | int = DEFAULT_STATE_FIPS,
    state_abbr: str = DEFAULT_STATE_ABBR,
    output_dir: str | Path = RAW_DIR,
    *,
    tiger_year: int = 2023,
    force: bool = False,
) -> gpd.GeoDataFrame:
    """Return TIGER/Line county polygons for one state.

    Parameters
    ----------
    state_fips
        Two-digit state FIPS code, for example ``44`` for Rhode Island.
    state_abbr
        Two-letter postal abbreviation used in the cached output filename.
    output_dir
        Folder where the GeoParquet result will be saved.
    tiger_year
        TIGER/Line vintage to download.
    force
        If True, download and rebuild the output even if it already exists.
    """
    state_fips = str(state_fips).zfill(2)
    state_abbr = state_abbr.upper()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outpath = output_dir / f"counties_{state_abbr}.parquet"

    if outpath.exists() and not force:
        return gpd.read_parquet(outpath)

    url = (
        f"https://www2.census.gov/geo/tiger/TIGER{tiger_year}/COUNTY/"
        f"tl_{tiger_year}_us_county.zip"
    )
    response = _download(url)

    temp_dir = output_dir / f"_tmp_counties_{tiger_year}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(temp_dir)

    shapefiles = list(temp_dir.glob("*.shp"))
    if not shapefiles:
        raise RuntimeError("The Census ZIP did not contain a county shapefile.")

    counties = gpd.read_file(shapefiles[0])
    counties = counties[counties["STATEFP"] == state_fips].copy()
    counties = counties.to_crs("EPSG:4326")

    if counties.empty:
        raise ValueError(
            f"No counties matched state FIPS {state_fips}. Check the selected FIPS code."
        )

    counties.to_parquet(outpath, index=False)
    return counties


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract Census TIGER/Line county boundaries for one state."
    )
    parser.add_argument("--state-fips", default=DEFAULT_STATE_FIPS)
    parser.add_argument("--state-abbr", default=DEFAULT_STATE_ABBR)
    parser.add_argument("--output-dir", default=str(RAW_DIR))
    parser.add_argument("--tiger-year", type=int, default=2023)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = extract_county_boundaries(
        state_fips=args.state_fips,
        state_abbr=args.state_abbr,
        output_dir=args.output_dir,
        tiger_year=args.tiger_year,
        force=args.force,
    )
    print(f"Saved {len(result):,} county boundaries.")
