"""HN Who is Hiring adapter — scrapes monthly HN thread (built in Phase 5).

Scrapes https://news.ycombinator.com/item?id=<monthly-thread-id> and filters
for UK + relevant tech. Thread ID changes monthly; lookup via HN Algolia API.
"""

from __future__ import annotations

from job_search.adapters.base import Adapter, JobRecord, RawJob


class HNHiringAdapter(Adapter):
    """Scrapes the monthly 'Ask HN: Who is Hiring?' thread on Hacker News."""

    name = "hn_hiring"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        raise NotImplementedError("HNHiringAdapter.fetch — built in Phase 5")

    def normalise(self, raw: RawJob) -> JobRecord:
        raise NotImplementedError("HNHiringAdapter.normalise — built in Phase 5")
