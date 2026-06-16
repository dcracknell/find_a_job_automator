"""FindAPhD adapter — science/academia domain (built in Phase 5)."""

from __future__ import annotations

from job_search.adapters.base import Adapter, JobRecord, RawJob


class FindAPhDAdapter(Adapter):
    """Fetches PhD studentships and postdoc roles from findaphd.com."""

    name = "findaphd"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        raise NotImplementedError("FindAPhDAdapter.fetch — built in Phase 5")

    def normalise(self, raw: RawJob) -> JobRecord:
        raise NotImplementedError("FindAPhDAdapter.normalise — built in Phase 5")
