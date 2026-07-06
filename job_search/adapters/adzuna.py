"""Adzuna API adapter — reference implementation.

UK jobs API, free tier ~250 calls/day.
Returns JSON with title/company/location/salary/url/description.
Credentials: ADZUNA_APP_ID and ADZUNA_APP_KEY in .env
"""

from __future__ import annotations

import logging
import os

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.adzuna.com/v1/api/jobs/gb/search"


class AdzunaAdapter(Adapter):
    """Fetches UK jobs from the Adzuna jobs API (free tier ~250 calls/day)."""

    name = "adzuna"

    def __init__(self) -> None:
        self._app_id: str | None = None
        self._app_key: str | None = None

    def _credentials(self) -> tuple[str, str]:
        if self._app_id and self._app_key:
            return self._app_id, self._app_key

        # Load from environment (dotenv should already be loaded by cli.py)
        app_id = os.environ.get("ADZUNA_APP_ID", "")
        app_key = os.environ.get("ADZUNA_APP_KEY", "")
        if not app_id or not app_key:
            raise RuntimeError(
                "ADZUNA_APP_ID and ADZUNA_APP_KEY must be set in .env"
            )
        self._app_id = app_id
        self._app_key = app_key
        return app_id, app_key

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        """Fetch raw job postings for the given search queries."""
        app_id, app_key = self._credentials()
        src_settings = settings.get("apis", {}).get("adzuna", {})
        results_per_query = src_settings.get("results_per_query", 50)

        # Only fetch recent listings, newest first — no point re-downloading
        # month-old postings the DB already has on every run.
        profile_filters = settings.get("_profile", {}).get("filters", {})
        max_days_old = int(profile_filters.get("max_days_since_posted", 30) or 30)
        # Server-side salary floor: skips sub-floor listings before they cost
        # download, geocoding, and LLM-ranking spend. salary_include_unknown
        # keeps postings with no stated salary (the local filter keeps nulls too).
        salary_floor = int(profile_filters.get("salary_floor_gbp", 0) or 0)

        seen_ids: set[str] = set()
        raw_jobs: list[RawJob] = []

        for query in queries:
            page = 1
            fetched = 0
            while fetched < results_per_query:
                page_size = min(50, results_per_query - fetched)
                try:
                    resp = http.get(
                        f"{_BASE_URL}/{page}",
                        params={
                            "app_id": app_id,
                            "app_key": app_key,
                            "what": query,
                            "where": "UK",
                            "results_per_page": page_size,
                            "max_days_old": max_days_old,
                            "sort_by": "date",
                            **(
                                {"salary_min": salary_floor, "salary_include_unknown": "1"}
                                if salary_floor
                                else {}
                            ),
                            "content-type": "application/json",
                        },
                    )
                    data = resp.json()
                except Exception as exc:
                    logger.warning("adzuna: fetch failed for query %r: %s", query, exc)
                    break

                results = data.get("results", [])
                if not results:
                    break

                for item in results:
                    adzuna_id = str(item.get("id", ""))
                    if adzuna_id and adzuna_id in seen_ids:
                        continue
                    seen_ids.add(adzuna_id)
                    item["matched_query"] = query
                    raw_jobs.append(item)

                fetched += len(results)
                if len(results) < page_size:
                    break  # no more pages
                page += 1

        return raw_jobs

    def normalise(self, raw: RawJob) -> JobRecord | None:
        """Convert a raw Adzuna response item into a normalised JobRecord."""
        # Map Adzuna field names to our common schema
        location_obj = raw.get("location", {})
        location_str = ", ".join(location_obj.get("display_name", "").split(", ")[:2])

        salary_min = raw.get("salary_min")
        salary_max = raw.get("salary_max")
        salary_raw = None
        if salary_min or salary_max:
            parts = []
            if salary_min:
                parts.append(f"£{int(salary_min):,}")
            if salary_max:
                parts.append(f"£{int(salary_max):,}")
            salary_raw = " - ".join(parts) if len(parts) == 2 else parts[0]

        mapped: RawJob = {
            "title": raw.get("title", ""),
            "company": (raw.get("company") or {}).get("display_name", ""),
            "url": raw.get("redirect_url", ""),
            "location": location_str,
            "description": raw.get("description", ""),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_raw": salary_raw,
            "created": raw.get("created", ""),
            "matched_query": raw.get("matched_query"),
            "source": self.name,
        }
        return normalise(mapped, self.name)

    def healthcheck(self) -> tuple[bool, str | None]:
        try:
            self.fetch(["engineer"], {"apis": {"adzuna": {"results_per_query": 1}}})
            return True, None
        except Exception as exc:
            return False, str(exc)
