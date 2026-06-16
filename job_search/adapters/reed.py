"""Reed.co.uk API adapter.

Reed API docs: https://www.reed.co.uk/developers/jobseeker
Auth: HTTP Basic with REED_API_KEY as username, empty password.
"""

from __future__ import annotations

import base64
import logging
import os

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.reed.co.uk/api/1.0/search"
_DETAIL_URL = "https://www.reed.co.uk/api/1.0/jobs/{job_id}"


class ReedAdapter(Adapter):
    """Fetches UK jobs from the Reed jobs API."""

    name = "reed"

    def _auth_header(self) -> dict:
        api_key = os.environ.get("REED_API_KEY", "")
        if not api_key:
            raise RuntimeError("REED_API_KEY must be set in .env")
        token = base64.b64encode(f"{api_key}:".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        """Fetch raw job postings for the given search queries."""
        src_settings = settings.get("apis", {}).get("reed", {})
        results_per_query = src_settings.get("results_per_query", 50)
        headers = self._auth_header()

        seen_ids: set[int] = set()
        raw_jobs: list[RawJob] = []

        for query in queries:
            skip = 0
            fetched = 0
            while fetched < results_per_query:
                take = min(100, results_per_query - fetched)
                try:
                    resp = http.get(
                        _BASE_URL,
                        params={
                            "keywords": query,
                            "locationName": "UK",
                            "resultsToTake": take,
                            "resultsToSkip": skip,
                            "fullTime": True,
                        },
                        headers=headers,
                    )
                    data = resp.json()
                except Exception as exc:
                    logger.warning("reed: fetch failed for query %r: %s", query, exc)
                    break

                results = data.get("results", [])
                if not results:
                    break

                for item in results:
                    job_id = item.get("jobId")
                    if job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)
                    # Fetch full description
                    try:
                        detail = http.get(
                            _DETAIL_URL.format(job_id=job_id),
                            headers=headers,
                        ).json()
                        item["description"] = detail.get("jobDescription", "")
                    except Exception as exc:
                        logger.debug("reed: could not fetch detail for %s: %s", job_id, exc)
                    item["matched_query"] = query
                    raw_jobs.append(item)

                fetched += len(results)
                if len(results) < take:
                    break
                skip += take

        return raw_jobs

    def normalise(self, raw: RawJob) -> JobRecord | None:
        """Convert a raw Reed response item into a normalised JobRecord."""
        salary_min = raw.get("minimumSalary")
        salary_max = raw.get("maximumSalary")
        salary_raw = None
        if salary_min or salary_max:
            parts = []
            if salary_min:
                parts.append(f"£{int(salary_min):,}")
            if salary_max:
                parts.append(f"£{int(salary_max):,}")
            salary_raw = " - ".join(parts) if len(parts) == 2 else parts[0]

        mapped: RawJob = {
            "title": raw.get("jobTitle", ""),
            "company": raw.get("employerName", ""),
            "url": raw.get("jobUrl", ""),
            "location": raw.get("locationName", ""),
            "description": raw.get("description", raw.get("jobDescription", "")),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_raw": salary_raw,
            "created": raw.get("date", ""),
            "matched_query": raw.get("matched_query"),
            "source": self.name,
        }
        return normalise(mapped, self.name)
