"""CharityJob adapter — cross-domain non-profit roles (built in Phase 5)."""

from __future__ import annotations

from job_search.adapters.base import Adapter, JobRecord, RawJob


class CharityJobAdapter(Adapter):
    """Fetches roles from charityjob.co.uk. Cross-domain; referenced by multiple packs."""

    name = "charityjob"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        raise NotImplementedError("CharityJobAdapter.fetch — built in Phase 5")

    def normalise(self, raw: RawJob) -> JobRecord:
        raise NotImplementedError("CharityJobAdapter.normalise — built in Phase 5")
