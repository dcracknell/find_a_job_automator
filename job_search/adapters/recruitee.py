"""Recruitee ATS adapter — generic, one-line YAML to add a company.

Endpoint: https://{slug}.recruitee.com/api/offers/
Public API, no authentication required. Descriptions arrive in the list call.
"""

from __future__ import annotations

import logging

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http

logger = logging.getLogger(__name__)

_BASE_URL = "https://{slug}.recruitee.com/api/offers/"


class RecruiteeAdapter(Adapter):
    """Generic Recruitee ATS adapter. Companies configured in sources.yaml."""

    name = "recruitee"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        companies = settings.get("ats", {}).get("recruitee", {}).get("companies", [])
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
                logger.warning("recruitee: fetch failed for %s: %s", company_name, exc)
                continue

            for offer in data.get("offers", []):
                offer["_company_name"] = company_name
                offer["_slug"] = slug
                raw_jobs.append(offer)

        return raw_jobs

    def normalise(self, raw: RawJob) -> JobRecord | None:
        parts = [raw.get(field) or "" for field in ("description", "requirements")]
        description = "\n\n".join(p for p in parts if p)

        location = ", ".join(
            p for p in (raw.get("city") or "", raw.get("country") or "") if p
        )
        if raw.get("remote"):
            location = f"Remote{f' ({location})' if location else ''}"

        mapped: RawJob = {
            "title": raw.get("title", ""),
            "company": raw.get("_company_name", ""),
            "url": raw.get("careers_url") or raw.get("url", ""),
            "location": location,
            "description": description,
            "created": (raw.get("published_at") or raw.get("created_at") or "")[:10],
            "source": f"{self.name}:{raw.get('_slug', '')}",
        }
        return normalise(mapped, self.name)
