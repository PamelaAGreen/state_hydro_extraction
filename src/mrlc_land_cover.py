"""Download and clip Annual NLCD land cover to a selected state."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import geopandas as gpd
import rasterio
from rasterio.mask import mask
import requests

from config import (
    DEFAULT_LAND_COVER_YEAR,
    DEFAULT_STATE_ABBR,
    DEFAULT_STATE_FIPS,
    RAW_DIR,
)

HEADERS = {
    "User-Agent": "mrlc-land-cover-extractor/1.0 (research use)"
}


def _download(url: str, destination: Path, *, timeout: int = 600) -> None:
    """Download a file in chunks, writing to a temporary file first."""
    temporary_path = destination.with_suffix(destination.suffix + ".part")

    try:
        with requests.get(
            url,
            headers=HEADERS,
            stream=True,
            timeout=timeout,
        ) as response:
            response.raise_for_status()

            with temporary_path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)

        temporary_path.replace(destination)

    except requests.RequestException as exc:
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(f"MRLC download failed: {exc}") from exc


def _get_state_geometry(
    state_fips: str | int,
    tiger_year: int,
) -> gpd.GeoDataFrame:
    """Download TIGER/Line state boundaries and select one state."""
    state_fips = str(state_fips).zfill(2)

    tiger_url = (
        f"https://www2.census.gov/geo/tiger/TIGER{tiger_year}/STATE/"
        f"tl_{tiger_year}_us_state.zip"
    )

    states = gpd.read_file(tiger_url)

    state = states.loc[states["STATEFP"] == state_fips].copy()

    if state.empty:
        raise ValueError(
            f"State FIPS {state_fips} was not found in TIGER{tiger_year}."
        )

    return state


def extract_mrlc_land_cover(
    state_fips: str | int = DEFAULT_STATE_FIPS,
    state_abbr: str = DEFAULT_STATE_ABBR,
    year: int = DEFAULT_LAND_COVER_YEAR,
    output_dir: str | Path = RAW_DIR,
    *,
    tiger_year: int = 2023,
    force: bool = False,
    keep_source: bool = False,
) -> Path:
    """Download Annual NLCD land cover and clip it to a state boundary."""
    state_fips = str(state_fips).zfill(2)
    state_abbr = state_abbr.upper()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"mrlc_land_cover_{state_abbr}_{year}.tif"

    if output_path.exists() and not force:
        print(f"Using cached clipped raster: {output_path}")
        return output_path

    source_dir = output_dir / "mrlc_source"
    source_dir.mkdir(parents=True, exist_ok=True)

    archive_name = f"Annual_NLCD_LndCov_{year}_CU_C1V2.zip"
    archive_path = source_dir / archive_name

    download_url = (
        "https://www.mrlc.gov/downloads/sciweb1/shared/mrlc/data-bundles/"
        f"{archive_name}"
    )

    if not archive_path.exists():
        print(f"Downloading official MRLC Annual NLCD archive for {year}...")
        _download(download_url, archive_path)
    else:
        print(f"Using cached MRLC archive: {archive_path}")

    source_tif_name = f"Annual_NLCD_LndCov_{year}_CU_C1V2.tif"
    source_tif_path = source_dir / source_tif_name

    if not source_tif_path.exists():
        print("Extracting the Annual NLCD GeoTIFF...")
        with zipfile.ZipFile(archive_path) as archive:
            tif_members = [
                name
                for name in archive.namelist()
                if name.lower().endswith(".tif")
            ]

            if len(tif_members) != 1:
                raise RuntimeError(
                    f"Expected one GeoTIFF in {archive_name}; found {tif_members}"
                )

            with archive.open(tif_members[0]) as source, source_tif_path.open(
                "wb"
            ) as destination:
                shutil.copyfileobj(source, destination)

    state = _get_state_geometry(state_fips, tiger_year)

    with rasterio.open(source_tif_path) as source:
        state_in_raster_crs = state.to_crs(source.crs)

        clipped_data, clipped_transform = mask(
            source,
            state_in_raster_crs.geometry,
            crop=True,
            nodata=source.nodata,
        )

        output_metadata = source.meta.copy()
        output_metadata.update(
            {
                "driver": "GTiff",
                "height": clipped_data.shape[1],
                "width": clipped_data.shape[2],
                "transform": clipped_transform,
            }
        )

        with rasterio.open(output_path, "w", **output_metadata) as destination:
            destination.write(clipped_data)

    print(f"Saved clipped {year} land-cover raster: {output_path}")

    if not keep_source:
        archive_path.unlink(missing_ok=True)
        source_tif_path.unlink(missing_ok=True)

        try:
            source_dir.rmdir()
        except OSError:
            pass

        print("Deleted cached full-CONUS NLCD source files.")
       
    return output_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Download and clip Annual NLCD land cover to a state."
    )
    parser.add_argument(
        "--keep-source",
        action="store_true",
        help="Keep the downloaded full-CONUS NLCD ZIP and GeoTIFF.",
    )

    parser.add_argument("--state-fips", default=DEFAULT_STATE_FIPS)
    parser.add_argument("--state-abbr", default=DEFAULT_STATE_ABBR)
    parser.add_argument("--year", type=int, default=DEFAULT_LAND_COVER_YEAR)
    parser.add_argument("--output-dir", default=str(RAW_DIR))
    parser.add_argument("--tiger-year", type=int, default=2023)
    parser.add_argument("--force", action="store_true")

    args = parser.parse_args()

    extract_mrlc_land_cover(
        state_fips=args.state_fips,
        state_abbr=args.state_abbr,
        year=args.year,
        output_dir=args.output_dir,
        tiger_year=args.tiger_year,
        force=args.force,
        keep_source=args.keep_source,
    )