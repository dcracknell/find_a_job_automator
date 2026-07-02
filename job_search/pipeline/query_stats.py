"""Persistence for search-query usage and yield.

Every generated query is recorded per run with how many results it matched and
how many were brand-new jobs. Query generation reads this back to (a) avoid
re-running the same searches every run and (b) keep the ones that reliably
surface new openings.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date

logger = logging.getLogger(__name__)


def load_query_stats(conn: sqlite3.Connection) -> list[dict]:
    """Return all query stats as dicts (empty list if the table is missing)."""
    try:
        rows = conn.execute(
            "SELECT query, first_used, last_used, times_used, jobs_seen, jobs_new "
            "FROM query_stats"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def record_query_stats(
    conn: sqlite3.Connection,
    queries: list[str],
    seen_counts: dict[str, int],
    new_counts: dict[str, int],
    today: date | None = None,
) -> None:
    """Upsert usage/yield for every query used this run.

    Queries with zero hits are still recorded — otherwise they look 'never
    tried' forever and get re-selected every run.
    """
    if today is None:
        today = date.today()
    today_str = today.isoformat()

    for query in queries:
        seen = int(seen_counts.get(query, 0))
        new = int(new_counts.get(query, 0))
        conn.execute(
            """
            INSERT INTO query_stats (query, first_used, last_used, times_used, jobs_seen, jobs_new)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(query) DO UPDATE SET
                last_used = excluded.last_used,
                times_used = times_used + 1,
                jobs_seen = jobs_seen + excluded.jobs_seen,
                jobs_new = jobs_new + excluded.jobs_new
            """,
            (query, today_str, today_str, seen, new),
        )
    conn.commit()
