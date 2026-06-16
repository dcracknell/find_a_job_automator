"""Mandy.com adapter — creative, film, and TV production roles (built in Phase 5)."""

from __future__ import annotations

from job_search.adapters.base import Adapter, JobRecord, RawJob


class MandyAdapter(Adapter):
    """Fetches roles from mandy.com. Referenced by creative domain pack."""

    name = "mandy"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        raise NotImplementedError("MandyAdapter.fetch — built in Phase 5")

    def normalise(self, raw: RawJob) -> JobRecord:
        raise NotImplementedError("MandyAdapter.normalise — built in Phase 5")
