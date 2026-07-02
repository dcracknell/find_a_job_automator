"""Greenhouse ATS adapter — generic, one-line YAML to add a company.

Endpoint: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
Public API, no authentication required.
"""

from __future__ import annotations

import logging

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http

logger = logging.getLogger(__name__)

_BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


class GreenhouseAdapter(Adapter):
    """Generic Greenhouse ATS adapter. Companies configured in sources.yaml."""

    name = "greenhouse"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        """Fetch all jobs from all configured Greenhouse companies."""
        companies = settings.get("ats", {}).get("greenhouse", {}).get("companies", [])
        raw_jobs: list[RawJob] = []

        for company in companies:
            slug = company.get("slug", "")
            company_name = company.get("name", slug)
            if not slug:
                continue
            try:
                resp = http.get(
                    _BASE_URL.format(slug=slug),
                    params={"content": "true"},
                )
                data = resp.json()
            except Exception as exc:
                logger.warning("greenhouse: fetch failed for %s: %s", company_name, exc)
                continue

            for job in data.get("jobs", []):
                job["_company_name"] = company_name
                job["_slug"] = slug
                raw_jobs.append(job)

        return raw_jobs

    def normalise(self, raw: RawJob) -> JobRecord | None:
        """Convert a raw Greenhouse job into a normalised JobRecord."""
        location_obj = raw.get("location", {})
        if isinstance(location_obj, dict):
            location_str = location_obj.get("name", "")
        else:
            location_str = str(location_obj)

        # The Greenhouse boards API returns `content` HTML-entity-escaped
        # (&lt;p&gt;...). Unescape so jd_clean can strip the real tags instead
        # of passing literal "<p>" noise to the ranker.
        description = ""
        content = raw.get("content", "")
        if content:
            import html
            description = html.unescape(content)

        slug = raw.get("_slug", "")
        job_id_gh = raw.get("id", "")
        url = raw.get("absolute_url") or f"https://boards.greenhouse.io/{slug}/jobs/{job_id_gh}"

        mapped: RawJob = {
            "title": raw.get("title", ""),
            "company": raw.get("_company_name", ""),
            "url": url,
            "location": location_str,
            "description": description,
            "created": raw.get("updated_at", ""),
            "source": f"{self.name}:{raw.get('_slug', '')}",
        }
        return normalise(mapped, self.name)
