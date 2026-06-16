"""Caterer.com adapter — hospitality domain (built in Phase 5)."""

from __future__ import annotations

from job_search.adapters.base import Adapter, JobRecord, RawJob


class CatererAdapter(Adapter):
    """Fetches roles from caterer.com. Referenced by hospitality domain pack."""

    name = "caterer"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        raise NotImplementedError("CatererAdapter.fetch — built in Phase 5")

    def normalise(self, raw: RawJob) -> JobRecord:
        raise NotImplementedError("CatererAdapter.normalise — built in Phase 5")
