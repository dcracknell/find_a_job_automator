"""Tests for the self-contained dashboard renderer."""

from __future__ import annotations

import sqlite3

from job_search.output.dashboard import regenerate_dashboard
from job_search.storage.db import migrate


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn=conn)
    return conn


def _insert_job(conn: sqlite3.Connection, **overrides) -> None:
    row = {
        "job_id": "j1",
        "source": "adzuna",
        "first_seen": "2026-07-06",
        "last_seen": "2026-07-06",
        "status": "new",
        "title": "Python Developer",
        "company": "Example Ltd",
        "location": "Sheffield",
        "url": "https://example.com/j1",
        "fit_score": 8.2,
        "fit_confidence": 0.9,
        "fit_reason": "Core skills match.",
        "matched_keywords": '["Python", "SQL"]',
        "salary_raw": "£30,000",
        "closes_on": None,
    }
    row.update(overrides)
    cols = ", ".join(row)
    placeholders = ", ".join(f":{k}" for k in row)
    conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({placeholders})", row)


def test_dashboard_lists_open_jobs_and_skips_closed(tmp_path) -> None:
    conn = _conn()
    _insert_job(conn)
    _insert_job(conn, job_id="j2", title="Data Engineer", company="Other Ltd",
                fit_score=5.5, status="applied")
    _insert_job(conn, job_id="j3", title="Should Not Appear", status="closed")
    conn.commit()

    out = tmp_path / "dashboard.html"
    regenerate_dashboard(conn, out, {"mode": "passive"})
    html = out.read_text(encoding="utf-8")

    assert "Python Developer" in html
    assert "Data Engineer" in html
    assert "Should Not Appear" not in html
    # Fit reason + keywords surface in the expandable detail row
    assert "Core skills match." in html
    assert "Python, SQL" in html
    # Stat tiles counted open jobs only
    assert "Open jobs" in html


def test_dashboard_escapes_scraped_html(tmp_path) -> None:
    """Job titles come from scraped sources — they must never inject markup."""
    conn = _conn()
    _insert_job(conn, title='<script>alert("x")</script> Engineer')
    conn.commit()

    out = tmp_path / "dashboard.html"
    regenerate_dashboard(conn, out, {"mode": "passive"})
    html = out.read_text(encoding="utf-8")

    assert '<script>alert("x")</script> Engineer' not in html
    assert "&lt;script&gt;" in html
