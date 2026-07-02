"""Remotive adapter — free, keyless API for remote jobs.

https://remotive.com/api/remote-jobs?search=...&limit=N
Only runs when the profile has remote_ok=true; results are filtered to
locations the candidate can actually take (UK/Europe/worldwide by default).
"""

from __future__ import annotations

import logging

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http

logger = logging.getLogger(__name__)

_BASE_URL = "https://remotive.com/api/remote-jobs"

_DEFAULT_LOCATION_TOKENS = [
    "worldwide", "anywhere", "uk", "united kingdom", "europe", "emea",
]

# Remotive is one endpoint per search term; cap how many queries we spend on it
_MAX_QUERIES = 8


class RemotiveAdapter(Adapter):
    """Fetches remote jobs from the Remotive public API."""

    name = "remotive"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        profile = settings.get("_profile", {})
        if not profile.get("remote_ok", True):
            logger.debug("remotive: profile has remote_ok=false, skipping")
            return []

        cfg = settings.get("aggregators", {}).get("remotive", {})
        tokens = [t.lower() for t in cfg.get("locations", _DEFAULT_LOCATION_TOKENS)]
        limit = int(cfg.get("results_per_query", 50))

        seen_urls: set[str] = set()
        raw_jobs: list[RawJob] = []

        for query in queries[:_MAX_QUERIES]:
            try:
                resp = http.get(_BASE_URL, params={"search": query, "limit": limit})
                jobs = resp.json().get("jobs", [])
            except Exception as exc:
                logger.warning("remotive: fetch failed for %r: %s", query, exc)
                continue

            for job in jobs:
                url = (job.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                location = (job.get("candidate_required_location") or "").lower()
                if location and not any(tok in location for tok in tokens):
                    continue
                seen_urls.add(url)
                job["matched_query"] = query
                raw_jobs.append(job)

        logger.info("remotive: %d jobs across %d queries",
                    len(raw_jobs), min(len(queries), _MAX_QUERIES))
        return raw_jobs

    def normalise(self, raw: RawJob) -> JobRecord | None:
        mapped: RawJob = {
            "title": raw.get("title", ""),
            "company": raw.get("company_name", ""),
            "url": raw.get("url", ""),
            "location": f"Remote ({raw.get('candidate_required_location') or 'Worldwide'})",
            "description": raw.get("description", ""),
            "salary_raw": raw.get("salary") or None,
            "created": (raw.get("publication_date") or "")[:10],
            "matched_query": raw.get("matched_query"),
            "source": self.name,
        }
        return normalise(mapped, self.name)

    def healthcheck(self) -> tuple[bool, str | None]:
        try:
            http.get(_BASE_URL, params={"limit": 1}).json()
            return True, None
        except Exception as exc:
            return False, str(exc)
