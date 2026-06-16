"""Tests for ranking prompt rendering and API-key fallbacks."""

from __future__ import annotations

from job_search.adapters.base import JobRecord
from job_search.pipeline.rank import _build_system_prompt, rank_jobs
from job_search.util.secrets import looks_configured_secret


def test_placeholder_secrets_are_not_treated_as_configured() -> None:
    assert not looks_configured_secret("")
    assert not looks_configured_secret("sk-ant-...")
    assert not looks_configured_secret("your_api_key_here")
    assert looks_configured_secret("sk-ant-api03-real-looking-value")


def test_system_prompt_allows_literal_json_braces() -> None:
    prompt = _build_system_prompt(
        {
            "system_prompt_template": (
                "Profile: {profile_json}\n"
                "Rubric: {scoring_rubric}\n"
                "Context: {domain_context}\n"
                'Example: [{"s": 8.2, "k": ["Python"]}]'
            ),
            "scoring_rubric": "Score carefully.",
        },
        {"name": "Candidate"},
        "Domain context.",
    )

    assert '{"name":"Candidate"}' in prompt
    assert "Score carefully." in prompt
    assert "Domain context." in prompt
    assert '[{"s": 8.2, "k": ["Python"]}]' in prompt


def test_rank_jobs_keeps_keyword_scores_without_configured_api_key(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-...")

    record = JobRecord(
        job_id="1",
        source="test",
        title="Python Developer",
        company="Example Ltd",
        location="Remote",
        lat=None,
        lon=None,
        url="https://example.com/jobs/1",
        description="Build services in Python.",
        posted_date=None,
        closes_on=None,
        salary_raw=None,
        salary_min=None,
        salary_max=None,
    )
    profile = {
        "core_skills": ["Python"],
        "adjacent_skills": [],
        "negative_signals": {},
    }

    ranked = rank_jobs([record], profile, {"models": {"rank": {"batch_size": 5}}})

    assert ranked[0].fit_score == 10.0
    assert ranked[0].fit_confidence == 0.3
    assert ranked[0].fit_reason == "keyword pre-score only; ANTHROPIC_API_KEY not configured"
    assert ranked[0].ranker_version
