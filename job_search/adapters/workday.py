"""Workday ATS adapter — per-tenant URL.

Workday tenants vary; typical pattern:
  POST {tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
  with JSON body {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "..."}
"""

from __future__ import annotations

import logging
import re

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http

logger = logging.getLogger(__name__)

_TENANT_RE = re.compile(r"https?://([^.]+)\.wd\d+\.myworkdayjobs\.com")


def _derive_api_url(careers_url: str) -> str | None:
    """Attempt to derive the Workday API endpoint from a careers URL.

    Workday career URLs look like:
      https://{tenant}.wdN.myworkdayjobs.com/{site}
    The corresponding API endpoint is:
      https://{tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    """
    m = _TENANT_RE.match(careers_url)
    if not m:
        return None
    tenant = m.group(1)
    # Extract the site path after the domain
    path = re.sub(r"https?://[^/]+", "", careers_url).strip("/")
    if not path:
        path = "jobs"
    base = careers_url.split(".myworkdayjobs.com")[0] + ".myworkdayjobs.com"
    return f"{base}/wday/cxs/{tenant}/{path}/jobs"


class WorkdayAdapter(Adapter):
    """Generic Workday ATS adapter. Companies + base URLs configured in sources.yaml."""

    name = "workday"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        """Fetch jobs from all configured Workday companies."""
        companies = settings.get("ats", {}).get("workday", {}).get("companies", [])
        raw_jobs: list[RawJob] = []

        for company in companies:
            company_name = company.get("name", "")
            careers_url = company.get("url", "")
            if not careers_url:
                continue

            api_url = _derive_api_url(careers_url)
            if not api_url:
                logger.warning("workday: cannot derive API URL from %s", careers_url)
                continue

            offset = 0
            limit = 20
            while True:
                try:
                    resp = http.post(
                        api_url,
                        json={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""},
                        headers={"Content-Type": "application/json"},
                    )
                    data = resp.json()
                except Exception as exc:
                    logger.warning("workday: fetch failed for %s: %s", company_name, exc)
                    break

                postings = data.get("jobPostings", [])
                if not postings:
                    break

                for posting in postings:
                    posting["_company_name"] = company_name
                    posting["_api_base"] = api_url
                    raw_jobs.append(posting)

                offset += limit
                total = data.get("total", 0)
                if offset >= total or offset >= 200:  # cap at 200 per company
                    break

        return raw_jobs

    def normalise(self, raw: RawJob) -> JobRecord | None:
        """Convert a raw Workday posting into a normalised JobRecord."""
        title = raw.get("title", "")
        # Workday external URL is typically a relative path; reconstruct
        ext_path = raw.get("externalPath") or raw.get("bulletFields", [{}])[0].get("fieldValue", "")
        api_base = raw.get("_api_base", "")
        # Derive the careers URL base from the API URL
        # api_url = https://tenant.wd3.../wday/cxs/tenant/site/jobs
        # careers_url = https://tenant.wd3.../site/jobDetails/{externalPath}
        url = ""
        if ext_path and api_base:
            domain = api_base.split("/wday/")[0]
            url = f"{domain}{ext_path}"
        elif raw.get("url"):
            url = raw["url"]

        mapped: RawJob = {
            "title": title,
            "company": raw.get("_company_name", ""),
            "url": url,
            "location": raw.get("locationsText") or raw.get("location", ""),
            "description": raw.get("jobDescription") or raw.get("briefDescription", ""),
            "source": self.name,
        }
        return normalise(mapped, self.name)
