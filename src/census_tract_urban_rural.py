"""Extract 2020 Census DHC H2 tract urban/rural housing-unit shares."""
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
