"""GOV.UK Find a Job adapter — XML feed (built in Phase 5)."""

from __future__ import annotations

from job_search.adapters.base import Adapter, JobRecord, RawJob


class GovUKAdapter(Adapter):
    """Fetches UK government jobs from the GOV.UK Find a Job XML feed."""

    name = "gov_uk_find_a_job"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        raise NotImplementedError("GovUKAdapter.fetch — built in Phase 5")

    def normalise(self, raw: RawJob) -> JobRecord:
        raise NotImplementedError("GovUKAdapter.normalise — built in Phase 5")
