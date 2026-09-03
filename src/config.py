"""Project-level default configuration for state-based GIS extractors.

Individual extractor functions should accept explicit argument values. Import
these defaults only for optional function defaults or headless execution, so a
notebook can always override them for a particular run.
"""
from __future__ import annotations

from pathlib import Path

# Project directories
# Assumes this file is stored in PROJECT_ROOT/src/config.py.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

# Create expected local directories when the configuration is imported.
for directory in (RAW_DIR, PROCESSED_DIR):
    directory.mkdir(parents=True, exist_ok=True)

# Default study area. Change these together when changing the headless default.
DEFAULT_STATE_FIPS = "44"
DEFAULT_STATE_ABBR = "RI"
DEFAULT_STATE_NAME = "Rhode Island"

# Default inclusive analysis period.
DEFAULT_YEAR_START = 2010
DEFAULT_YEAR_END = 2024
DEFAULT_LAND_COVER_YEAR = 2024

# Common source-vintage defaults.
DEFAULT_TIGER_YEAR = 2023

# Standard HTTP identity used when an extractor does not need source-specific headers.
DEFAULT_HEADERS = {
    "User-Agent": "state-geospatial-extractors/1.0 (research use)",
}


def state_config(
    state_fips: str = DEFAULT_STATE_FIPS,
    state_abbr: str = DEFAULT_STATE_ABBR,
    state_name: str = DEFAULT_STATE_NAME,
) -> dict[str, str]:
    """Return normalized state settings for use in notebooks or extractors."""
    return {
        "state_fips": str(state_fips).zfill(2),
        "state_abbr": state_abbr.strip().upper(),
        "state_name": state_name.strip(),
    }
