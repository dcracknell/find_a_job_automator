"""Migration 003 — company_probes table.

Records every company whose careers site the discovery step has probed, so a
company is only ever probed once (whether or not an ATS was found).
"""

from __future__ import annotations

import sqlite3


def run(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS company_probes (
            company_lower TEXT PRIMARY KEY,
            company TEXT NOT NULL,
            probed_at TEXT NOT NULL,
            ats TEXT,          -- provider name when found, NULL when nothing found
            slug TEXT
        );
        """
    )
