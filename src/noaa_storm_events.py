"""
src/noaa_storm_events.py
=========================
Extracts NOAA Storm Events Database records for flood-related event
types in one state and resolves each event to a five-digit county FIPS
code.

DATA SOURCES:
1. NOAA/NCEI Storm Events Database (event records):
   - Documentation:
     https://www.ncdc.noaa.gov/stormevents/
   - Direct download pattern used by this script (one gzipped CSV per
     calendar year, covering the entire United States, not just one
     state):
     https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/StormEvents_details-ftp_v1.0_d{year}_c{8-digit-date}.csv.gz
   - This script first fetches the directory listing at that base URL
     and searches it with a regular expression to find the current
     filename for each requested year, since the trailing
     c{8-digit-date} portion (a file-creation/version stamp) is not
     predictable in advance and changes as NOAA reprocesses data.
2. NOAA/NWS public forecast zone-to-county correlation file (reference
   table, used only to resolve zone-based events to counties):
   - Landing page:
     https://www.weather.gov/gis/ZoneCounty
   - This script scrapes that page's HTML for links matching the
     pattern bp{2 digits}{2 letters}{2 digits}.dbx, picks the most
     recent one by decoding the embedded date in the filename, and
     downloads it from:
     https://www.weather.gov/source/gis/Shapefiles/County/{filename}.dbx

FORMAT AND SPECIAL CONSIDERATIONS -- ZONE VS. COUNTY RESOLUTION: NOAA
Storm Events records use a CZ_TYPE field that is either "C" (county) or
"Z" (NWS forecast zone), and a CZ_FIPS field whose meaning depends on
CZ_TYPE:
- CZ_TYPE == "C": CZ_FIPS is already a three-digit county code; this
  script pads it to three digits and prepends the two-digit state FIPS
  to build a five-digit county_fips directly (geo_resolution =
  "county_direct").
- CZ_TYPE == "Z": CZ_FIPS is an NWS forecast-zone code.
  This script downloads the NWS zone-to-county correlation file
  described above, joins zone events to it on the zone code, and
  attaches the corresponding county_fips (geo_resolution =
  "zone_to_county_join"). Because a single NWS zone can span multiple
  counties, a zone-based event can expand into more than one output
  row -- one per matched county -- and n_counties_for_zone records how
  many counties that particular zone maps to, so downstream analysis
  can identify and, if desired, weight or de-duplicate these
  one-to-many expansions.

OTHER SPECIAL CONSIDERATIONS:
- This script filters national-scale yearly files down to one state
  using the STATE column (matched against state_name, e.g. "RHODE
  ISLAND") -- state_name must match NOAA's state-name spelling/casing 
  (case-insensitive comparison is applied).
- Only four flood-related event types are retained:
  Flood, Flash Flood, Coastal Flood, and Lakeshore Flood
  (FLOOD_EVENT_TYPES). Other NOAA event types (e.g. Thunderstorm Wind,
  Winter Storm) are not included.
- If NOAA's directory listing does not contain a details file for a
  requested year, this script prints a warning and continues rather
  than raising, so a run can still succeed with partial year coverage.
- Supplementary point/line geometry is derived from each event's
  BEGIN_LAT/BEGIN_LON and END_LAT/END_LON fields where present (a
  LineString when both differing begin/end coordinates exist, otherwise
  a single Point); has_point_or_line_geometry flags whether an event
  received any such geometry at all, since many Storm Events records
  have no coordinates.
- The NWS zone-to-county correlation file is cached locally
  (noaa_zone_county_correlation.parquet) and reused across runs/states
  unless force=True, since it changes infrequently relative to Storm
  Events data itself.

OUTPUT: data/raw/noaa_storm_events_{STATE_ABBR}.parquet -- GeoParquet,
EPSG:4326, one row per county-attributed flood event, with the original
NOAA Storm Events columns plus county_fips, n_counties_for_zone,
geo_resolution, geometry, and has_point_or_line_geometry.

SINGLE ENTRY POINT: extract_noaa_storm_events() is the only function
meant to be called from outside this module. (extract_zone_county_correlation()
is also usable on its own if only the raw NWS correlation table is
needed.)

USAGE:
Interactive:
    from noaa_storm_events import extract_noaa_storm_events
    storm_events_gdf = extract_noaa_storm_events()

Headless CLI:
    Default:
        python src/noaa_storm_events.py
    Specify state:
        python src/noaa_storm_events.py --state-fips 25 --state-abbr MA --state-name Massachusetts
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point
import requests

from config import (
    DEFAULT_STATE_ABBR,
    DEFAULT_STATE_FIPS,
    DEFAULT_STATE_NAME,
    DEFAULT_YEAR_END,
    DEFAULT_YEAR_START,
    RAW_DIR,
)

HEADERS = {"User-Agent": "state-storm-events-extractor/1.0 (research use)"}
FLOOD_EVENT_TYPES = ("Flood", "Flash Flood", "Coastal Flood", "Lakeshore Flood")


def _get(url: str, *, timeout: int = 60, attempts: int = 3, **kwargs) -> requests.Response:
    """GET a URL with short retries and an identifying User-Agent."""
    error = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            error = exc
            if attempt < attempts - 1:
                import time
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Request failed after {attempts} attempts: {url}. Last error: {error}")


def _outpath(output_dir: str | Path, filename: str) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def extract_zone_county_correlation(
    output_dir: str | Path = RAW_DIR,
    *,
    force: bool = False,
) -> pd.DataFrame:
    """Download the newest NWS public-forecast-zone-to-county correlation file."""
    outpath = _outpath(output_dir, "noaa_zone_county_correlation.parquet")
    if outpath.exists() and not force:
        return pd.read_parquet(outpath)

    listing = _get("https://www.weather.gov/gis/ZoneCounty", timeout=60).text
    candidates = sorted(set(re.findall(r"bp\d{2}[a-z]{2}\d{2}\.dbx", listing, re.IGNORECASE)))
    if not candidates:
        raise RuntimeError("Could not find an NWS zone-to-county correlation .dbx file.")

    months = {
        "ja": 1, "fe": 2, "mr": 3, "ap": 4, "my": 5, "jn": 6,
        "jl": 7, "au": 8, "se": 9, "oc": 10, "no": 11, "de": 12,
    }

    def file_date(filename: str) -> tuple[int, int, int]:
        core = filename[2:-4]
        return (2000 + int(core[4:6]), months[core[2:4].lower()], int(core[:2]))

    latest = max(candidates, key=file_date)
    url = f"https://www.weather.gov/source/gis/Shapefiles/County/{latest}"
    columns = [
        "STATE", "ZONE", "CWA", "NAME", "STATEZONE", "COUNTY",
        "FIPS", "TIMEZONE", "FEAREA", "LAT", "LON",
    ]
    df = pd.read_csv(
        io.StringIO(_get(url).content.decode("latin-1")),
        sep="|",
        names=columns,
        header=None,
        dtype=str,
    )
    df.to_parquet(outpath, index=False)
    return df


def _event_geometry(row: pd.Series):
    """Make supplementary geometry from NOAA begin/end coordinates."""
    begin_lat, begin_lon = row.get("BEGIN_LAT"), row.get("BEGIN_LON")
    end_lat, end_lon = row.get("END_LAT"), row.get("END_LON")
    has_begin = pd.notna(begin_lat) and pd.notna(begin_lon)
    has_end = pd.notna(end_lat) and pd.notna(end_lon)

    if has_begin and has_end and (begin_lat, begin_lon) != (end_lat, end_lon):
        return LineString([(begin_lon, begin_lat), (end_lon, end_lat)])
    if has_begin:
        return Point(begin_lon, begin_lat)
    if has_end:
        return Point(end_lon, end_lat)
    return None


def extract_noaa_storm_events(
    state_fips: str | int = DEFAULT_STATE_FIPS,
    state_abbr: str = DEFAULT_STATE_ABBR,
    state_name: str = DEFAULT_STATE_NAME,
    year_start: int = DEFAULT_YEAR_START,
    year_end: int = DEFAULT_YEAR_END,
    output_dir: str | Path = RAW_DIR,
    *,
    force: bool = False,
) -> gpd.GeoDataFrame:
    """Extract flood-related NOAA Storm Events and add a five-digit county FIPS.

    Parameters
    ----------
    state_fips
        Two-digit state FIPS code, such as ``44`` for Rhode Island.
    state_abbr
        Two-letter postal abbreviation, such as ``RI``.
    state_name
        NOAA state name, such as ``Rhode Island``.
    year_start, year_end
        Inclusive range of Storm Events detail-file years.
    output_dir
        Folder for cached GeoParquet outputs.
    force
        If True, refresh cached outputs.
    """
    state_fips = str(state_fips).zfill(2)
    state_abbr = state_abbr.upper()
    outpath = _outpath(output_dir, f"noaa_storm_events_{state_abbr}.parquet")
    if outpath.exists() and not force:
        return gpd.read_parquet(outpath)

    base_url = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
    listing = _get(base_url, timeout=60).text
    frames: list[pd.DataFrame] = []

    for year in range(year_start, year_end + 1):
        match = re.search(
            rf"StormEvents_details-ftp_v1\.0_d{year}_c\d{{8}}\.csv\.gz",
            listing,
        )
        if not match:
            print(f"Warning: no Storm Events details file was found for {year}.")
            continue

        response = _get(base_url + match.group(0), timeout=90)
        events = pd.read_csv(io.BytesIO(response.content), compression="gzip", low_memory=False)
        events = events[
            (events["STATE"].astype(str).str.upper() == state_name.upper())
            & events["EVENT_TYPE"].isin(FLOOD_EVENT_TYPES)
        ].copy()
        events["data_year"] = year
        frames.append(events)

    if not frames:
        raise RuntimeError("No requested NOAA Storm Events files could be retrieved.")

    events = pd.concat(frames, ignore_index=True)
    if events.empty:
        result = gpd.GeoDataFrame(events, geometry=[], crs="EPSG:4326")
        result.to_parquet(outpath, index=False)
        return result

    events["CZ_FIPS"] = events["CZ_FIPS"].astype(str).str.zfill(3)
    events["geometry"] = events.apply(_event_geometry, axis=1)
    events["has_point_or_line_geometry"] = events["geometry"].notna()

    county_events = events[events["CZ_TYPE"] == "C"].copy()
    county_events["county_fips"] = state_fips + county_events["CZ_FIPS"]
    county_events["n_counties_for_zone"] = 1
    county_events["geo_resolution"] = "county_direct"

    zone_events = events[events["CZ_TYPE"] == "Z"].copy()
    if not zone_events.empty:
        correlation = extract_zone_county_correlation(output_dir)
        correlation = correlation[correlation["STATE"].str.upper() == state_abbr].copy()
        correlation["ZONE"] = correlation["ZONE"].astype(str).str.zfill(3)
        correlation["county_fips"] = state_fips + correlation["FIPS"].astype(str).str.zfill(3)
        correlation["n_counties_for_zone"] = correlation.groupby("ZONE")["county_fips"].transform("count")

        zone_events = zone_events.merge(
            correlation[["ZONE", "county_fips", "n_counties_for_zone"]],
            left_on="CZ_FIPS",
            right_on="ZONE",
            how="left",
        )
        zone_events["geo_resolution"] = "zone_to_county_join"

    combined = pd.concat([county_events, zone_events], ignore_index=True)
    result = gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326")
    result.to_parquet(outpath, index=False)
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Extract county-resolved NOAA flood-related Storm Events.")
    parser.add_argument("--state-fips", default=DEFAULT_STATE_FIPS)
    parser.add_argument("--state-abbr", default=DEFAULT_STATE_ABBR)
    parser.add_argument("--state-name", default=DEFAULT_STATE_NAME)
    parser.add_argument("--year-start", type=int, default=DEFAULT_YEAR_START)
    parser.add_argument("--year-end", type=int, default=DEFAULT_YEAR_END)
    parser.add_argument("--output-dir", default=str(RAW_DIR))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    data = extract_noaa_storm_events(
        state_fips=args.state_fips,
        state_abbr=args.state_abbr,
        state_name=args.state_name,
        year_start=args.year_start,
        year_end=args.year_end,
        output_dir=args.output_dir,
        force=args.force,
    )
    print(f"Saved {len(data):,} county-attributed flood-event rows.")
