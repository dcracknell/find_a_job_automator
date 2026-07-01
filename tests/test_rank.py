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

    # One core-skill hit = 3 points against _PRESCORE_FULL_MARKS (15) → 2.0
    assert ranked[0].fit_score == 2.0
    assert ranked[0].fit_confidence == 0.3
    assert ranked[0].fit_reason == "keyword pre-score only; ANTHROPIC_API_KEY not configured"
    assert ranked[0].ranker_version


# ---------------------------------------------------------------------------
# keyword_prescore
# ---------------------------------------------------------------------------


def _record(title: str, description: str) -> JobRecord:
    return JobRecord(
        job_id="x", source="test", title=title, company="Co", location="",
        lat=None, lon=None, url="https://example.com", description=description,
        posted_date=None, closes_on=None,
        salary_raw=None, salary_min=None, salary_max=None,
    )


def test_prescore_matches_words_not_substrings() -> None:
    from job_search.pipeline.rank import keyword_prescore

    profile = {"core_skills": ["C", "Git"], "adjacent_skills": [], "negative_signals": {}}

    # "C" must not match the letter c inside words; "Git" must not match "digital"
    florist = _record("Florist", "Arrange flowers. Digital marketing occasionally.")
    assert keyword_prescore(florist, profile) == 0.0
    assert florist.matched_keywords == []

    embedded = _record("Embedded Engineer", "Write C for microcontrollers, use Git daily.")
    assert keyword_prescore(embedded, profile) > 0.0
    assert set(embedded.matched_keywords) == {"C", "Git"}


def test_prescore_good_match_scores_high() -> None:
    from job_search.pipeline.rank import keyword_prescore

    profile = {
        "core_skills": ["Verilog", "FPGA", "digital design", "DSP", "Python"],
        "adjacent_skills": ["SystemVerilog"],
        "negative_signals": {},
    }
    rec = _record(
        "Graduate FPGA Engineer",
        "Verilog and SystemVerilog digital design with DSP pipelines; "
        "FPGA prototyping; Python tooling.",
    )
    # 5 core hits (15 pts) + 1 adjacent = full marks
    assert keyword_prescore(rec, profile) == 10.0


def test_prescore_title_exclude_penalty_is_word_bounded() -> None:
    from job_search.pipeline.rank import keyword_prescore

    profile = {
        "core_skills": ["Python"],
        "adjacent_skills": [],
        "negative_signals": {"title_excludes": ["hr", "sales"]},
    }
    # "hr" must not penalise "Three Bridges"; "sales" must not penalise "Salesforce"
    rec = _record("Three Bridges Salesforce Python Engineer", "Python.")
    assert keyword_prescore(rec, profile) == 2.0  # no penalty applied

    rec2 = _record("HR Systems Python Engineer", "Python.")
    assert keyword_prescore(rec2, profile) == 0.0  # 2.0 - 4.0 floor 0


# ---------------------------------------------------------------------------
# _apply_scores index matching
# ---------------------------------------------------------------------------


def test_apply_scores_matches_by_index_when_reordered() -> None:
    from job_search.pipeline.rank import _apply_scores

    a = _record("Job A", "")
    b = _record("Job B", "")
    scores = [
        {"i": 1, "s": 9.0, "c": 0.9, "r": "for B", "k": []},
        {"i": 0, "s": 2.0, "c": 0.9, "r": "for A", "k": []},
    ]
    _apply_scores([a, b], scores, "v-test")
    assert a.fit_score == 2.0 and a.fit_reason == "for A"
    assert b.fit_score == 9.0 and b.fit_reason == "for B"
    assert a.freshly_ranked and b.freshly_ranked


def test_apply_scores_falls_back_to_positional_without_index() -> None:
    from job_search.pipeline.rank import _apply_scores

    a = _record("Job A", "")
    b = _record("Job B", "")
    scores = [
        {"s": 5.0, "c": 0.5, "r": "first", "k": []},
        {"s": 6.0, "c": 0.5, "r": "second", "k": []},
    ]
    _apply_scores([a, b], scores, "v-test")
    assert a.fit_score == 5.0
    assert b.fit_score == 6.0
