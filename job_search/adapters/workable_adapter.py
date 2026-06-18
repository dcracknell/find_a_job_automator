"""Workable ATS adapter."""
from __future__ import annotations
import logging
from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http
logger = logging.getLogger(__name__)
_BASE_URL = "https://apply.workable.com/api/v1/widget/accounts/{slug}/jobs"
class WorkableAdapter(Adapter):
    """Generic Workable ATS adapter. Companies configured in sources.yaml."""
    name = "workable"
    def fetch(self, queries, settings):
        companies = settings.get("ats", {}).get("workable", {}).get("companies", [])
        raw_jobs = []
        for company in companies:
            slug = company.get("slug", "")
            name = company.get("name", slug)
            if not slug:
                continue
            try:
                resp = http.get(_BASE_URL.format(slug=slug), params={"details": "true"})
                data = resp.json()
            except Exception as exc:
                logger.warning("workable: %s failed: %s", name, exc)
                continue
            for job in data.get("results", []):
                job["_company_name"] = name
                job["_slug"] = slug
                raw_jobs.append(job)
        return raw_jobs
    def normalise(self, raw):
        parts = [raw.get(f, "") for f in ("description", "requirements", "benefits") if raw.get(f)]
        description = "\n\n".join(parts)
        loc = raw.get("location", {})
        if isinstance(loc, dict):
            location_str = ", ".join(filter(None, [loc.get("city",""), loc.get("country","")]))
        else:
            location_str = str(loc or "")
        slug = raw.get("_slug", "")
        shortcode = raw.get("shortcode", "")
        url = raw.get("url") or f"https://apply.workable.com/{slug}/j/{shortcode}"
        mapped = {"title": raw.get("title",""), "company": raw.get("_company_name",""),
                  "url": url, "location": location_str, "description": description,
                  "created": raw.get("created_at",""), "source": f"{self.name}:{slug}"}
        return normalise(mapped, self.name)
    def healthcheck(self):
        try:
            http.get(_BASE_URL.format(slug="monzo"), timeout=5).json()
            return True, None
        except Exception as exc:
            return False, str(exc)
