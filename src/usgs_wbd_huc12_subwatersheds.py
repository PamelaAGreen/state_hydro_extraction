"""
src/usgs_wbd_huc12_subwatersheds.py
=====================================
Extracts USGS Watershed Boundary Dataset (WBD) 12-digit hydrologic
units (HUC12 subwatersheds) that include one U.S. state, keeping each
subwatershed's full, un-clipped geometry.

DATA SOURCE: USGS National Map WBD ArcGIS REST service, 12-digit HU
(Subwatershed) layer.
- Service documentation / map service root:
  https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer
- Specific query endpoint used by this script (layer 6, the HUC12
  Subwatershed layer):
  https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/6/query

FORMAT: This is a paginated ArcGIS REST query. Records are selected using 
the service's own States attribute via a SQL-style WHERE clause, and returned 
as GeoJSON features. Because a subwatershed's States field can hold more than 
one state (e.g. "MA,RI" for a HUC12 straddling both), the WHERE clause
matches four patterns: an exact single-state match, or the requested
abbreviation appearing at the start, middle, or end of a comma-
separated list. This script requests results in batches
(resultRecordCount, default 1,000) and keeps requesting subsequent
pages (advancing resultOffset) until a page returns fewer features than
the batch size.

SPECIAL CONSIDERATIONS:
- No clipping is performed. A HUC12 that spans this state and a
  neighboring state is returned as its complete, original polygon --
  including the portion outside the requested state.
- state_abbr is validated strictly: it must be exactly two alphabetic
  characters after stripping and upper-casing, or a ValueError flag is
  raised.
- If the WBD service returns zero features for the requested state
  abbreviation, this raises a flag.
- The service's own maximum page size may be lower than the requested
  batch_size; this script relies on comparing the number of features
  actually returned per page against batch_size to decide whether more
  pages remain.
- A short delay (time.sleep(0.2)) is added between pages to avoid
  querying the ArcGIS REST service too rapidly.
- No API key or authentication is required.

OUTPUT: data/raw/usgs_wbd_huc12_subwatersheds_{STATE_ABBR}.parquet --
GeoParquet, EPSG:4326, one row per full HUC12 subwatershed polygon that
includes the requested state, with the WBD service's original
attribute fields preserved.

SINGLE ENTRY POINT: extract_wbd_huc12_subwatersheds() is the only
function meant to be called from outside this module.

USAGE:
Interactive:
    from usgs_wbd_huc12_subwatersheds import extract_wbd_huc12_subwatersheds
    huc12_gdf = extract_wbd_huc12_subwatersheds()

Headless CLI:
    Default:
        python src/usgs_wbd_huc12_subwatersheds.py
    Specify state:
        python src/usgs_wbd_huc12_subwatersheds.py --state-abbr MA
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