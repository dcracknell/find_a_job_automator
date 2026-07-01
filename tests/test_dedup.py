"""Tests for content-hash dedup logic and SQLite sync."""

from __future__ import annotations

import sqlite3

from job_search.adapters.base import JobRecord
from job_search.pipeline.dedup import existing_jobs_map, sync_job
from job_search.storage.db import migrate


def test_job_id_is_deterministic() -> None:
    """The same inputs must always produce the same job_id."""
    a = JobRecord.make_job_id("Arm Ltd", "FPGA Engineer", "https://arm.com/jobs/123")
    b = JobRecord.make_job_id("Arm Ltd", "FPGA Engineer", "https://arm.com/jobs/123")
    assert a == b


def test_job_id_is_case_insensitive() -> None:
    """Company and title are lowercased before hashing."""
    a = JobRecord.make_job_id("ARM LTD", "FPGA ENGINEER", "https://arm.com/jobs/123")
    b = JobRecord.make_job_id("arm ltd", "fpga engineer", "https://arm.com/jobs/123")
    assert a == b


def test_different_urls_produce_different_ids() -> None:
    """Two otherwise identical jobs at different URLs are distinct."""
    a = JobRecord.make_job_id("Arm Ltd", "Engineer", "https://arm.com/jobs/1")
    b = JobRecord.make_job_id("Arm Ltd", "Engineer", "https://arm.com/jobs/2")
    assert a != b


# ---------------------------------------------------------------------------
# sync_job
# ---------------------------------------------------------------------------


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn=conn)
    return conn


def _record(**kwargs) -> JobRecord:
    defaults = dict(
        job_id="job-1",
        source="test",
        title="Graduate Engineer",
        company="Example Ltd",
        location="Sheffield",
        lat=None,
        lon=None,
        url="https://example.com/1",
        description="Original JD",
        posted_date=None,
        closes_on=None,
        salary_raw=None,
        salary_min=None,
        salary_max=None,
        fit_score=8.0,
        fit_confidence=0.8,
        fit_reason="good match",
        matched_keywords=["python"],
        ranker_version="v-test",
        jd_content_hash="hash-a",
    )
    defaults.update(kwargs)
    return JobRecord(**defaults)


def test_sync_job_insert() -> None:
    """A new job is inserted with status='new' and its score."""
    conn = _conn()
    assert sync_job(conn, _record()) == "inserted"
    row = conn.execute("SELECT * FROM jobs WHERE job_id='job-1'").fetchone()
    assert row["status"] == "new"
    assert row["fit_score"] == 8.0
    conn.close()


def test_sync_job_preserves_user_edits() -> None:
    """Re-syncing an existing job with same jd_content_hash preserves status and notes."""
    conn = _conn()
    sync_job(conn, _record())
    conn.execute("UPDATE jobs SET status='applied', notes='sent CV' WHERE job_id='job-1'")
    conn.commit()

    assert sync_job(conn, _record()) == "updated_meta"
    row = conn.execute("SELECT status, notes FROM jobs WHERE job_id='job-1'").fetchone()
    assert row["status"] == "applied"
    assert row["notes"] == "sent CV"
    conn.close()


def test_sync_job_unchanged_does_not_clobber_score() -> None:
    """An unchanged, un-reranked record must not overwrite the stored score."""
    conn = _conn()
    sync_job(conn, _record())
    # Second scrape of same JD: pipeline skipped ranking, so fit fields are junk
    stale = _record(fit_score=1.2, fit_reason="keyword pre-score", freshly_ranked=False)
    assert sync_job(conn, stale) == "updated_meta"
    row = conn.execute("SELECT fit_score, fit_reason FROM jobs WHERE job_id='job-1'").fetchone()
    assert row["fit_score"] == 8.0
    assert row["fit_reason"] == "good match"
    conn.close()


def test_sync_job_persists_score_when_jd_changed() -> None:
    """A changed JD with a fresh ranking must persist the new score."""
    conn = _conn()
    sync_job(conn, _record())
    updated = _record(
        description="New JD",
        jd_content_hash="hash-b",
        fit_score=4.5,
        fit_reason="requirements changed",
        ranker_version="v-test2",
        freshly_ranked=True,
    )
    assert sync_job(conn, updated) == "updated_jd"
    row = conn.execute(
        "SELECT fit_score, fit_reason, ranker_version, jd_content_hash "
        "FROM jobs WHERE job_id='job-1'"
    ).fetchone()
    assert row["fit_score"] == 4.5
    assert row["fit_reason"] == "requirements changed"
    assert row["ranker_version"] == "v-test2"
    assert row["jd_content_hash"] == "hash-b"
    conn.close()


def test_sync_job_persists_fresh_rerank_of_unchanged_jd() -> None:
    """A stale-version re-rank (same JD) must also be persisted."""
    conn = _conn()
    sync_job(conn, _record(ranker_version="v-old"))
    reranked = _record(fit_score=7.1, ranker_version="v-new", freshly_ranked=True)
    assert sync_job(conn, reranked) == "updated_meta"
    row = conn.execute(
        "SELECT fit_score, ranker_version FROM jobs WHERE job_id='job-1'"
    ).fetchone()
    assert row["fit_score"] == 7.1
    assert row["ranker_version"] == "v-new"
    conn.close()


def test_existing_jobs_map() -> None:
    conn = _conn()
    sync_job(conn, _record())
    mapping = existing_jobs_map(conn)
    assert mapping == {"job-1": ("hash-a", "v-test")}
    conn.close()
