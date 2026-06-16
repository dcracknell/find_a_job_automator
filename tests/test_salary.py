"""Tests for salary parsing (built in Phase 2).

Salary parsing is one of the "most likely to break silently" areas — test thoroughly.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="parse_salary not yet implemented (Phase 2)")
class TestAnnualSalary:
    def test_gbp_k_notation(self) -> None:
        from job_search.util.salary import parse_salary
        assert parse_salary("£35k") == (35000, 35000)

    def test_range_notation(self) -> None:
        from job_search.util.salary import parse_salary
        assert parse_salary("£35,000 - £40,000") == (35000, 40000)

    def test_sentinel_competitive(self) -> None:
        from job_search.util.salary import parse_salary
        assert parse_salary("Competitive") == (None, None)

    def test_sentinel_doe(self) -> None:
        from job_search.util.salary import parse_salary
        assert parse_salary("DOE") == (None, None)


@pytest.mark.skip(reason="parse_salary not yet implemented (Phase 2)")
class TestHourlySalary:
    def test_hourly_rate(self) -> None:
        from job_search.util.salary import parse_salary
        min_ann, max_ann = parse_salary("£18/hr")
        assert min_ann == pytest.approx(18 * 1880, rel=0.01)

    def test_hourly_per_hour(self) -> None:
        from job_search.util.salary import parse_salary
        min_ann, _ = parse_salary("£18 per hour")
        assert min_ann == pytest.approx(18 * 1880, rel=0.01)


@pytest.mark.skip(reason="parse_salary not yet implemented (Phase 2)")
class TestNHSBands:
    def test_band_5(self) -> None:
        from job_search.util.salary import parse_salary
        min_ann, max_ann = parse_salary(
            "Band 5",
            domain_pack={"salary": {"agenda_for_change_bands": True}},
        )
        assert min_ann is not None
        assert min_ann > 20000

    def test_band_6(self) -> None:
        from job_search.util.salary import parse_salary
        min_ann, _ = parse_salary(
            "Band 6",
            domain_pack={"salary": {"agenda_for_change_bands": True}},
        )
        assert min_ann is not None
        assert min_ann > 35000
