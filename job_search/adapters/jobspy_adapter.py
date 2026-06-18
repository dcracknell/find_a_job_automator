"""JobSpy adapter — wraps python-jobspy for LinkedIn/Indeed/Glassdoor/Google Jobs."""

from __future__ import annotations

import logging

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise

logger = logging.getLogger(__name__)

_DEFAULT_SITES = ["indeed", "linkedin", "google"]
_SUPPORTED_SITES = {"indeed", "linkedin", "glassdoor", "google", "zip_recruiter"}


class JobSpyAdapter(Adapter):
    """Wraps python-jobspy to scrape LinkedIn, Indeed, and Google Jobs."""

    name = "jobspy"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        """Scrape jobs via JobSpy for the given search queries."""
        try:
            import jobspy
        except ImportError:
            logger.error(
                "jobspy: python-jobspy is not installed. Run: pip install python-jobspy"
            )
            return []

        src = settings.get("jobspy", {})
        sites_cfg = src.get("sites", _DEFAULT_SITES)
        sites = [s for s in sites_cfg if s in _SUPPORTED_SITES]
        if not sites:
            sites = _DEFAULT_SITES

        country = src.get("country", "uk")
        results_per_query = int(src.get("results_wanted_per_query", 25))
        proxies = src.get("proxies") or []
        profile_location = settings.get("_profile_location", "")
        hours_old = 720

        seen_urls: set[str] = set()
        raw_jobs: list[RawJob] = []

        for query in queries:
            try:
                location = profile_location or "United Kingdom"
                df = jobspy.scrape_jobs(
                    site_name=sites,
                    search_term=query,
                    location=location,
                    results_wanted=results_per_query,
                    country_indeed=country,
                    hours_old=hours_old,
                    proxies=proxies if proxies else None,
                    linkedin_fetch_description=True,
                    verbose=0,
                )
            except Exception as exc:
                logger.warning("jobspy: scrape failed for query %r: %s", query, exc)
                continue

            if df is None or df.empty:
                continue

            for _, row in df.iterrows():
                url = str(row.get("job_url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                raw: RawJob = {
                    "title": str(row.get("title") or ""),
                    "company": str(row.get("company") or ""),
                    "url": url,
                    "location": str(row.get("location") or ""),
                    "description": str(row.get("description") or ""),
                    "salary_raw": str(row.get("salary_source") or row.get("min_amount") or ""),
                    "salary_min": _safe_float(row.get("min_amount")),
                    "salary_max": _safe_float(row.get("max_amount")),
                    "created": str(row.get("date_posted") or ""),
                    "matched_query": query,
                    "source": self.name,
                }
                raw_jobs.append(raw)

        logger.info("jobspy: fetched %d jobs across %d queries", len(raw_jobs), len(queries))
        return raw_jobs

    def normalise(self, raw: RawJob) -> JobRecord | None:
        return normalise(raw, self.name)

    def healthcheck(self) -> tuple[bool, str | None]:
        try:
            import jobspy  # noqa: F401
            return True, None
        except ImportError as exc:
            return False, str(exc)


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
