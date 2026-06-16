"""Content-hash dedup and SQLite sync.

Key invariant: re-scraping a job never clobbers user-edited status or notes.
job_id = sha1(company.lower() + title.lower() + canonical_url)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date

from job_search.adapters.base import JobRecord

logger = logging.getLogger(__name__)


def sync_job(conn: sqlite3.Connection, record: JobRecord) -> str:
    """Insert or update a JobRecord in SQLite.

    Returns one of: 'inserted' | 'updated_meta' | 'updated_jd' | 'unchanged'.
    Never overwrites status or notes unless the row is brand-new.
    """
    today = date.today().isoformat()

    existing = conn.execute(
        "SELECT jd_content_hash, status FROM jobs WHERE job_id = ?",
        (record.job_id,),
    ).fetchone()

    if existing is None:
        # New job — insert with full data
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, source, matched_query,
                first_seen, last_seen, status,
                title, company, location, lat, lon,
                url, description, posted_date, closes_on,
                salary_raw, salary_min, salary_max,
                fit_score, fit_confidence, fit_reason,
                matched_keywords, ranker_version,
                jd_content_hash
            ) VALUES (
                :job_id, :source, :matched_query,
                :first_seen, :last_seen, 'new',
                :title, :company, :location, :lat, :lon,
                :url, :description, :posted_date, :closes_on,
                :salary_raw, :salary_min, :salary_max,
                :fit_score, :fit_confidence, :fit_reason,
                :matched_keywords, :ranker_version,
                :jd_content_hash
            )
            """,
            {
                "job_id": record.job_id,
                "source": record.source,
                "matched_query": record.matched_query,
                "first_seen": record.first_seen.isoformat(),
                "last_seen": today,
                "title": record.title,
                "company": record.company,
                "location": record.location,
                "lat": record.lat,
                "lon": record.lon,
                "url": record.url,
                "description": record.description,
                "posted_date": record.posted_date.isoformat() if record.posted_date else None,
                "closes_on": record.closes_on.isoformat() if record.closes_on else None,
                "salary_raw": record.salary_raw,
                "salary_min": record.salary_min,
                "salary_max": record.salary_max,
                "fit_score": record.fit_score,
                "fit_confidence": record.fit_confidence,
                "fit_reason": record.fit_reason,
                "matched_keywords": json.dumps(record.matched_keywords),
                "ranker_version": record.ranker_version,
                "jd_content_hash": record.jd_content_hash,
            },
        )
        conn.commit()
        return "inserted"

    # Existing job — update last_seen always, preserve status/notes
    existing_hash = existing["jd_content_hash"]
    jd_changed = existing_hash != record.jd_content_hash

    if jd_changed:
        conn.execute(
            """
            UPDATE jobs SET
                last_seen = ?,
                description = ?,
                jd_content_hash = ?,
                salary_raw = ?,
                salary_min = ?,
                salary_max = ?,
                closes_on = ?,
                lat = ?,
                lon = ?
            WHERE job_id = ?
            """,
            (
                today,
                record.description,
                record.jd_content_hash,
                record.salary_raw,
                record.salary_min,
                record.salary_max,
                record.closes_on.isoformat() if record.closes_on else None,
                record.lat,
                record.lon,
                record.job_id,
            ),
        )
        conn.commit()
        return "updated_jd"

    # JD unchanged — just touch last_seen
    conn.execute(
        "UPDATE jobs SET last_seen = ? WHERE job_id = ?",
        (today, record.job_id),
    )
    conn.commit()
    return "updated_meta"


def mark_closed_stale(conn: sqlite3.Connection, stale_days: int = 14) -> int:
    """Set status='closed' on new rows not seen in stale_days. Returns count updated."""
    cursor = conn.execute(
        """
        UPDATE jobs
        SET status = 'closed'
        WHERE status = 'new'
          AND julianday('now') - julianday(last_seen) > ?
        """,
        (stale_days,),
    )
    conn.commit()
    return cursor.rowcount
