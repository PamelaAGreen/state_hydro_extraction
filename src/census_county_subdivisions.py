"""Extract Census TIGER/Line county-subdivision boundaries for one U.S. state."""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import geopandas as gpd
import requests

import shutil

from config import (
    DEFAULT_STATE_ABBR,
    DEFAULT_STATE_FIPS,
    RAW_DIR,
)

HEADERS = {"User-Agent": "state-county-subdivision-extractor/1.0 (research use)"}


def _download(url: str, *, timeout: int = 90, attempts: int = 3) -> requests.Response:
    """Download a Census TIGER/Line archive with retries."""
    error = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            error = exc
            if attempt < attempts - 1:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Could not download {url}. Last error: {error}")


def extract_county_subdivisions(
    state_fips: str | int = DEFAULT_STATE_FIPS,
    state_abbr: str = DEFAULT_STATE_ABBR,
    output_dir: str | Path = RAW_DIR,
    *,
    tiger_year: int = 2023,
    force: bool = False,
) -> gpd.GeoDataFrame:
    """Return TIGER/Line county-subdivision polygons for one state.

    County subdivisions are Census geographic units. In some states they align
    closely with towns or townships; in others they do not represent local
    governments, so interpret the layer according to the selected state's
    Census geography.

    Parameters
    ----------
    state_fips
        Two-digit state FIPS code, such as ``44`` for Rhode Island.
    state_abbr
        Two-letter postal abbreviation used in the output filename.
    output_dir
        Folder where the GeoParquet result will be saved.
    tiger_year
        TIGER/Line vintage to download.
    force
        If True, rebuild an existing cached output.
    """
    state_fips = str(state_fips).zfill(2)
    state_abbr = state_abbr.upper()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outpath = output_dir / f"county_subdivisions_{state_abbr}.parquet"

    if outpath.exists() and not force:
        return gpd.read_parquet(outpath)

    url = (
        f"https://www2.census.gov/geo/tiger/TIGER{tiger_year}/COUSUB/"
        f"tl_{tiger_year}_{state_fips}_cousub.zip"
    )
    response = _download(url)

    temp_dir = output_dir / f"_tmp_cousub_{state_fips}_{tiger_year}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(temp_dir)

    shapefiles = list(temp_dir.glob("*.shp"))
    if not shapefiles:
        raise RuntimeError("The Census ZIP did not contain a county-subdivision shapefile.")

    try:
        subdivisions = gpd.read_file(shapefiles[0]).to_crs("EPSG:4326")

        if subdivisions.empty:
            raise ValueError(
                f"No county subdivisions were found for state FIPS {state_fips}."
            )

        subdivisions.to_parquet(outpath, index=False)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return subdivisions

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract Census TIGER/Line county subdivisions for one state."
    )
    parser.add_argument("--state-fips", default=DEFAULT_STATE_FIPS)
    parser.add_argument("--state-abbr", default=DEFAULT_STATE_ABBR)
    parser.add_argument("--output-dir", default=str(RAW_DIR))
    parser.add_argument("--tiger-year", type=int, default=2023)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = extract_county_subdivisions(
        state_fips=args.state_fips,
        state_abbr=args.state_abbr,
        output_dir=args.output_dir,
        tiger_year=args.tiger_year,
        force=args.force,
    )

    print(f"Saved {len(result):,} county subdivisions.")
