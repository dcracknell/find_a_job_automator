"""Migration 002 — query_stats table.

Tracks every search query the pipeline has run and what it yielded, so query
generation can rotate through fresh phrasings instead of re-running the same
searches every run, while keeping queries that actually produce new jobs.
"""

from __future__ import annotations

import sqlite3


def run(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS query_stats (
            query TEXT PRIMARY KEY,
            first_used TEXT,
            last_used TEXT,
            times_used INTEGER NOT NULL DEFAULT 0,
            jobs_seen INTEGER NOT NULL DEFAULT 0,
            jobs_new INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_query_stats_last_used ON query_stats(last_used);
        """
    )
