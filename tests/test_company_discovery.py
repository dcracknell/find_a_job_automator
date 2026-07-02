"""Tests for automatic career-site discovery and the direct-source adapters."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path


def _conn() -> sqlite3.Connection:
    from job_search.storage.db import migrate
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn=conn)
    return conn


def _insert_job(conn, company: str, fit: float, source: str = "adzuna") -> None:
    conn.execute(
        """
        INSERT INTO jobs (job_id, source, first_seen, last_seen, status,
                          title, company, url, fit_score)
        VALUES (?, ?, ?, ?, 'new', 'Graduate Engineer', ?, ?, ?)
        """,
        (
            f"id-{company}-{source}", source,
            date.today().isoformat(), date.today().isoformat(),
            company, f"https://example.com/{company}", fit,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Slug candidates + probing
# ---------------------------------------------------------------------------


def test_slug_candidates_strip_legal_suffixes() -> None:
    from job_search.discovery import slug_candidates

    assert slug_candidates("Thought Machine Ltd") == [
        "thoughtmachine", "thought-machine", "thought",
    ]
    assert slug_candidates("Graphcore") == ["graphcore"]
    assert slug_candidates("ACME Robotics PLC")[0] == "acmerobotics"
    assert slug_candidates("") == []


def test_probe_company_returns_first_provider_with_jobs(monkeypatch) -> None:
    from job_search import discovery

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        if "ashbyhq.com" in url and "acme" in url:
            return _Resp({"jobs": [{"title": "Engineer"}]})
        raise RuntimeError("404")

    monkeypatch.setattr(discovery.http, "get", fake_get)
    assert discovery.probe_company("Acme") == ("ashby", "acme", 1)
    # empty boards / all-404 => None
    monkeypatch.setattr(
        discovery.http, "get",
        lambda url, **kw: (_ for _ in ()).throw(RuntimeError("404")),
    )
    assert discovery.probe_company("Nowhere Co") is None


# ---------------------------------------------------------------------------
# discovered_sources.yaml round-trip + merge
# ---------------------------------------------------------------------------


def test_add_and_merge_discovered_sources(tmp_path: Path) -> None:
    from job_search.discovery import add_discovered_company, merge_discovered_sources

    path = tmp_path / "discovered.yaml"
    assert add_discovered_company("ashby", "Acme", "acme", path=path) is True
    assert add_discovered_company("ashby", "Acme", "acme", path=path) is False  # dedupe

    sources = {"ats": {"ashby": {"companies": [{"name": "Other", "slug": "other"}]}}}
    merged = merge_discovered_sources(sources, path=path)
    slugs = {c["slug"] for c in merged["ats"]["ashby"]["companies"]}
    assert slugs == {"other", "acme"}


def test_discover_from_db_probes_only_new_promising_companies(
    monkeypatch, tmp_path: Path
) -> None:
    from job_search import discovery

    conn = _conn()
    _insert_job(conn, "Acme Robotics", fit=8.0)                # promising, unprobed
    _insert_job(conn, "Low Fit Co", fit=2.0)                   # below threshold
    _insert_job(conn, "Configured Co", fit=9.0)                # already configured
    _insert_job(conn, "ATS Sourced", fit=9.0, source="greenhouse:x")  # already direct

    probed: list[str] = []

    def fake_probe(company):
        probed.append(company)
        return ("recruitee", "acmerobotics", 4)

    monkeypatch.setattr(discovery, "probe_company", fake_probe)

    sources_cfg = {"ats": {"lever": {"companies": [{"name": "Configured Co", "slug": "cc"}]}}}
    found = discovery.discover_from_db(
        conn, sources_cfg, max_probes=5, min_fit_score=5.0,
        path=tmp_path / "discovered.yaml",
    )
    assert probed == ["Acme Robotics"]
    assert found == [("Acme Robotics", "recruitee", "acmerobotics")]

    # A second pass must not probe the same company again
    probed.clear()
    discovery.discover_from_db(
        conn, sources_cfg, max_probes=5, min_fit_score=5.0,
        path=tmp_path / "discovered.yaml",
    )
    assert probed == []
    conn.close()


# ---------------------------------------------------------------------------
# New ATS adapters — normalise mapping
# ---------------------------------------------------------------------------


def test_ashby_normalise(monkeypatch) -> None:
    import job_search.pipeline.normalise as norm
    monkeypatch.setattr(norm, "geocode", lambda loc: None)

    from job_search.adapters.ashby import AshbyAdapter

    record = AshbyAdapter().normalise(
        {
            "title": "Embedded Engineer",
            "_company_name": "Acme",
            "_slug": "acme",
            "location": "London",
            "isRemote": False,
            "jobUrl": "https://jobs.ashbyhq.com/acme/123",
            "descriptionHtml": "<p>Firmware role.</p>",
            "publishedAt": "2026-06-20T00:00:00Z",
        }
    )
    assert record is not None
    assert record.company == "Acme"
    assert record.source == "ashby:acme"
    assert record.posted_date == date(2026, 6, 20)
    assert "Firmware" in record.description


def test_recruitee_normalise(monkeypatch) -> None:
    import job_search.pipeline.normalise as norm
    monkeypatch.setattr(norm, "geocode", lambda loc: None)

    from job_search.adapters.recruitee import RecruiteeAdapter

    record = RecruiteeAdapter().normalise(
        {
            "title": "Electronics Engineer",
            "_company_name": "Acme",
            "_slug": "acme",
            "city": "Sheffield",
            "country": "United Kingdom",
            "remote": False,
            "careers_url": "https://acme.recruitee.com/o/electronics-engineer",
            "description": "<p>Design PCBs.</p>",
            "requirements": "<p>Degree required.</p>",
            "created_at": "2026-06-01 10:00:00",
        }
    )
    assert record is not None
    assert record.location == "Sheffield, United Kingdom"
    assert "PCBs" in record.description and "Degree" in record.description


def test_smartrecruiters_fetch_limits_detail_calls(monkeypatch) -> None:
    from job_search.adapters import smartrecruiters as sr

    detail_calls: list[str] = []

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    listing = {
        "content": [
            {"id": "1", "name": "Graduate Engineer", "location": {"city": "London"}},
            {"id": "2", "name": "Senior Engineer", "location": {"city": "London"}},
        ]
    }

    def fake_get(url, *, params=None, **kwargs):
        if url.endswith("/postings"):
            return _Resp(listing)
        detail_calls.append(url)
        return _Resp({"jobAd": {"sections": {"jobDescription": {"text": "JD"}}}})

    monkeypatch.setattr(sr.http, "get", fake_get)
    raw = sr.SmartRecruitersAdapter().fetch(
        [],
        {
            "ats": {"smartrecruiters": {"companies": [{"name": "Acme", "slug": "acme"}]}},
            "_profile": {"negative_signals": {"title_excludes": ["senior"]}},
            "_known_job_urls": set(),
        },
    )
    assert len(raw) == 2
    # detail fetched only for the non-excluded posting
    assert len(detail_calls) == 1 and detail_calls[0].endswith("/postings/1")
    assert raw[0]["_description"] == "JD"


# ---------------------------------------------------------------------------
# Careers-page JSON-LD extraction
# ---------------------------------------------------------------------------


_JSONLD_PAGE = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "JobPosting",
  "title": "Graduate Hardware Engineer",
  "datePosted": "2026-06-25",
  "hiringOrganization": {"@type": "Organization", "name": "Acme Devices"},
  "jobLocation": {"@type": "Place",
    "address": {"addressLocality": "Sheffield", "addressCountry": "GB"}},
  "baseSalary": {"@type": "MonetaryAmount",
    "value": {"@type": "QuantitativeValue", "minValue": 30000, "maxValue": 38000,
              "unitText": "YEAR"}},
  "description": "<p>Design digital hardware with FPGAs.</p>",
  "url": "https://acme.example/jobs/42"
}
</script>
<script type="application/ld+json">{"@type": "Organization", "name": "Acme"}</script>
</head><body>Careers</body></html>
"""


def test_extract_job_postings_from_jsonld() -> None:
    from job_search.adapters.careers_page import extract_job_postings

    jobs = extract_job_postings(_JSONLD_PAGE, fallback_company="Fallback",
                                page_url="https://acme.example/careers")
    assert len(jobs) == 1
    job = jobs[0]
    assert job["title"] == "Graduate Hardware Engineer"
    assert job["company"] == "Acme Devices"
    assert job["url"] == "https://acme.example/jobs/42"
    assert "Sheffield" in job["location"]
    assert job["created"] == "2026-06-25"
    assert job["salary_raw"] == "£30000 - £38000"


def test_extract_job_postings_handles_graph_and_garbage() -> None:
    from job_search.adapters.careers_page import extract_job_postings

    page = """
    <script type="application/ld+json">not json at all</script>
    <script type="application/ld+json">
    {"@graph": [{"@type": "JobPosting", "title": "Nurse",
                 "hiringOrganization": {"name": "Ward Co"},
                 "description": "Care role."}]}
    </script>
    """
    jobs = extract_job_postings(page, page_url="https://x/careers")
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Nurse"
    assert jobs[0]["url"] == "https://x/careers"  # falls back to page URL
