"""Tests for content-hash dedup logic (built in Phase 2)."""

from __future__ import annotations

import pytest

from job_search.adapters.base import JobRecord


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


@pytest.mark.skip(reason="sync_job not yet implemented (Phase 2)")
def test_sync_job_insert() -> None:
    """A new job is inserted with status='new'."""
    pass


@pytest.mark.skip(reason="sync_job not yet implemented (Phase 2)")
def test_sync_job_preserves_user_edits() -> None:
    """Re-syncing an existing job with same jd_content_hash preserves status and notes."""
    pass
