"""Arbeitnow job-board adapter — free, keyless JSON API.

https://www.arbeitnow.com/api/job-board-api (paginated). Mostly European
listings; only UK-located or remote roles are kept.
"""

from __future__ import annotations

import logging

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.arbeitnow.com/api/job-board-api"

_UK_TOKENS = [
    "uk", "united kingdom", "london", "manchester", "edinburgh", "glasgow",
    "birmingham", "leeds", "sheffield", "bristol", "cambridge", "oxford",
]


class ArbeitnowAdapter(Adapter):
    """Fetches jobs from the Arbeitnow public job-board API."""

    name = "arbeitnow"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        cfg = settings.get("aggregators", {}).get("arbeitnow", {})
        pages = int(cfg.get("pages", 3))
        remote_ok = settings.get("_profile", {}).get("remote_ok", True)

        raw_jobs: list[RawJob] = []
        seen: set[str] = set()

        for page in range(1, pages + 1):
            try:
                resp = http.get(_BASE_URL, params={"page": page})
                data = resp.json().get("data", [])
            except Exception as exc:
                logger.warning("arbeitnow: page %d failed: %s", page, exc)
                break
            if not data:
                break

            for job in data:
                slug = job.get("slug") or ""
                if not slug or slug in seen:
                    continue
                location = (job.get("location") or "").lower()
                is_uk = any(tok in location for tok in _UK_TOKENS)
                is_remote = bool(job.get("remote")) and remote_ok
                if not (is_uk or is_remote):
                    continue
                seen.add(slug)
                raw_jobs.append(job)

        logger.info("arbeitnow: %d UK/remote jobs across %d page(s)", len(raw_jobs), pages)
        return raw_jobs

    def normalise(self, raw: RawJob) -> JobRecord | None:
        location = raw.get("location") or ""
        if raw.get("remote"):
            location = f"Remote{f' ({location})' if location else ''}"
        created = raw.get("created_at")
        mapped: RawJob = {
            "title": raw.get("title", ""),
            "company": raw.get("company_name", ""),
            "url": raw.get("url", ""),
            "location": location,
            "description": raw.get("description", ""),
            "created": str(created) if created else "",
            "source": self.name,
        }
        return normalise(mapped, self.name)

    def healthcheck(self) -> tuple[bool, str | None]:
        try:
            http.get(_BASE_URL, params={"page": 1}).json()
            return True, None
        except Exception as exc:
            return False, str(exc)
