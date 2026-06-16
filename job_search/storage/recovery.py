"""Rebuild the SQLite DB from cached raw adapter responses (built in Phase 5).

Learned from JobFunnel: if the DB is lost or corrupted, raw API responses
cached in data/cache/{adapter}/{YYYY-MM-DD}/ can reconstruct the full DB
without any further network calls.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def recover_from_cache(conn: sqlite3.Connection, cache_dir: Path) -> int:
    """Walk cache_dir, re-normalise all cached raw responses, and re-insert into DB.

    Returns the number of job rows inserted.
    """
    raise NotImplementedError("recover_from_cache — built in Phase 5")
