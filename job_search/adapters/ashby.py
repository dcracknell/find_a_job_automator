"""Ashby ATS adapter — generic, one-line YAML to add a company.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/{slug}
Public API, no authentication required.
"""

from __future__ import annotations

import logging

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


class AshbyAdapter(Adapter):
    """Generic Ashby ATS adapter. Companies configured in sources.yaml."""

    name = "ashby"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        companies = settings.get("ats", {}).get("ashby", {}).get("companies", [])
        raw_jobs: list[RawJob] = []

        for company in companies:
            slug = company.get("slug", "")
            company_name = company.get("name", slug)
            if not slug:
                continue
            try:
                resp = http.get(_BASE_URL.format(slug=slug))
                data = resp.json()
            except Exception as exc:
                logger.warning("ashby: fetch failed for %s: %s", company_name, exc)
                continue

            for job in data.get("jobs", []):
                if job.get("isListed") is False:
                    continue
                job["_company_name"] = company_name
                job["_slug"] = slug
                raw_jobs.append(job)

        return raw_jobs

    def normalise(self, raw: RawJob) -> JobRecord | None:
        location = raw.get("location") or ""
        if raw.get("isRemote"):
            location = f"Remote{f' ({location})' if location else ''}"

        mapped: RawJob = {
            "title": raw.get("title", ""),
            "company": raw.get("_company_name", ""),
            "url": raw.get("jobUrl") or raw.get("applyUrl", ""),
            "location": location,
            "description": raw.get("descriptionHtml") or raw.get("descriptionPlain", ""),
            "created": (raw.get("publishedAt") or "")[:10],
            "source": f"{self.name}:{raw.get('_slug', '')}",
        }
        return normalise(mapped, self.name)
