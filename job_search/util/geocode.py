"""Nominatim geocoding with local cache.

Uses the free Nominatim API (openstreetmap.org). Results are cached in
data/cache/geocode/ to avoid repeat lookups and respect the 1 req/sec rate limit.
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

from job_search import PROJECT_ROOT

logger = logging.getLogger(__name__)

_CACHE_DIR: Path = PROJECT_ROOT / "data" / "cache" / "geocode"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_REQUEST_DELAY_S = 1.1  # Nominatim policy: max 1 request/second


def _cache_path(location: str) -> Path:
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in location).strip()[:80]
    return _CACHE_DIR / f"{safe}.json"


def geocode(location: str) -> tuple[float, float] | None:
    """Return (lat, lon) for a location string, or None if not found.

    Results are cached locally; cache never expires (locations don't move).
    """
    if not location or location.lower() in ("remote", "uk-wide", "nationwide", "flexible"):
        return None

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(location)

    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            if data is None:
                return None
            return float(data["lat"]), float(data["lon"])
        except (json.JSONDecodeError, KeyError):
            pass

    # Query Nominatim — import here to avoid circular deps at module level
    try:
        import requests as _requests

        time.sleep(_REQUEST_DELAY_S)
        resp = _requests.get(
            _NOMINATIM_URL,
            params={
                "q": f"{location}, UK",
                "format": "json",
                "limit": 1,
                "countrycodes": "gb",
            },
            headers={"User-Agent": "job-search-pipeline/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as exc:
        logger.warning("geocode: failed for %r: %s", location, exc)
        # Cache negative so we don't hammer the API
        cache_file.write_text("null")
        return None

    if not results:
        cache_file.write_text("null")
        return None

    lat = float(results[0]["lat"])
    lon = float(results[0]["lon"])
    cache_file.write_text(json.dumps({"lat": lat, "lon": lon}))
    return lat, lon


def distance_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in miles between two lat/lon points."""
    R = 3_958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
