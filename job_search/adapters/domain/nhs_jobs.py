"""NHS Jobs / Health Jobs UK adapter — healthcare domain (built in Phase 5)."""

from __future__ import annotations

from job_search.adapters.base import Adapter, JobRecord, RawJob


class NHSJobsAdapter(Adapter):
    """Fetches roles from healthjobsuk.com. Referenced by healthcare domain pack."""

    name = "nhs_jobs"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        raise NotImplementedError("NHSJobsAdapter.fetch — built in Phase 5")

    def normalise(self, raw: RawJob) -> JobRecord:
        raise NotImplementedError("NHSJobsAdapter.normalise — built in Phase 5")
