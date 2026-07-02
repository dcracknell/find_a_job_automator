"""HTTP helper with retry, backoff, rate limiting, and polite delay.

ALL HTTP calls in the codebase must go through this module.
No direct requests.get() calls in adapters or anywhere else.

Retries cover connection errors, timeouts, and retryable HTTP statuses
(429 and 5xx). The polite delay is applied per host, so interleaved
requests to different hosts don't serialise on one global sleep.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "job-search-pipeline/1.0 (contact: see README)"})

_POLITE_DELAY_S = 1.0  # seconds between requests to the same host

_RETRYABLE_STATUSES = frozenset([429, 500, 502, 503, 504])

_host_lock = threading.Lock()
_last_request_by_host: dict[str, float] = {}


def _polite_wait(url: str) -> None:
    """Sleep just long enough to keep >= _POLITE_DELAY_S between same-host requests."""
    host = urlparse(url).netloc
    with _host_lock:
        last = _last_request_by_host.get(host, 0.0)
        now = time.monotonic()
        wait = _POLITE_DELAY_S - (now - last)
        # Reserve the slot before sleeping so concurrent callers space out too.
        _last_request_by_host[host] = max(now, last + _POLITE_DELAY_S)
    if wait > 0:
        time.sleep(wait)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in _RETRYABLE_STATUSES
    return False


def _log_attempt(retry_state: Any) -> None:
    if retry_state.attempt_number > 1:
        logger.warning(
            "HTTP retry %d for %s",
            retry_state.attempt_number,
            retry_state.args[0] if retry_state.args else "?",
        )


def _respect_retry_after(resp: requests.Response) -> None:
    """Honour a Retry-After header (seconds form) before tenacity's own backoff."""
    retry_after = resp.headers.get("Retry-After", "")
    try:
        delay = min(float(retry_after), 60.0)
    except ValueError:
        return
    if delay > 0:
        time.sleep(delay)


@retry(
    retry=retry_if_exception(_is_retryable),
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
    """Make a GET request with retry/backoff and per-host polite delay."""
    _polite_wait(url)
    resp = _SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    if resp.status_code in _RETRYABLE_STATUSES:
        _respect_retry_after(resp)
    resp.raise_for_status()
    return resp


def get_once(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
    **kwargs: Any,
) -> requests.Response:
    """GET with polite delay but NO retries.

    For probing endpoints where failure is the expected common case (e.g.
    discovery slug guessing): a wrong slug can be a 404 or an NXDOMAIN, and
    retrying either just multiplies the wasted time.
    """
    _polite_wait(url)
    resp = _SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    resp.raise_for_status()
    return resp


@retry(
    retry=retry_if_exception(_is_retryable),
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
    """Make a POST request with retry/backoff and per-host polite delay."""
    _polite_wait(url)
    resp = _SESSION.post(url, json=json, headers=headers, timeout=timeout, **kwargs)
    if resp.status_code in _RETRYABLE_STATUSES:
        _respect_retry_after(resp)
    resp.raise_for_status()
    return resp
