"""
src/usgs_stream_gauges.py
==========================
Extracts USGS stream-gauge sites for one state that have a discharge or
gauge-height measurement history.

DATA SOURCE: USGS National Water Information System (NWIS), accessed via
the dataretrieval Python package (not a direct file download or REST
call written by this script).
- Package documentation:
  https://doi-usgs.github.io/dataretrieval-python/
- Underlying NWIS site-service documentation:
  https://waterservices.usgs.gov/

FORMAT: This script does not construct its own download URLs. It calls
dataretrieval.nwis.get_info() twice:
1. Once with siteType="ST" to get every stream site in the state,
   regardless of what it measures or how long its record is.
2. Once more with siteType="ST" and seriesCatalogOutput=True to get each
   site's full parameter-and-date-range catalog, filtered to two
   parameter codes: 00060 (discharge) and 00065 (gauge height).
   Per-site begin_date/end_date are aggregated from that filtered
   catalog and merged back onto the full site list. Sites with no
   matching begin_date are dropped.

SPECIAL CONSIDERATIONS:
- Requires the dataretrieval package. If missing, this raises an
  ImportError with an explicit "pip install dataretrieval" instruction.
- is_active_now is a derived heuristic (end_date within the last 90
  days of today).
- 00060 and 00065 are USGS parameter codes for discharge (cfs) and
  gauge height (feet); other parameter codes are ignored.
- Point geometry is built directly from dec_long_va/dec_lat_va; no
  reprojection is applied beyond declaring CRS EPSG:4326.
- No API key or authentication is required.

OUTPUT: data/raw/usgs_stream_gauges_{STATE_ABBR}.parquet -- GeoParquet,
EPSG:4326, one row per stream site with a discharge or gauge-height
history, including site_no, station_nm, site_tp_cd, huc_cd, begin_date,
end_date, is_active_now, and geometry.

SINGLE ENTRY POINT: extract_usgs_stream_gauges() is the only function
meant to be called from outside this module.

USAGE:
Interactive:
    from usgs_stream_gauges import extract_usgs_stream_gauges
    gauges_gdf = extract_usgs_stream_gauges()

Headless CLI:
    Default:
        python src/usgs_stream_gauges.py
    Specify state:
        python src/usgs_stream_gauges.py --state-abbr MA
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from config import DEFAULT_STATE_ABBR, RAW_DIR

def extract_usgs_stream_gauges(
    state_abbr: str = DEFAULT_STATE_ABBR,
    output_dir: str | Path = RAW_DIR,
    *,
    force: bool = False,
) -> gpd.GeoDataFrame:
    """Return stream sites with discharge (00060) or gauge-height (00065) history.

    Parameters
    ----------
    state_abbr
        Two-letter postal abbreviation, for example ``"RI"`` or ``"NY"``.
    output_dir
        Folder where the GeoParquet output will be cached.
    force
        If True, re-download data even when a cached output exists.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outpath = output_dir / f"usgs_stream_gauges_{state_abbr.upper()}.parquet"

    if outpath.exists() and not force:
        return gpd.read_parquet(outpath)

    try:
        import dataretrieval.nwis as nwis
    except ImportError as exc:
        raise ImportError("Install the required package with: pip install dataretrieval") from exc

    state_abbr = state_abbr.upper()

    # First retrieve every stream site, independent of its measurement history.
    stream_sites, _ = nwis.get_info(stateCd=state_abbr, siteType="ST")
    stream_sites = stream_sites.drop_duplicates(subset="site_no").copy()

    # Then retrieve parameter records and retain discharge or gauge-height history.
    catalog, _ = nwis.get_info(
        stateCd=state_abbr,
        siteType="ST",
        seriesCatalogOutput=True,
    )
    history = catalog[catalog["parm_cd"].isin(["00060", "00065"])].copy()
    dates = (
        history.groupby("site_no")
        .agg(begin_date=("begin_date", "min"), end_date=("end_date", "max"))
        .reset_index()
    )

    sites = stream_sites.merge(dates, on="site_no", how="left")
    sites = sites[sites["begin_date"].notna()].copy()

    keep = [
        column
        for column in [
            "site_no",
            "station_nm",
            "dec_lat_va",
            "dec_long_va",
            "site_tp_cd",
            "huc_cd",
            "begin_date",
            "end_date",
        ]
        if column in sites.columns
    ]
    sites = sites[keep].copy()
    sites["begin_date"] = pd.to_datetime(sites["begin_date"], errors="coerce")
    sites["end_date"] = pd.to_datetime(sites["end_date"], errors="coerce")
    sites["is_active_now"] = (
        sites["end_date"] >= pd.Timestamp.today().normalize() - pd.Timedelta(days=90)
    )

    gauges = gpd.GeoDataFrame(
        sites,
        geometry=gpd.points_from_xy(sites["dec_long_va"], sites["dec_lat_va"]),
        crs="EPSG:4326",
    )
    gauges.to_parquet(outpath, index=False)
    return gauges


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract USGS stream gauges with discharge or gauge-height record history."
    )
    parser.add_argument("--state-abbr", default=DEFAULT_STATE_ABBR, help="Two-letter state abbreviation, e.g. RI")
    parser.add_argument("--output-dir", default="data/raw")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = extract_usgs_stream_gauges(
        args.state_abbr,
        args.output_dir,
        force=args.force,
    )
    print(f"Saved {len(result):,} stream gauges.")
