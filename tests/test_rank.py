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


# ---------------------------------------------------------------------------
# duplicate score sharing + Message Batches API path
# ---------------------------------------------------------------------------

_PROFILE = {"core_skills": ["Python"], "adjacent_skills": [], "negative_signals": {}}


def _named_record(job_id: str, title: str, company: str) -> JobRecord:
    return JobRecord(
        job_id=job_id, source="test", title=title, company=company, location="Leeds",
        lat=None, lon=None, url=f"https://example.com/{job_id}",
        description="Build services in Python.",
        posted_date=None, closes_on=None,
        salary_raw=None, salary_min=None, salary_max=None,
    )


def _isolate_quota(monkeypatch, tmp_path) -> None:
    """Keep test quota logging out of the real data/ directory."""
    from job_search.util import quota

    monkeypatch.setattr(quota, "_QUOTA_JSONL", tmp_path / "quota.jsonl")
    monkeypatch.setattr(quota, "_DB_PATH", tmp_path / "jobs.db")


def test_rank_jobs_shares_scores_across_duplicate_postings(monkeypatch, tmp_path) -> None:
    """Identical title+company from two sources is ranked once, scored twice."""
    import job_search.pipeline.rank as rank_mod

    _isolate_quota(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test-value-000000")

    dup_a = _named_record("a1", "Python Developer", "Example Ltd")
    dup_b = _named_record("a2", "Python Developer", "Example Ltd")
    dup_b.source = "reed"
    other = _named_record("b1", "Data Engineer", "Other Ltd")

    calls: list[list[JobRecord]] = []

    def fake_call(client, model, max_tokens, system_prompt, batch, ranker_cfg):
        calls.append(batch)
        return [
            {"i": 0, "s": 8.0, "c": 0.9, "r": "strong match", "k": ["Python"]},
            {"i": 1, "s": 3.0, "c": 0.8, "r": "weak match", "k": []},
        ]

    monkeypatch.setattr(rank_mod, "_call_llm_batch", fake_call)

    rank_mod.rank_jobs(
        [dup_a, dup_b, other],
        _PROFILE,
        {"models": {"rank": {"batch_size": 5, "use_batch_api": False}}},
    )

    # Only the two unique postings hit the LLM
    assert len(calls) == 1
    assert [r.job_id for r in calls[0]] == ["a1", "b1"]
    # The duplicate inherited its twin's LLM verdict
    assert dup_a.fit_score == 8.0
    assert dup_b.fit_score == 8.0
    assert dup_b.fit_reason == "strong match"
    assert dup_b.freshly_ranked
    assert other.fit_score == 3.0


def test_rank_jobs_uses_message_batches_api(monkeypatch, tmp_path) -> None:
    """With 2+ ranking calls, jobs are scored via the Batches API, not sync calls."""
    from types import SimpleNamespace

    import anthropic

    import job_search.pipeline.rank as rank_mod

    _isolate_quota(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test-value-000000")

    def make_result(custom_id: str, text: str):
        message = SimpleNamespace(
            usage=SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0),
            content=[SimpleNamespace(type="text", text=text)],
        )
        return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type="succeeded", message=message))

    class FakeBatches:
        def __init__(self):
            self.created_requests = None

        def create(self, requests):
            self.created_requests = requests
            return SimpleNamespace(id="mb_test", processing_status="ended")

        def retrieve(self, batch_id):
            return SimpleNamespace(id=batch_id, processing_status="ended")

        def results(self, batch_id):
            return iter([
                make_result("rank-0", '[{"i":0,"s":9.1,"c":0.9,"r":"batch A","k":[]}]'),
                make_result("rank-1", '[{"i":0,"s":1.2,"c":0.7,"r":"batch B","k":[]}]'),
            ])

        def cancel(self, batch_id):
            raise AssertionError("batch should not be cancelled in this test")

    def fail_sync_create(**kwargs):
        raise AssertionError("sync messages.create must not be called on the batch path")

    fake_batches = FakeBatches()
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(batches=fake_batches, create=fail_sync_create)
    )
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: fake_client)

    rec_a = _named_record("a1", "Python Developer", "Example Ltd")
    rec_b = _named_record("b1", "Data Engineer", "Other Ltd")

    rank_mod.rank_jobs(
        [rec_a, rec_b],
        _PROFILE,
        # batch_size 1 forces two ranking calls → batch API path engages
        {"models": {"rank": {"batch_size": 1, "use_batch_api": True}}},
    )

    assert rec_a.fit_score == 9.1 and rec_a.fit_reason == "batch A"
    assert rec_b.fit_score == 1.2 and rec_b.fit_reason == "batch B"
    assert rec_a.freshly_ranked and rec_b.freshly_ranked

    assert len(fake_batches.created_requests) == 2
    params = fake_batches.created_requests[0]["params"]
    assert params["thinking"] == {"type": "disabled"}
    assert "Python Developer" in params["messages"][0]["content"]
