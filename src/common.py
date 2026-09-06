"""
src/common.py
==============
Shared, generic utility functions used by other state-level data
extractor scripts in this project: FIPS-code formatting, output-path
creation, and a retrying HTTP GET wrapper.

FUNCTIONS PROVIDED:
- state_fips(value): Coerces a state FIPS code (given as either an int
  or a str) to a zero-padded two-character string, e.g. 9 -> "09",
  "44" -> "44". Two-digit, zero-padded FIPS codes are required by most
  Census and federal geospatial APIs and file-naming conventions; passing
  an un-padded single-digit code (e.g. "9" instead of "09") to those
  sources typically returns no results or a malformed URL.
- output_path(output_dir, filename): Creates the target output directory
  if it does not already exist (including any missing parent folders),
  then returns the full file path for a given filename inside it.
- get_with_retries(url, ...): Wraps requests.get() with a default
  identifying User-Agent header, a configurable timeout, and automatic
  retry with a linearly increasing backoff delay (3 seconds x attempt
  number) on any requests.RequestException. Raises a RuntimeError with
  the last underlying error if all attempts are exhausted.

SPECIAL CONSIDERATIONS:
- DEFAULT_HEADERS sets a generic, identifying User-Agent string
  ("state-geospatial-extractors/1.0 (research use)"). Some source APIs
  or file servers may expect or prefer a more specific User-Agent;
  callers can override this by passing their own headers= dict to
  get_with_retries().
- get_with_retries() re-raises as RuntimeError rather than the original
  exception type, so calling code should catch RuntimeError (not
  requests.RequestException) if it wants to handle final failures after
  all retries are exhausted.
- attempts must be at least 1; passing 0 or a negative number raises
  ValueError immediately rather than silently making zero requests.

USAGE:
Interactive or within another script:
    from common import state_fips, output_path, get_with_retries

    padded = state_fips(44)              # "44"
    path = output_path("data/raw", "example.parquet")
    response = get_with_retries("https://example.gov/data.csv")

This module has no standalone headless CLI usage.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

DEFAULT_HEADERS = {
    "User-Agent": "state-geospatial-extractors/1.0 (research use)"
}


def state_fips(value: str | int) -> str:
    """Return a state FIPS code as a zero-padded two-character string.

    Examples
    --------
    >>> state_fips(9)
    '09'
    >>> state_fips("44")
    '44'
    """
    return str(value).zfill(2)


def output_path(output_dir: str | Path, filename: str) -> Path:
    """Create an output directory if necessary and return its file path."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def get_with_retries(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
    attempts: int = 3,
    **kwargs,
) -> requests.Response:
    """Send a GET request with a User-Agent, timeout, and retry behavior.

    Parameters
    ----------
    url
        URL to request.
    headers
        Optional headers that replace the default headers.
    timeout
        Per-request timeout in seconds.
    attempts
        Total number of requests before raising an error.
    **kwargs
        Additional keyword arguments passed to ``requests.get()``, such as
        ``params``.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1.")

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                url,
                headers=headers or DEFAULT_HEADERS,
                timeout=timeout,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(3 * attempt)

    raise RuntimeError(
        f"Request failed after {attempts} attempts: {url}. Last error: {last_error}"
    )
