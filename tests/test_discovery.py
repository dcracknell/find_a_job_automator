"""Tests for job-discovery improvements: query rotation/stats, freshness-first
fetching, and the new keyless sources (HN hiring, Remotive, Arbeitnow)."""

from __future__ import annotations

import sqlite3
from datetime import date

# ---------------------------------------------------------------------------
# Query rotation
# ---------------------------------------------------------------------------


def test_rotate_queries_prefers_untried_then_lru() -> None:
    from job_search.profile.queries import _rotate_queries

    pool = ["a", "b", "c", "d", "e", "f"]
    stats = [
        {"query": "a", "last_used": "2026-06-30", "jobs_new": 0},
        {"query": "b", "last_used": "2026-06-01", "jobs_new": 0},
        # c, d never tried
        {"query": "e", "last_used": "2026-06-15", "jobs_new": 3},  # proven
        {"query": "f", "last_used": "2026-06-29", "jobs_new": 0},
    ]
    picked = _rotate_queries(pool, stats, 4)
    # Proven producer kept, never-tried queries next, then least-recently-used
    assert picked[0] == "e"
    assert picked[1:3] == ["c", "d"]
    assert picked[3] == "b"  # LRU beats the recently-tried a/f


def test_rotate_queries_without_stats_takes_pool_head() -> None:
    from job_search.profile.queries import _rotate_queries

    assert _rotate_queries(["a", "b", "c"], None, 2) == ["a", "b"]


def test_query_prompt_includes_history_and_exploration_rule() -> None:
    from job_search.profile.queries import _build_query_prompt

    stats = [
        {"query": "fpga engineer", "last_used": "2026-06-30", "jobs_new": 5},
        {"query": "dead query", "last_used": "2026-06-30", "jobs_new": 0},
    ]
    prompt = _build_query_prompt({}, "", ["fallback"], 40, stats)
    assert "fpga engineer" in prompt          # productive list
    assert "dead query" in prompt             # recent list
    assert "NEW phrasings" in prompt          # exploration instruction


# ---------------------------------------------------------------------------
# query_stats persistence
# ---------------------------------------------------------------------------


def _conn() -> sqlite3.Connection:
    from job_search.storage.db import migrate
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn=conn)
    return conn


def test_query_stats_roundtrip_and_accumulation() -> None:
    from job_search.pipeline.query_stats import load_query_stats, record_query_stats

    conn = _conn()
    record_query_stats(
        conn, ["q1", "q2"], {"q1": 10}, {"q1": 3}, today=date(2026, 7, 1)
    )
    record_query_stats(
        conn, ["q1"], {"q1": 4}, {}, today=date(2026, 7, 2)
    )
    stats = {s["query"]: s for s in load_query_stats(conn)}
    assert stats["q1"]["times_used"] == 2
    assert stats["q1"]["jobs_seen"] == 14
    assert stats["q1"]["jobs_new"] == 3
    assert stats["q1"]["last_used"] == "2026-07-02"
    assert stats["q1"]["first_used"] == "2026-07-01"
    # zero-hit queries are still recorded so they rotate out
    assert stats["q2"]["times_used"] == 1
    assert stats["q2"]["jobs_seen"] == 0
    conn.close()


# ---------------------------------------------------------------------------
# Freshness-first fetching
# ---------------------------------------------------------------------------


def test_adzuna_requests_recent_jobs_sorted_by_date(monkeypatch) -> None:
    from job_search.adapters import adzuna as adz

    captured: list[dict] = []

    class _Resp:
        @staticmethod
        def json():
            return {"results": []}

    def fake_get(url, *, params=None, **kwargs):
        captured.append(params or {})
        return _Resp()

    monkeypatch.setattr(adz.http, "get", fake_get)
    adapter = adz.AdzunaAdapter()
    adapter._app_id, adapter._app_key = "id", "key"
    adapter.fetch(
        ["query"],
        {"apis": {"adzuna": {}}, "_profile": {"filters": {"max_days_since_posted": 14}}},
    )
    assert captured, "no request made"
    assert captured[0]["max_days_old"] == 14
    assert captured[0]["sort_by"] == "date"


