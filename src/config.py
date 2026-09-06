"""
src/config.py
==============
Project-level default configuration for state-based GIS extractors:
default project directories, default study-area state, default year
range, and a default HTTP User-Agent identity.

WHAT IT PROVIDES:
- Project directory paths (PROJECT_ROOT, SRC_DIR, DATA_DIR, RAW_DIR), computed relative to this file's own location on disk
  (assumes this file lives at PROJECT_ROOT/src/config.py). RAW_DIR and
  is created automatically on import if it does not
  already exist.
- Default study-area identifiers: DEFAULT_STATE_FIPS ("44"),
  DEFAULT_STATE_ABBR ("RI"), DEFAULT_STATE_NAME ("Rhode Island"). These
  three values describe the same state and are intended to be changed
  together.
- Default inclusive year range for time-series extractors
  (DEFAULT_YEAR_START = 2010, DEFAULT_YEAR_END = 2024) and a default
  land-cover vintage year (DEFAULT_LAND_COVER_YEAR = 2024).
- DEFAULT_TIGER_YEAR (2023), the default Census TIGER/Line vintage used
  by extractors that download Census geometry.
- DEFAULT_HEADERS, a generic identifying User-Agent string
  ("state-geospatial-extractors/1.0 (research use)") for extractors that
  do not need a source-specific header.
- state_config(state_fips, state_abbr, state_name): Takes any of the
  three state identifiers (defaulting to the module's own DEFAULT_*
  values) and returns them together as a normalized dict -- FIPS
  zero-padded to two digits, abbreviation upper-cased and stripped, name
  stripped of surrounding whitespace. Useful for passing a single,
  already-normalized state definition into a notebook workflow or
  another function.

SPECIAL CONSIDERATIONS:
- Individual extractor functions are designed to accept explicit
  argument values; the intent is that these DEFAULT_* values are only
  used as optional function defaults or for convenience during headless
  CLI execution, not as hardcoded values baked into extractor logic.
  Any notebook or script can override them for a specific run by 
  passing different arguments.
- To change the project's headless default state (for example, from
  Rhode Island to another state), update DEFAULT_STATE_FIPS,
  DEFAULT_STATE_ABBR, and DEFAULT_STATE_NAME together in this one file.

USAGE:
Interactive or within another script:
    from config import (
        DEFAULT_STATE_FIPS, DEFAULT_STATE_ABBR, RAW_DIR, state_config
    )

    settings = state_config()                       # Rhode Island defaults
    settings = state_config(25, "MA", "Massachusetts")  # explicit override

This module has no standalone headless CLI usage.
"""

from __future__ import annotations

from pathlib import Path

# Project directories
# Assumes this file is stored in PROJECT_ROOT/src/config.py.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = PROJECT_ROOT / "data" / "raw"

# Create expected local directories when the configuration is imported.
for directory in (RAW_DIR,):
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
