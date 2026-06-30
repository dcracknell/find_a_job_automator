"""Tests for job_search.pipeline.filter — specifically the title-relevance logic.

The title filter must use ONLY profile["negative_signals"]["title_excludes"] as
the source of truth for hard exclusions, matched with word boundaries so that
substrings inside longer words are never false-positived.

Critical regression tests:
- Hardware/FPGA/embedded roles MUST pass through (they were previously blocked
  by the hardcoded _IRRELEVANT_TITLE_PATTERNS).
  - Roles matching a title_excludes word at a word boundary MUST be dropped.
  - Substrings that contain an excluded word as part of a longer word must NOT
    be dropped (e.g. "lead" in title_excludes must not block "leading").
    """

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from job_search.pipeline.filter import (
    _build_title_exclude_pattern,
    _title_is_relevant,
    apply_filters,
)
from job_search.adapters.base import JobRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HARDWARE_PROFILE_TITLE_EXCLUDES = [
      "senior",
      "staff",
      "principal",
      "lead",
      "head of",
      "director",
      "manager",
      "vp",
      "vice president",
      "sales",
      "retail",
      "account manager",
      "business development",
      "marketing",
      "compliance",
      "sourcing",
      "procurement",
      "legal",
      "hr",
      "recruitment",
      "office manager",
      "programme manager",
      "program manager",
      "product manager",
]


def _make_profile(title_excludes: list[str] | None = None) -> dict:
      """Return a minimal profile dict suitable for filter tests."""
      return {
          "filters": {
              "salary_floor_gbp": 0,
              "max_days_since_posted": 60,
              "exclude_companies": [],
              "rejected_company_cooldown_days": 90,
          },
          "negative_signals": {
              "title_excludes": title_excludes if title_excludes is not None else [],
              "company_blocklist": [],
          },
          "target_roles": {"core": [], "adjacent": []},
          "search_radius_miles": 100,
          "remote_ok": True,
          "location": {"lat": 53.38, "lon": -1.47},
      }


def _make_record(title: str, **kwargs) -> JobRecord:
      """Return a minimal JobRecord with the given title."""
      defaults = dict(
          job_id="test-1",
          source="test",
          company="Example Ltd",
          location="Remote",
          lat=None,
          lon=None,
          url="https://example.com/job/1",
          description="A test job.",
          posted_date=None,
          closes_on=None,
          salary_raw=None,
          salary_min=None,
          salary_max=None,
      )
      defaults.update(kwargs)
      return JobRecord(title=title, **defaults)


def _mock_conn():
      """Return a mock SQLite connection that never puts companies in cooldown."""
      conn = MagicMock()
      conn.execute.return_value.fetchone.return_value = None
      return conn


# ---------------------------------------------------------------------------
# Unit tests for _build_title_exclude_pattern
# ---------------------------------------------------------------------------

class TestBuildTitleExcludePattern:
      def test_returns_none_for_empty_list(self):
                assert _build_title_exclude_pattern([]) is None

      def test_returns_none_for_whitespace_only_entries(self):
                assert _build_title_exclude_pattern(["", "  "]) is None

      def test_returns_compiled_pattern(self):
                pat = _build_title_exclude_pattern(["senior"])
                assert pat is not None
                assert isinstance(pat, re.Pattern)

      def test_case_insensitive(self):
                pat = _build_title_exclude_pattern(["senior"])
                assert pat is not None
                assert pat.search("Senior FPGA Engineer")
                assert pat.search("SENIOR FPGA ENGINEER")
                assert pat.search("Graduate senior engineer")

      def test_word_boundary_prevents_substring_match(self):
                """'lead' in excludes must NOT match 'leading' or 'electrical'."""
                pat = _build_title_exclude_pattern(["lead"])
                assert pat is not None
                # exact word match — should be found
                assert pat.search("Lead Engineer")
                assert pat.search("Engineering Lead")
                # substring inside a longer word — must NOT be found
                assert not pat.search("Leading Engineer")
        assert not pat.search("Electrical Engineer")

    def test_multi_word_phrase_excluded(self):
              pat = _build_title_exclude_pattern(["head of"])
              assert pat is not None
              assert pat.search("Head of Engineering")
              # a title that happens to contain "head" but not "head of" is fine
              assert not pat.search("Beachhead Systems Engineer")


