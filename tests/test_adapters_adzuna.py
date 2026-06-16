"""Tests for the Adzuna adapter (built in Phase 2).

Uses recorded fixture data — no live API calls in CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "adzuna_response.json"


def test_fixture_loads() -> None:
    """Sanity-check that the fixture file is valid JSON."""
    with FIXTURE_PATH.open() as f:
        data = json.load(f)
    assert "results" in data
    assert len(data["results"]) > 0


@pytest.mark.skip(reason="AdzunaAdapter.normalise not yet implemented (Phase 2)")
def test_normalise_produces_job_record() -> None:
    """normalise() should return a JobRecord with all required fields."""
    from job_search.adapters.adzuna import AdzunaAdapter
    with FIXTURE_PATH.open() as f:
        data = json.load(f)
    adapter = AdzunaAdapter()
    record = adapter.normalise(data["results"][0])
    assert record.job_id
    assert record.title
    assert record.company
