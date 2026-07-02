"""SmartRecruiters ATS adapter — generic, one-line YAML to add a company.

List endpoint: https://api.smartrecruiters.com/v1/companies/{slug}/postings
Public API, no authentication. Descriptions live behind a per-posting detail
call, so details are only fetched for new, non-excluded jobs (same policy as
the Reed adapter).
"""

from __future__ import annotations

import logging

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http

logger = logging.getLogger(__name__)

_LIST_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
_DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"
_PUBLIC_URL = "https://jobs.smartrecruiters.com/{slug}/{posting_id}"

_MAX_DETAILS_PER_COMPANY = 30


class SmartRecruitersAdapter(Adapter):
    """Generic SmartRecruiters ATS adapter. Companies configured in sources.yaml."""

    name = "smartrecruiters"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        companies = settings.get("ats", {}).get("smartrecruiters", {}).get("companies", [])
        known_urls: set[str] = settings.get("_known_job_urls") or set()

        from job_search.pipeline.filter import _build_title_exclude_pattern
        profile = settings.get("_profile", {})
        title_exclude_pattern = _build_title_exclude_pattern(
            profile.get("negative_signals", {}).get("title_excludes", [])
        )

        raw_jobs: list[RawJob] = []

        for company in companies:
            slug = company.get("slug", "")
            company_name = company.get("name", slug)
            if not slug:
                continue
            try:
                resp = http.get(_LIST_URL.format(slug=slug), params={"limit": 100})
                postings = resp.json().get("content", [])
            except Exception as exc:
                logger.warning("smartrecruiters: fetch failed for %s: %s", company_name, exc)
                continue

            details_fetched = 0
            for posting in postings:
                posting_id = posting.get("id", "")
                title = posting.get("name", "") or ""
                public_url = _PUBLIC_URL.format(slug=slug, posting_id=posting_id)

                excluded = (
                    title_exclude_pattern is not None
                    and title_exclude_pattern.search(title)
                )
                if (
                    not excluded
                    and public_url not in known_urls
                    and details_fetched < _MAX_DETAILS_PER_COMPANY
                ):
                    try:
                        detail = http.get(
                            _DETAIL_URL.format(slug=slug, posting_id=posting_id)
                        ).json()
                        sections = (detail.get("jobAd") or {}).get("sections") or {}
                        posting["_description"] = "\n\n".join(
                            str(section.get("text") or "")
                            for section in sections.values()
                            if isinstance(section, dict)
                        )
                        details_fetched += 1
                    except Exception as exc:
                        logger.debug(
                            "smartrecruiters: detail failed for %s/%s: %s",
                            slug, posting_id, exc,
                        )

                posting["_company_name"] = company_name
                posting["_slug"] = slug
                posting["_public_url"] = public_url
                raw_jobs.append(posting)

        return raw_jobs

    def normalise(self, raw: RawJob) -> JobRecord | None:
        location_obj = raw.get("location") or {}
        location = ", ".join(
            p for p in (location_obj.get("city") or "", location_obj.get("country") or "")
            if p
        )
        if location_obj.get("remote"):
            location = f"Remote{f' ({location})' if location else ''}"

        mapped: RawJob = {
            "title": raw.get("name", ""),
            "company": raw.get("_company_name", ""),
            "url": raw.get("_public_url", ""),
            "location": location,
            "description": raw.get("_description", ""),
            "created": (raw.get("releasedDate") or "")[:10],
            "source": f"{self.name}:{raw.get('_slug', '')}",
        }
        return normalise(mapped, self.name)