# ---------------------------------------------------------------------------
# Unit tests for _title_is_relevant
# ---------------------------------------------------------------------------

class TestTitleIsRelevant:

      # --- Hardware / FPGA / embedded roles that MUST pass through ---

    @pytest.mark.parametrize("title", [
              "Graduate FPGA Engineer",
              "Graduate Hardware Engineer",
              "Graduate Electronics Engineer",
              "Graduate Embedded Engineer",
              "Embedded Firmware Engineer",
              "Digital Design Engineer",
              "Junior FPGA Engineer",
              "Junior Digital Design Engineer",
              "Graduate Electrical Engineer",
              "Hardware Design Engineer",
              "ASIC Design Engineer",
              "VLSI Engineer",
              "RTL Design Engineer",
              "Verilog/VHDL Design Engineer",
              "Chip Design Engineer",
              "Silicon Validation Engineer",
              "Graduate Semiconductor Engineer",
              "RF Engineer Graduate",
              "Graduate RF Engineer",
              "Graduate Photonics Engineer",
              "PCB Design Engineer",
              "DSP Engineer",
              "Firmware Developer",
              "Embedded Systems Engineer",
              "IoT Engineer",
              "Graduate Systems Engineer",
              "Graduate Electronics Technician",
              "Graduate Avionics Engineer",
    ])
    def test_hardware_roles_pass(self, title):
              """Hardware/FPGA/embedded roles must NEVER be filtered out."""
              pattern = _build_title_exclude_pattern(_HARDWARE_PROFILE_TITLE_EXCLUDES)
              assert _title_is_relevant(title, pattern), (
                  f"Title '{title}' was unexpectedly blocked. "
                  "Check that hardware/FPGA/embedded keywords are not in title_excludes."
              )

    # --- Roles matching title_excludes that MUST be blocked ---

    @pytest.mark.parametrize("title", [
              "Senior FPGA Engineer",
              "Senior Hardware Engineer",
              "Senior Embedded Engineer",
              "Lead FPGA Engineer",
              "Principal Hardware Engineer",
              "Staff Engineer",
              "Director of Engineering",
              "VP Engineering",
              "Head of Hardware",
              "Engineering Manager",
              "Programme Manager FPGA",
              "Program Manager Hardware",
              "Product Manager Electronics",
              "Sales Engineer",
              "Marketing Manager",
              "Recruitment Consultant",
              "HR Manager",
              "Compliance Officer",
              "Sourcing Manager",
              "Procurement Engineer",
              "Legal Counsel",
    ])
    def test_excluded_titles_are_blocked(self, title):
              """Titles containing a word from title_excludes must be filtered out."""
              pattern = _build_title_exclude_pattern(_HARDWARE_PROFILE_TITLE_EXCLUDES)
              assert not _title_is_relevant(title, pattern), (
                  f"Title '{title}' was unexpectedly allowed through."
              )

    def test_no_excludes_allows_everything(self):
              """Empty title_excludes list means all titles pass."""
              pattern = _build_title_exclude_pattern([])
              for title in ["Senior CEO", "VP of Sales", "Recruitment Manager"]:
                            assert _title_is_relevant(title, pattern)

          def test_none_pattern_allows_everything(self):
                    """None pattern (no excludes configured) means all titles pass."""
                    assert _title_is_relevant("Whatever Title", None)
                    assert _title_is_relevant("", None)

    def test_word_boundary_lead_in_electrical(self):
              """'lead' in excludes must not block 'Electrical Engineer'."""
              pattern = _build_title_exclude_pattern(["lead"])
              assert _title_is_relevant("Electrical Engineer", pattern)
              assert _title_is_relevant("Graduate Electronics Engineer", pattern)
              # but "Lead Engineer" should be blocked
              assert not _title_is_relevant("Lead Engineer", pattern)

    def test_word_boundary_senior_in_engineering(self):
              """'senior' in excludes must not block 'Engineering' (no match)."""
              pattern = _build_title_exclude_pattern(["senior"])
              assert _title_is_relevant("Engineering Graduate", pattern)
              assert not _title_is_relevant("Senior Engineer", pattern)


