"""Regression tests: Indeed job-key URLs, title cleanup, jobspy salary strings."""

from __future__ import annotations

from job_search.adapters.jobspy_adapter import _format_salary
from job_search.pipeline.normalise import _canonical_url


def test_canonical_url_keeps_indeed_job_key() -> None:
    url = "https://uk.indeed.com/viewjob?jk=abc123&from=serp&tk=tracking"
    canonical = _canonical_url(url)
    assert "jk=abc123" in canonical          # the link is useless without it
    assert "from=" not in canonical          # tracking params still stripped
    assert "tk=" not in canonical


def test_canonical_url_still_strips_tracking_only_urls() -> None:
    url = "https://example.com/jobs/123?utm_source=feed&ref=homepage"
    assert _canonical_url(url) == "https://example.com/jobs/123"


def test_normalise_strips_trailing_period_from_title(monkeypatch) -> None:
    import job_search.pipeline.normalise as norm

    monkeypatch.setattr(norm, "geocode", lambda loc: None)
    record = norm.normalise(
        {
            "title": "Building an optical reference setup for quantum sensing.",
            "company": "Uni Lab",
            "url": "https://example.com/j/1",
            "description": "",
        },
        "test",
    )
    assert record.title == "Building an optical reference setup for quantum sensing"


def test_format_salary_builds_readable_strings() -> None:
    assert _format_salary(30000.0, 40000.0, "yearly") == "£30,000 - £40,000"
    assert _format_salary(18.0, None, "hourly") == "£18 per hour"
    assert _format_salary(None, 450.0, "daily") == "£450 per day"
    assert _format_salary(None, None, "yearly") == ""
