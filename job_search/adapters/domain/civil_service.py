"""Civil Service Jobs adapter — government domain (built in Phase 5)."""

from __future__ import annotations

from job_search.adapters.base import Adapter, JobRecord, RawJob


class CivilServiceAdapter(Adapter):
    """Fetches roles from civilservicejobs.service.gov.uk. Referenced by government domain pack."""

    name = "civil_service"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        raise NotImplementedError("CivilServiceAdapter.fetch — built in Phase 5")

    def normalise(self, raw: RawJob) -> JobRecord:
        raise NotImplementedError("CivilServiceAdapter.normalise — built in Phase 5")
