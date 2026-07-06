"""Tests for the Adzuna adapter (built in Phase 2).

Uses recorded fixture data — no live API calls in CI.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "adzuna_response.json"


def test_fixture_loads() -> None:
    """Sanity-check that the fixture file is valid JSON."""
    with FIXTURE_PATH.open() as f:
        data = json.load(f)
    assert "results" in data
    assert len(data["results"]) > 0


def test_normalise_produces_job_record(monkeypatch) -> None:
    """normalise() should return a JobRecord with all required fields."""
    import job_search.pipeline.normalise as norm
    monkeypatch.setattr(norm, "geocode", lambda loc: None)  # no network in tests
    from job_search.adapters.adzuna import AdzunaAdapter
    with FIXTURE_PATH.open() as f:
        data = json.load(f)
    adapter = AdzunaAdapter()
    record = adapter.normalise(data["results"][0])
    assert record.job_id
    assert record.title
    assert record.company


def test_fetch_sends_salary_floor_and_freshness_params(monkeypatch) -> None:
    """fetch() should push profile salary floor + freshness to the API server-side."""
    from job_search.adapters import adzuna as adz

    captured: dict = {}

    class FakeResp:
        def json(self):
            return {"results": []}

    def fake_get(url, params=None, **kwargs):
        captured["params"] = params
        return FakeResp()

    monkeypatch.setattr(adz.http, "get", fake_get)
    monkeypatch.setenv("ADZUNA_APP_ID", "test-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-key")

    adz.AdzunaAdapter().fetch(
        ["python developer"],
        {
            "apis": {"adzuna": {"results_per_query": 10}},
            "_profile": {"filters": {"salary_floor_gbp": 25000, "max_days_since_posted": 14}},
        },
    )

    params = captured["params"]
    assert params["salary_min"] == 25000
    assert params["salary_include_unknown"] == "1"  # never drop unlisted-salary jobs
    assert params["max_days_old"] == 14
