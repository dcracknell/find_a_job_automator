"""TES (Times Educational Supplement) Jobs adapter — education domain (built in Phase 5)."""

from __future__ import annotations

from job_search.adapters.base import Adapter, JobRecord, RawJob


class TESAdapter(Adapter):
    """Fetches roles from tes.com/jobs. Referenced by education domain pack."""

    name = "tes"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        raise NotImplementedError("TESAdapter.fetch — built in Phase 5")

    def normalise(self, raw: RawJob) -> JobRecord:
        raise NotImplementedError("TESAdapter.normalise — built in Phase 5")
