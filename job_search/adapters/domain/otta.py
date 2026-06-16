"""Otta adapter — tech, creative, and product roles (built in Phase 5)."""

from __future__ import annotations

from job_search.adapters.base import Adapter, JobRecord, RawJob


class OttaAdapter(Adapter):
    """Fetches roles from app.otta.com. Referenced by engineering and creative domain packs."""

    name = "otta"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        raise NotImplementedError("OttaAdapter.fetch — built in Phase 5")

    def normalise(self, raw: RawJob) -> JobRecord:
        raise NotImplementedError("OttaAdapter.normalise — built in Phase 5")
