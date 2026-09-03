"""Extract USGS Watershed Boundary Dataset 12-digit hydrologic units by state.

The extractor queries the USGS National Map WBD ArcGIS REST service's
12-digit HU (Subwatershed) layer. It selects records by the service's
``States`` attribute and retains each original, full subwatershed geometry.
No clipping is performed, so subwatersheds that cross the selected state
boundary remain complete polygons.
"""
from __future__ import annotations

from pathlib import Path
import time

import geopandas as gpd
import requests

from config import DEFAULT_STATE_ABBR, RAW_DIR

SERVICE_URL = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/6/query"
HEADERS = {"User-Agent": "usgs-wbd-huc12-extractor/1.0 (research use)"}


def _request(params: dict, *, timeout: int = 120, attempts: int = 3) -> dict:
    """Submit an ArcGIS REST query with retries and return JSON."""
    error = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(SERVICE_URL, params=params, headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if "error" in payload:
                raise RuntimeError(payload["error"])
            return payload
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            error = exc
            if attempt < attempts:
                time.sleep(3 * attempt)
    raise RuntimeError(f"WBD service request failed after {attempts} attempts: {error}")


def extract_wbd_huc12_subwatersheds(
    state_abbr: str = DEFAULT_STATE_ABBR,
    output_dir: str | Path = RAW_DIR,
    *,
    batch_size: int = 1000,
    force: bool = False,
) -> gpd.GeoDataFrame:
    """Download all WBD HUC12 subwatersheds whose ``States`` field includes a state.

    Parameters
    ----------
    state_abbr
        Two-letter state or territory abbreviation, for example ``RI`` or
        ``MA``. The service's ``States`` field can contain more than one value
        (for example, ``MA,RI``); this function selects either a single-state
        value or a comma-delimited value that contains the requested state.
    output_dir
        Folder where the output GeoParquet file will be cached.
    batch_size
        Number of records requested per ArcGIS REST page. The service may
        impose its own maximum, so the function also follows returned offsets.
    force
        If True, download again even when a cached output exists.

    Returns
    -------
    geopandas.GeoDataFrame
        Full, un-clipped HUC12 polygons in EPSG:4326.
    """
    state_abbr = state_abbr.strip().upper()
    if len(state_abbr) != 2 or not state_abbr.isalpha():
        raise ValueError("state_abbr must be a two-letter abbreviation, such as 'RI'.")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outpath = output_dir / f"usgs_wbd_huc12_subwatersheds_{state_abbr}.parquet"

    if outpath.exists() and not force:
        return gpd.read_parquet(outpath)

    # This matches the whole value or the abbreviation at either end of a
    # comma-separated States list, such as RI, MA,RI, or RI,CT.
    where = (
        f"States = '{state_abbr}' OR "
        f"States LIKE '{state_abbr},%' OR "
        f"States LIKE '%,{state_abbr}' OR "
        f"States LIKE '%,{state_abbr},%'"
    )

    features: list[dict] = []
    offset = 0

    while True:
        print(f"Requesting HUC12 features starting at record {offset:,}...")
        params = {
            "where": where,
            "outFields": "*",
            "returnGeometry": "true",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": batch_size,
            "outSR": 4326,
        }
        payload = _request(params)
        batch = payload.get("features", [])
        features.extend(batch)

        if len(batch) < batch_size:
            break
        offset += len(batch)
        time.sleep(0.2)

    if not features:
        raise RuntimeError(
            f"The WBD service returned no HUC12 subwatersheds for state '{state_abbr}'."
        )

    result = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    result = result.to_crs("EPSG:4326")
    result.to_parquet(outpath, index=False)
    print(f"Saved {len(result):,} full HUC12 subwatershed polygons to {outpath}.")
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract full USGS WBD HUC12 subwatersheds that include a selected state."
    )
    parser.add_argument("--state-abbr", default=DEFAULT_STATE_ABBR)
    parser.add_argument("--output-dir", default=str(RAW_DIR))
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    extract_wbd_huc12_subwatersheds(
    state_abbr=args.state_abbr,
    output_dir=args.output_dir,
    batch_size=args.batch_size,
    force=args.force,
)