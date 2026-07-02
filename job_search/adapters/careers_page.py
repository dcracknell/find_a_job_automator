"""Generic careers-page adapter — reads schema.org JobPosting JSON-LD.

Catch-all for companies not on any known ATS: most careers pages embed
<script type="application/ld+json"> JobPosting blocks so Google can index
their jobs — the same markup gives this pipeline structured title, company,
location, dates, and description with no site-specific scraping.

Configure pages in sources.yaml:

    custom_pages:
      - {name: Some Co, url: "https://someco.example/careers"}
"""

from __future__ import annotations

import json
import logging

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http

logger = logging.getLogger(__name__)


def _iter_jsonld_nodes(payload: object):
    """Yield every dict node from a JSON-LD document (handles lists/@graph)."""
    if isinstance(payload, dict):
        yield payload
        for value in payload.get("@graph") or []:
            yield from _iter_jsonld_nodes(value)
        for key in ("itemListElement", "mainEntity"):
            if key in payload:
                yield from _iter_jsonld_nodes(payload[key])
        if "item" in payload:
            yield from _iter_jsonld_nodes(payload["item"])
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_jsonld_nodes(item)


def _location_text(node: dict) -> str:
    loc = node.get("jobLocation")
    locations = loc if isinstance(loc, list) else [loc]
    parts: list[str] = []
    for entry in locations:
        if not isinstance(entry, dict):
            continue
        address = entry.get("address") or {}
        if isinstance(address, dict):
            for key in ("addressLocality", "addressRegion", "addressCountry"):
                value = address.get(key)
                if isinstance(value, dict):
                    value = value.get("name")
                if value:
                    parts.append(str(value))
        elif isinstance(address, str):
            parts.append(address)
    if node.get("jobLocationType") == "TELECOMMUTE":
        parts.insert(0, "Remote")
    return ", ".join(dict.fromkeys(parts))


def extract_job_postings(html: str, fallback_company: str = "", page_url: str = "") -> list[RawJob]:
    """Extract schema.org JobPosting entries from a page's JSON-LD blocks."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("careers_page: beautifulsoup4 is required")
        return []

    soup = BeautifulSoup(html, "html.parser")
    jobs: list[RawJob] = []

    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string or script.get_text() or ""
        if not text.strip():
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue

        for node in _iter_jsonld_nodes(payload):
            if str(node.get("@type", "")).lower() != "jobposting":
                continue
            org = node.get("hiringOrganization") or {}
            company = (
                org.get("name") if isinstance(org, dict) else str(org)
            ) or fallback_company
            url = node.get("url") or node.get("directApply") or page_url
            if not isinstance(url, str):
                url = page_url
            jobs.append(
                {
                    "title": str(node.get("title") or ""),
                    "company": str(company or ""),
                    "url": url,
                    "location": _location_text(node),
                    "description": str(node.get("description") or ""),
                    "created": str(node.get("datePosted") or "")[:10],
                    "salary_raw": _salary_text(node),
                }
            )
    return jobs


def _salary_text(node: dict) -> str | None:
    salary = node.get("baseSalary")
    if not isinstance(salary, dict):
        return None
    value = salary.get("value")
    if isinstance(value, dict):
        lo, hi = value.get("minValue"), value.get("maxValue")
        unit = str(value.get("unitText") or "YEAR").lower()
        suffix = {"hour": "/hr", "day": "/day"}.get(unit, "")
        if lo and hi:
            return f"£{lo} - £{hi}{suffix}"
        single = lo or hi or value.get("value")
        if single:
            return f"£{single}{suffix}"
    return None


class CareersPageAdapter(Adapter):
    """Reads JobPosting JSON-LD from arbitrary careers pages in sources.yaml."""

    name = "careers_page"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        pages = settings.get("custom_pages") or []
        raw_jobs: list[RawJob] = []

        for page in pages:
            if isinstance(page, str):
                page = {"url": page}
            url = page.get("url", "")
            name = page.get("name", "")
            if not url:
                continue
            try:
                resp = http.get(url, timeout=30)
            except Exception as exc:
                logger.warning("careers_page: fetch failed for %s: %s", url, exc)
                continue

            found = extract_job_postings(resp.text, fallback_company=name, page_url=url)
            if not found:
                logger.info("careers_page: no JobPosting JSON-LD on %s", url)
            for job in found:
                job["source"] = self.name
                raw_jobs.append(job)

        return raw_jobs

    def normalise(self, raw: RawJob) -> JobRecord | None:
        return normalise(raw, self.name)
