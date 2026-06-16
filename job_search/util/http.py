"""HTTP helper with retry, backoff, rate limiting, and polite delay.

ALL HTTP calls in the codebase must go through this module.
No direct requests.get() calls in adapters or anywhere else.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "job-search-pipeline/1.0 (contact: see README)"})

_POLITE_DELAY_S = 1.0  # seconds between requests to the same host


def _log_attempt(retry_state: Any) -> None:
    if retry_state.attempt_number > 1:
        logger.warning(
            "HTTP retry %d for %s",
            retry_state.attempt_number,
            retry_state.args[0] if retry_state.args else "?",
        )


@retry(
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    before_sleep=_log_attempt,
    reraise=True,
)
def get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 30,
    **kwargs: Any,
) -> requests.Response:
    """Make a GET request with retry/backoff and polite delay."""
    time.sleep(_POLITE_DELAY_S)
    resp = _SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    resp.raise_for_status()
    return resp


@retry(
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    before_sleep=_log_attempt,
    reraise=True,
)
def post(
    url: str,
    *,
    json: Any = None,
    headers: dict | None = None,
    timeout: int = 30,
    **kwargs: Any,
) -> requests.Response:
    """Make a POST request with retry/backoff and polite delay."""
    time.sleep(_POLITE_DELAY_S)
    resp = _SESSION.post(url, json=json, headers=headers, timeout=timeout, **kwargs)
    resp.raise_for_status()
    return resp
