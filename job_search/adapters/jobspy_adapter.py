"""JobSpy adapter — wraps python-jobspy for LinkedIn/Indeed/Glassdoor/Google (built in Phase 5).

JobSpy is off by default in sources.yaml because bot detection on these sites makes results
inconsistent. Enable when structured APIs aren't producing enough volume.
"""

from __future__ import annotations

from job_search.adapters.base import Adapter, JobRecord, RawJob


class JobSpyAdapter(Adapter):
    """Wraps the python-jobspy library. Off by default; enable in sources.yaml."""

    name = "jobspy"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        raise NotImplementedError("JobSpyAdapter.fetch — built in Phase 5")

    def normalise(self, raw: RawJob) -> JobRecord:
        raise NotImplementedError("JobSpyAdapter.normalise — built in Phase 5")