# ---------------------------------------------------------------------------
# Integration tests for apply_filters
# ---------------------------------------------------------------------------

class TestApplyFilters:

      def _run(self, records, title_excludes=None):
                profile = _make_profile(title_excludes=title_excludes or _HARDWARE_PROFILE_TITLE_EXCLUDES)
                conn = _mock_conn()
                from datetime import date
                return apply_filters(records, profile, conn, today=date(2026, 6, 30))

    def test_graduate_fpga_engineer_passes(self):
              """Critical regression: 'Graduate FPGA Engineer' must never be dropped."""
              rec = _make_record("Graduate FPGA Engineer")
              result = self._run([rec])
              assert len(result) == 1
              assert result[0].title == "Graduate FPGA Engineer"

    def test_embedded_firmware_engineer_passes(self):
              rec = _make_record("Embedded Firmware Engineer")
              result = self._run([rec])
              assert len(result) == 1

    def test_senior_fpga_engineer_blocked(self):
              """'Senior FPGA Engineer' must be dropped when 'senior' is in title_excludes."""
              rec = _make_record("Senior FPGA Engineer")
              result = self._run([rec])
              assert len(result) == 0

    def test_lead_engineer_blocked(self):
              rec = _make_record("Lead Hardware Engineer")
              result = self._run([rec])
              assert len(result) == 0

    def test_electrical_engineer_not_blocked_by_lead(self):
              """'lead' in title_excludes must NOT block 'Electrical Engineer'."""
              rec = _make_record("Electrical Engineer")
              result = self._run([rec], title_excludes=["lead"])
              assert len(result) == 1

    def test_multiple_records_mixed(self):
              """Some roles pass, some are blocked."""
              records = [
                  _make_record("Graduate FPGA Engineer"),
                  _make_record("Senior FPGA Engineer"),
                  _make_record("Embedded Firmware Engineer"),
                  _make_record("Lead Hardware Engineer"),
                  _make_record("Junior Digital Design Engineer"),
              ]
              result = self._run(records)
              passed_titles = {r.title for r in result}
              assert "Graduate FPGA Engineer" in passed_titles
              assert "Embedded Firmware Engineer" in passed_titles
              assert "Junior Digital Design Engineer" in passed_titles
              assert "Senior FPGA Engineer" not in passed_titles
              assert "Lead Hardware Engineer" not in passed_titles

    def test_salary_filter_still_works(self):
              """Salary filtering must not be broken by the title filter changes."""
              profile = _make_profile(title_excludes=[])
              profile["filters"]["salary_floor_gbp"] = 30000
              records = [
                  _make_record("Graduate FPGA Engineer", salary_max=20000.0),
                  _make_record("Graduate Hardware Engineer", salary_max=35000.0),
                  _make_record("Embedded Engineer", salary_max=None),  # null kept
              ]
              conn = _mock_conn()
              from datetime import date
              result = apply_filters(records, profile, conn, today=date(2026, 6, 30))
              passed_titles = {r.title for r in result}
              assert "Graduate FPGA Engineer" not in passed_titles
              assert "Graduate Hardware Engineer" in passed_titles
              assert "Embedded Engineer" in passed_titles

    def test_empty_records_returns_empty(self):
              result = self._run([])
              assert result == []
