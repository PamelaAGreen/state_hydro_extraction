"""Shared utility functions for reusable state-level data extractors."""
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
