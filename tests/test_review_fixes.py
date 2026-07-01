"""Regression tests for the fixes from the engineering review."""

from __future__ import annotations

from datetime import date

import pytest

# ---------------------------------------------------------------------------
# Workday API URL derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("careers_url,expected", [
    (
        "https://arm.wd1.myworkdayjobs.com/Arm_Careers",
        "https://arm.wd1.myworkdayjobs.com/wday/cxs/arm/Arm_Careers/jobs",
    ),
    (
        # Locale segment must be stripped — with it, every request 404s
        "https://rollsroyce.wd3.myworkdayjobs.com/en-US/rolr",
        "https://rollsroyce.wd3.myworkdayjobs.com/wday/cxs/rollsroyce/rolr/jobs",
    ),
    (
        "https://bentley.wd3.myworkdayjobs.com/en-US/external",
        "https://bentley.wd3.myworkdayjobs.com/wday/cxs/bentley/external/jobs",
    ),
])
def test_workday_api_url_strips_locale(careers_url: str, expected: str) -> None:
    from job_search.adapters.workday import _derive_api_url
    assert _derive_api_url(careers_url) == expected


def test_workday_normalise_survives_empty_bullet_fields() -> None:
    from job_search.adapters.workday import WorkdayAdapter
    adapter = WorkdayAdapter()
    raw = {
        "title": "Graduate Engineer",
        "_company_name": "Example",
        "bulletFields": [],  # present but empty — used to raise IndexError
        "externalPath": "",
        "url": "https://example.com/job/1",
        "locationsText": "Sheffield",
        "jobDescription": "JD text",
    }
    record = adapter.normalise(raw)
    assert record is not None
    assert record.title == "Graduate Engineer"


# ---------------------------------------------------------------------------
# Date parsing in normalise
# ---------------------------------------------------------------------------


def test_parse_date_handles_common_formats() -> None:
    from job_search.pipeline.normalise import _parse_date

    assert _parse_date("2026-06-05") == date(2026, 6, 5)
    assert _parse_date("2026-06-05T10:30:00Z") == date(2026, 6, 5)
    assert _parse_date("05/06/2026") == date(2026, 6, 5)  # UK d/m/Y
    assert _parse_date("1750000000000") == date(2025, 6, 15)  # Lever epoch millis
    assert _parse_date("") is None
    assert _parse_date(None) is None
    assert _parse_date("not a date") is None


# ---------------------------------------------------------------------------
# Greenhouse HTML-entity unescaping
# ---------------------------------------------------------------------------


def test_greenhouse_description_is_unescaped(monkeypatch) -> None:
    import job_search.pipeline.normalise as norm
    monkeypatch.setattr(norm, "geocode", lambda loc: None)

    from job_search.adapters.greenhouse import GreenhouseAdapter
    adapter = GreenhouseAdapter()
    raw = {
        "title": "Graduate Engineer",
        "_company_name": "Example",
        "_slug": "example",
        "id": 1,
        "absolute_url": "https://boards.greenhouse.io/example/jobs/1",
        "location": {"name": "London"},
        "content": "&lt;p&gt;Design &amp;amp; build FPGA systems.&lt;/p&gt;",
    }
    record = adapter.normalise(raw)
    assert record is not None
    assert "<p>" not in record.description
    assert "&lt;" not in record.description
    assert "Design" in record.description and "FPGA" in record.description


# ---------------------------------------------------------------------------
# Quota soft cap
# ---------------------------------------------------------------------------


def test_quota_hard_stop_raises(monkeypatch) -> None:
    from job_search.util import quota

    monkeypatch.setattr(quota, "soft_cap_gbp", lambda: 1.0)
    monkeypatch.setattr(quota, "today_total_gbp", lambda: 2.5)
    with pytest.raises(quota.QuotaExceededError):
        quota.check_quota()


def test_quota_below_cap_passes(monkeypatch) -> None:
    from job_search.util import quota

    monkeypatch.setattr(quota, "soft_cap_gbp", lambda: 1.0)
    monkeypatch.setattr(quota, "today_total_gbp", lambda: 0.5)
    quota.check_quota()  # must not raise


def test_quota_zero_cap_disables_check(monkeypatch) -> None:
    from job_search.util import quota

    monkeypatch.setattr(quota, "soft_cap_gbp", lambda: 0.0)
    monkeypatch.setattr(quota, "today_total_gbp", lambda: 999.0)
    quota.check_quota()  # must not raise


# ---------------------------------------------------------------------------
# Query generation seniority handling
# ---------------------------------------------------------------------------


def test_no_junior_modifiers_for_senior_profile() -> None:
    from job_search.profile.queries import _fallback_queries

    profile = {
        "experience_years": 12,
        "target_roles": {"core": ["Data Architect"], "adjacent": [], "stretch": []},
        "core_skills": [],
        "location": {"city": "Leeds"},
        "remote_ok": False,
        "negative_signals": {},
        "filters": {},
    }
    queries = _fallback_queries(profile)
    assert "Data Architect" in queries
    assert not any(q.startswith(("junior", "graduate", "entry level")) for q in queries)


def test_junior_modifiers_for_early_career_profile() -> None:
    from job_search.profile.queries import _fallback_queries

    profile = {
        "experience_years": 1,
        "target_roles": {"core": ["Data Analyst"], "adjacent": [], "stretch": []},
        "core_skills": [],
        "location": {"city": ""},
        "remote_ok": False,
        "negative_signals": {},
        "filters": {},
    }
    queries = _fallback_queries(profile)
    assert "junior Data Analyst" in queries
    assert "graduate Data Analyst" in queries


def test_no_hardcoded_engineer_suffix_for_skills() -> None:
    from job_search.profile.queries import _fallback_queries

    profile = {
        "experience_years": 5,
        "target_roles": {"core": ["Ward Sister"], "adjacent": [], "stretch": []},
        "core_skills": ["phlebotomy"],
        "location": {"city": "Manchester"},
        "remote_ok": False,
        "negative_signals": {},
        "filters": {},
    }
    queries = _fallback_queries(profile)
    assert "phlebotomy engineer" not in [q.lower() for q in queries]
    assert any("phlebotomy" in q.lower() for q in queries)


# ---------------------------------------------------------------------------
# Settings editor helper (used by the `job-search ui` command)
# ---------------------------------------------------------------------------


def test_update_settings_keys_preserves_comments() -> None:
    from job_search.ui import update_settings_text

    original = (
        "# top comment\n"
        "mode: passive # keep me\n"
        "quota_soft_cap_gbp: 5.00\n"
        "models:\n"
        "  rank:\n"
        "    model: claude-haiku-4-5\n"
        "    batch_size: 5\n"
        "  queries:\n"
        "    model: claude-haiku-4-5\n"
        "    max_queries: 40\n"
    )
    updated = update_settings_text(
        original,
        {
            "mode": "active",
            "quota_soft_cap_gbp": 2.5,
            "models.rank.model": "claude-sonnet-4-6",
            "models.queries.max_queries": 60,
        },
    )
    assert "mode: active # keep me" in updated
    assert "quota_soft_cap_gbp: 2.5" in updated
    assert "    model: claude-sonnet-4-6" in updated
    assert "    max_queries: 60" in updated
    # untouched keys keep their values and the top comment survives
    assert "# top comment" in updated
    assert "batch_size: 5" in updated
    # queries model was NOT updated
    assert updated.count("claude-haiku-4-5") == 1
