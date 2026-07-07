"""Tests for the jobs.ac.uk adapter — parsing, normalisation, fetch strategy.

Fixture HTML mirrors the real markup (verified against live pages 2026-07-07):
search results use div.j-search-result__result blocks, detail pages carry the
JD in #job-description plus a th/td details table with full dates.
"""

from __future__ import annotations

from datetime import date

from job_search.adapters.jobs_ac_uk import JobsAcUkAdapter, _parse_full_date

_SEARCH_HTML = """
<div id="job-listings">
  <div class="j-search-result__result ie-border-left" data-advert-id="1080287">
    <div class="j-search-result__text">
      <a href="/job/DSC125/phd-studentship-fpga">
        PhD Studentship: FPGA Acceleration
      </a>
      <div class="j-search-result__department">Electronic &amp; Electrical Engineering</div>
      <div class="j-search-result__employer"><b>University of Bath</b></div>
      <div>Location:
        Bath
      </div>
      <div class="j-search-result__info">
        <strong>Salary: </strong>
        £20,000
        stipend
      </div>
      <div><strong>Date Placed: </strong>01 Jul</div>
    </div>
  </div>
  <div class="j-search-result__result" data-advert-id="1080288">
    <div class="j-search-result__text">
      <a href="/job/DRZ446/research-software-engineer">
        Research Software Engineer
      </a>
      <div class="j-search-result__employer"><b>UCL</b></div>
      <div>Location: London, Hybrid</div>
    </div>
  </div>
</div>
"""

_DETAIL_HTML = """
<div class="j-advert-details__container">
  <table>
    <tr><th class="j-advert-details__table-header">Location:</th><td>London, Hybrid</td></tr>
    <tr><th class="j-advert-details__table-header">Salary:</th><td>£54,931 to £64,644</td></tr>
    <tr><th class="j-advert-details__table-header">Hours:</th><td>Full Time</td></tr>
  </table>
  <table>
    <tr><th class="j-advert-details__table-header">Placed On:</th><td>22nd June 2026</td></tr>
    <tr><th class="j-advert-details__table-header">Closes:</th><td>19th July 2026</td></tr>
    <tr><th class="j-advert-details__table-header">Job Ref:</th><td>B04-07554</td></tr>
  </table>
</div>
<div id="job-description" class="row-8">
  <p><strong>About us</strong></p>
  <p>Build data acquisition systems with FPGA and Python.</p>
</div>
"""


class TestParseFullDate:
    def test_ordinal_dates(self):
        assert _parse_full_date("22nd June 2026") == date(2026, 6, 22)
        assert _parse_full_date("1st January 2027") == date(2027, 1, 1)
        assert _parse_full_date("3rd March 2026") == date(2026, 3, 3)
        assert _parse_full_date("15 July 2026") == date(2026, 7, 15)

    def test_garbage_returns_none(self):
        assert _parse_full_date("") is None
        assert _parse_full_date("soon") is None
        assert _parse_full_date("07 Jul") is None  # search-page short form


class TestParseSearchPage:
    def test_extracts_listing_fields(self):
        listings = JobsAcUkAdapter._parse_search_page(_SEARCH_HTML)
        assert len(listings) == 2

        first = listings[0]
        assert first["title"] == "PhD Studentship: FPGA Acceleration"
        assert first["url"] == "https://www.jobs.ac.uk/job/DSC125/phd-studentship-fpga"
        assert first["company"] == "University of Bath"
        assert first["department"] == "Electronic & Electrical Engineering"
        assert first["location"] == "Bath"
        assert first["salary_raw"] == "£20,000 stipend"

        second = listings[1]
        assert second["company"] == "UCL"
        assert second["location"] == "London, Hybrid"
        assert second["salary_raw"] is None

    def test_empty_page(self):
        assert JobsAcUkAdapter._parse_search_page("<html><body></body></html>") == []


class TestParseDetailPage:
    def test_extracts_jd_and_dated_fields(self):
        detail = JobsAcUkAdapter._parse_detail_page(_DETAIL_HTML)
        assert "FPGA and Python" in detail["description"]
        assert detail["location"] == "London, Hybrid"
        assert detail["salary_raw"] == "£54,931 to £64,644"
        assert detail["created"] == "2026-06-22"
        assert detail["closes"] == "2026-07-19"