def test_reed_skips_detail_fetch_for_known_urls(monkeypatch) -> None:
    from job_search.adapters import reed as reed_mod

    detail_urls: list[str] = []

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    search_payload = {
        "results": [
            {"jobId": 1, "jobTitle": "Graduate Engineer", "jobUrl": "https://reed/known"},
            {"jobId": 2, "jobTitle": "Graduate Engineer", "jobUrl": "https://reed/new"},
        ]
    }

    def fake_get(url, *, params=None, headers=None, **kwargs):
        if "/jobs/" in url:
            detail_urls.append(url)
            return _Resp({"jobDescription": "full JD"})
        return _Resp(search_payload)

    monkeypatch.setattr(reed_mod.http, "get", fake_get)
    monkeypatch.setenv("REED_API_KEY", "test-key")

    adapter = reed_mod.ReedAdapter()
    raw = adapter.fetch(
        ["q"],
        {
            "apis": {"reed": {"results_per_query": 2}},
            "_profile": {},
            "_known_job_urls": {"https://reed/known"},
        },
    )
    assert len(raw) == 2
    assert len(detail_urls) == 1  # only the unknown job got a detail request
    assert detail_urls[0].endswith("/jobs/2")


def test_sync_does_not_clobber_description_with_empty(monkeypatch) -> None:
    from job_search.pipeline.dedup import sync_job
    from tests.test_dedup import _conn as dedup_conn
    from tests.test_dedup import _record

    conn = dedup_conn()
    sync_job(conn, _record(description="Full JD", jd_content_hash="hash-full"))
    # Re-scrape without a detail fetch: empty description, different hash
    empty = _record(description="", jd_content_hash="hash-empty", freshly_ranked=False)
    assert sync_job(conn, empty) == "updated_meta"
    row = conn.execute("SELECT description FROM jobs WHERE job_id='job-1'").fetchone()
    assert row["description"] == "Full JD"
    conn.close()


# ---------------------------------------------------------------------------
# New sources
# ---------------------------------------------------------------------------


def test_parse_hiring_comment_pipe_format() -> None:
    from job_search.adapters.hn_hiring import parse_hiring_comment

    parsed = parse_hiring_comment(
        "Acme Robotics | Embedded Engineer | London, UK or Remote | £45-60k<p>"
        "We build robots. Stack: C++, STM32, RTOS."
    )
    assert parsed == {
        "company": "Acme Robotics",
        "title": "Embedded Engineer",
        "location": "London, UK or Remote",
    }
    # Non-conforming comments are rejected
    assert parse_hiring_comment("Great thread, thanks!") is None
    assert parse_hiring_comment("") is None


def test_remotive_normalise_and_remote_ok_gate(monkeypatch) -> None:
    import job_search.pipeline.normalise as norm
    monkeypatch.setattr(norm, "geocode", lambda loc: None)

    from job_search.adapters.remotive import RemotiveAdapter

    adapter = RemotiveAdapter()
    # remote_ok=false → adapter refuses to fetch at all
    assert adapter.fetch(["q"], {"_profile": {"remote_ok": False}}) == []

    record = adapter.normalise(
        {
            "title": "Backend Engineer",
            "company_name": "RemoteCo",
            "url": "https://remotive.com/jobs/1",
            "candidate_required_location": "UK",
            "description": "<p>Python backend role.</p>",
            "publication_date": "2026-06-28T00:00:00",
            "salary": "£50k",
        }
    )
    assert record is not None
    assert record.company == "RemoteCo"
    assert "Remote" in record.location
    assert record.posted_date == date(2026, 6, 28)


def test_arbeitnow_normalise_and_filtering(monkeypatch) -> None:
    import job_search.pipeline.normalise as norm
    monkeypatch.setattr(norm, "geocode", lambda loc: None)

    from job_search.adapters import arbeitnow as arb

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    payload = {
        "data": [
            {"slug": "a", "title": "Engineer", "company_name": "DE Co",
             "location": "Berlin", "remote": False, "url": "https://x/a",
             "description": "d", "created_at": 1750000000},
            {"slug": "b", "title": "Engineer", "company_name": "UK Co",
             "location": "London", "remote": False, "url": "https://x/b",
             "description": "d", "created_at": 1750000000},
            {"slug": "c", "title": "Engineer", "company_name": "Remote Co",
             "location": "Anywhere", "remote": True, "url": "https://x/c",
             "description": "d", "created_at": 1750000000},
        ]
    }
    def fake_get(url, *, params=None, **kw):
        return _Resp(payload if params.get("page") == 1 else {"data": []})

    monkeypatch.setattr(arb.http, "get", fake_get)

    adapter = arb.ArbeitnowAdapter()
    raw = adapter.fetch([], {"_profile": {"remote_ok": True}})
    assert {j["slug"] for j in raw} == {"b", "c"}  # Berlin on-site dropped

    record = adapter.normalise(raw[1] if raw[0]["slug"] == "b" else raw[0])
    assert record is not None
    assert record.location.startswith("Remote")
