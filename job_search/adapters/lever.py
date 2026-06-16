"""Lever ATS adapter — generic, one-line YAML to add a company.

Endpoint: https://api.lever.co/v0/postings/{slug}?mode=json
Public API, no authentication required.
"""

from __future__ import annotations

import logging

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.lever.co/v0/postings/{slug}"


class LeverAdapter(Adapter):
    """Generic Lever ATS adapter. Companies configured in sources.yaml."""

    name = "lever"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        """Fetch all jobs from all configured Lever companies."""
        companies = settings.get("ats", {}).get("lever", {}).get("companies", [])
        raw_jobs: list[RawJob] = []

        for company in companies:
            slug = company.get("slug", "")
            company_name = company.get("name", slug)
            if not slug:
                continue
            try:
                resp = http.get(
                    _BASE_URL.format(slug=slug),
                    params={"mode": "json"},
                )
                postings = resp.json()
            except Exception as exc:
                logger.warning("lever: fetch failed for %s: %s", company_name, exc)
                continue

            for posting in postings:
                posting["_company_name"] = company_name
                posting["_slug"] = slug
                raw_jobs.append(posting)

        return raw_jobs

    def normalise(self, raw: RawJob) -> JobRecord | None:
        """Convert a raw Lever posting into a normalised JobRecord."""
        # Lever description is split into lists + closing sections
        description_parts = []
        description_obj = raw.get("descriptionBody") or raw.get("description", "")
        if isinstance(description_obj, str):
            description_parts.append(description_obj)

        for section in raw.get("lists", []):
            description_parts.append(section.get("text", ""))
            items = section.get("content", "")
            if items:
                description_parts.append(items)

        closing = raw.get("closing", "")
        if closing:
            description_parts.append(closing)

        description = "\n\n".join(filter(None, description_parts))

        categories = raw.get("categories", {})
        location = categories.get("location") or categories.get("team", "")

        mapped: RawJob = {
            "title": raw.get("text", ""),
            "company": raw.get("_company_name", ""),
            "url": raw.get("hostedUrl") or raw.get("applyUrl", ""),
            "location": location,
            "description": description,
            "created": str(raw.get("createdAt", "")),
            "source": f"{self.name}:{raw.get('_slug', '')}",
        }
        return normalise(mapped, self.name)