class TestNormalise:
    def test_maps_to_job_record_with_closes_fallback(self):
        adapter = JobsAcUkAdapter()
        raw = {
            "title": "PhD Studentship: FPGA Acceleration",
            "company": "University of Bath",
            "url": "https://www.jobs.ac.uk/job/DSC125/phd-studentship-fpga",
            "location": "Bath",
            "department": "Electronic & Electrical Engineering",
            "description": "<p>Verilog and Vivado work on FPGA accelerators.</p>",
            "salary_raw": "£20,000 stipend",
            "created": "2026-06-22",
            "closes": "2026-07-19",
            "matched_query": "PhD Studentship FPGA",
        }
        rec = adapter.normalise(raw)
        assert rec is not None
        assert rec.source == "jobs_ac_uk"
        assert rec.title == "PhD Studentship: FPGA Acceleration"
        assert rec.company == "University of Bath"
        assert rec.posted_date == date(2026, 6, 22)
        # No closing date in the JD text — the listing's Closes field is used.
        assert rec.closes_on == date(2026, 7, 19)
        assert "Department: Electronic & Electrical Engineering" in rec.description
        assert "Verilog" in rec.description
        assert rec.matched_query == "PhD Studentship FPGA"

    def test_missing_required_fields_returns_none(self):
        adapter = JobsAcUkAdapter()
        assert adapter.normalise({"title": "X", "company": "", "url": ""}) is None


class TestFetchStrategy:
    """Detail fetches are budgeted and skipped for known/excluded jobs."""

    def _run_fetch(self, monkeypatch, settings, search_html=_SEARCH_HTML):
        from job_search.adapters import jobs_ac_uk as mod

        detail_urls: list[str] = []

        class FakeResponse:
            def __init__(self, text):
                self.text = text

        def fake_get(url, params=None, **kwargs):
            if params and "keywords" in params:
                return FakeResponse(search_html)
            detail_urls.append(url)
            return FakeResponse(_DETAIL_HTML)

        monkeypatch.setattr(mod.http, "get", fake_get)
        adapter = JobsAcUkAdapter()
        raw_jobs = adapter.fetch(["fpga"], settings)
        return raw_jobs, detail_urls

    def test_new_jobs_get_detail_fetch(self, monkeypatch):
        raw_jobs, detail_urls = self._run_fetch(monkeypatch, {})
        assert len(raw_jobs) == 2
        assert len(detail_urls) == 2
        # Detail data merged into the search stub
        assert raw_jobs[0]["closes"] == "2026-07-19"
        assert raw_jobs[0]["matched_query"] == "fpga"

    def test_known_urls_skip_detail(self, monkeypatch):
        settings = {
            "_known_job_urls": {
                "https://www.jobs.ac.uk/job/DSC125/phd-studentship-fpga"
            }
        }
        raw_jobs, detail_urls = self._run_fetch(monkeypatch, settings)
        # Both jobs returned (known one touches last_seen), one detail fetch
        assert len(raw_jobs) == 2
        assert detail_urls == [
            "https://www.jobs.ac.uk/job/DRZ446/research-software-engineer"
        ]
        known = raw_jobs[0]
        assert "description" not in known  # stored JD survives via sync_job

    def test_title_excluded_jobs_skip_detail(self, monkeypatch):
        settings = {
            "_profile": {
                "negative_signals": {"title_excludes": ["research software engineer"]}
            }
        }
        raw_jobs, detail_urls = self._run_fetch(monkeypatch, settings)
        assert len(raw_jobs) == 2
        assert detail_urls == [
            "https://www.jobs.ac.uk/job/DSC125/phd-studentship-fpga"
        ]

    def test_exhausted_budget_defers_new_jobs(self, monkeypatch):
        settings = {"aggregators": {"jobs_ac_uk": {"max_detail_fetches": 1}}}
        raw_jobs, detail_urls = self._run_fetch(monkeypatch, settings)
        # Only the first new job is fetched+returned; the second is deferred
        # entirely so a later run inserts it with a full JD.
        assert len(detail_urls) == 1
        assert len(raw_jobs) == 1
        assert raw_jobs[0]["url"].endswith("/job/DSC125/phd-studentship-fpga")
