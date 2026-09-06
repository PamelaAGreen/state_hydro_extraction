"""
src/openfema_nfip_claims.py
============================
Extracts OpenFEMA National Flood Insurance Program (NFIP) redacted
claims records for one state over an inclusive date range.

DATA SOURCE: OpenFEMA API, FimaNfipClaims dataset (redacted claims).
- Dataset documentation:
  https://www.fema.gov/openfema-data-page/fima-nfip-redacted-claims-v2
- API base endpoint used by this script:
  https://www.fema.gov/api/open/v2/FimaNfipClaims

FORMAT: This is a standard OData-style REST API, not a bulk file
download. Records are requested as JSON using OData query parameters: 
$filter to select a state and date-of-loss range, $top to set the page size, 
and $skip topaginate. This script builds a $filter clause combining state eq
'{state_abbr}' with a dateOfLoss range (year_start-01-01 through
year_end-12-31), then repeatedly requests pages of 1,000 records
($top=1000), advancing $skip by 1,000 each time, until a page comes
back with fewer than 1,000 records (indicating the last page) or an
empty page is returned.

SPECIAL CONSIDERATIONS:
- "Redacted" in the dataset name means certain fields (e.g. precise
  location, policy/claimant identifying details) are generalized or
  withheld by FEMA before publication; full claims data including location
  information is not available with this dataset.
- Filtering is done entirely server-side via the $filter parameter --
  this script does not download all national claims.
- year_end must be greater than or equal to year_start; passing a
  reversed range raises a ValueError flag.
- No API key or authentication is required for this OpenFEMA endpoint.
- A short delay (time.sleep(0.25)) is added between pages to avoid
  sending requests to the OpenFEMA API too rapidly.
- If OpenFEMA returns a non-JSON response (e.g. an HTML error page),
  this raises a RuntimeError that includes the first 400 characters of
  the raw response.
- state_abbr is matched against OpenFEMA's own "state" field using the
  two-letter postal abbreviation, upper-cased by this script before the
  request is sent.

OUTPUT: data/raw/nfip_claims_{STATE_ABBR}_{YEAR_START}_{YEAR_END}.parquet
-- one row per redacted claim record, with OpenFEMA's original
FimaNfipClaims fields preserved as returned by the API (no columns are
renamed, dropped, or derived by this script).

SINGLE ENTRY POINT: extract_nfip_claims() is the only function meant to
be called from outside this module.

USAGE:
Interactive:
    from openfema_nfip_claims import extract_nfip_claims
    claims_df = extract_nfip_claims()

Headless CLI:
    Default:
        python src/openfema_nfip_claims.py
    Specify state:
        python src/openfema_nfip_claims.py --state-abbr MA --year-start 2010 --year-end 2024
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

from config import (
    DEFAULT_STATE_ABBR,
    DEFAULT_YEAR_END,
    DEFAULT_YEAR_START,
    RAW_DIR,
)

HEADERS = {"User-Agent": "openfema-nfip-claims-extractor/1.0 (research use)"}


def _get(url: str, *, timeout: int = 90, attempts: int = 3, **kwargs) -> requests.Response:
    """Request OpenFEMA with retries."""
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
    raise RuntimeError(f"OpenFEMA request failed: {url}. Last error: {error}")


def extract_nfip_claims(
    state_abbr: str = DEFAULT_STATE_ABBR,
    year_start: int = DEFAULT_YEAR_START,
    year_end: int = DEFAULT_YEAR_END,
    output_dir: str | Path = RAW_DIR,
    *,
    force: bool = False,
) -> pd.DataFrame:
    """Return NFIP redacted claims for a state over an inclusive date range.

    Parameters
    ----------
    state_abbr
        Two-letter postal abbreviation, such as ``RI``.
    year_start, year_end
        Inclusive years used to filter ``dateOfLoss`` on the OpenFEMA server.
    output_dir
        Folder where the Parquet result will be saved.
    force
        If True, refresh an existing cached output.

    Notes
    -----
    The OpenFEMA endpoint is paginated. This function requests batches of
    1,000 records until no additional records remain.
    """
    if year_end < year_start:
        raise ValueError("year_end must be greater than or equal to year_start.")

    state_abbr = state_abbr.upper()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outpath = output_dir / f"nfip_claims_{state_abbr}_{year_start}_{year_end}.parquet"

    if outpath.exists() and not force:
        return pd.read_parquet(outpath)

    base_url = "https://www.fema.gov/api/open/v2/FimaNfipClaims"
    date_filter = (
        f"state eq '{state_abbr}' "
        f"and dateOfLoss ge {year_start}-01-01 "
        f"and dateOfLoss le {year_end}-12-31"
    )

    page_size = 1000
    offset = 0
    frames: list[pd.DataFrame] = []

    while True:
        print(f"Fetching NFIP claims: offset {offset:,}")
        params = {
            "$filter": date_filter,
            "$top": page_size,
            "$skip": offset,
            "$format": "json",
        }
        response = _get(base_url, params=params)

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"OpenFEMA returned non-JSON: {response.text[:400]}"
            ) from exc

        records = payload.get("FimaNfipClaims", [])
        if not records:
            break

        frames.append(pd.DataFrame(records))
        if len(records) < page_size:
            break

        offset += page_size
        time.sleep(0.25)

    claims = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    claims.to_parquet(outpath, index=False)
    return claims


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract OpenFEMA NFIP redacted claims for a state and period."
    )
    parser.add_argument("--state-abbr", default=DEFAULT_STATE_ABBR)
    parser.add_argument("--year-start", type=int, default=DEFAULT_YEAR_START)
    parser.add_argument("--year-end", type=int, default=DEFAULT_YEAR_END)
    parser.add_argument("--output-dir", default=str(RAW_DIR))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = extract_nfip_claims(
        state_abbr=args.state_abbr,
        year_start=args.year_start,
        year_end=args.year_end,
        output_dir=args.output_dir,
        force=args.force,
    )
    print(f"Saved {len(result):,} NFIP redacted-claim records.")
