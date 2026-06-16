"""Normalise raw adapter output into JobRecords."""

from __future__ import annotations

import logging
import re
from datetime import date
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

from job_search.adapters.base import JobRecord, RawJob
from job_search.pipeline.jd_clean import clean_jd
from job_search.util.dates import extract_closing_date
from job_search.util.geocode import geocode
from job_search.util.salary import parse_salary

logger = logging.getLogger(__name__)

# Query parameters that are required for a listing to be loadable (whitelist).
# Everything else is stripped to produce the canonical URL.
_KEEP_PARAMS: frozenset[str] = frozenset(["id", "jobId", "job_id", "vacancyId", "reference"])


def _canonical_url(url: str) -> str:
    """Strip unnecessary query parameters from a URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=False)
    kept = {k: v for k, v in qs.items() if k in _KEEP_PARAMS}
    new_query = urlencode(kept, doseq=True)
    canonical = parsed._replace(query=new_query, fragment="")
    return urlunparse(canonical)


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%d/%m/%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def normalise(
    raw: RawJob,
    adapter_name: str,
    domain_pack: dict | None = None,
    max_jd_tokens: int = 1500,
) -> JobRecord | None:
    """Convert a raw adapter response to a normalised JobRecord.

    Returns None if the record is missing required fields (title, company, url).
    """
    title = (raw.get("title") or "").strip()
    company = (raw.get("company") or raw.get("employer") or "").strip()
    url = (raw.get("url") or raw.get("redirect_url") or "").strip()

    if not title or not company or not url:
        logger.debug("normalise: skipping record missing title/company/url: %r", raw)
        return None

    canonical = _canonical_url(url)
    job_id = JobRecord.make_job_id(company, title, canonical)

    location = (raw.get("location") or raw.get("location_name") or "").strip()
    # Try to geocode
    coords = None
    try:
        coords = geocode(location)
    except Exception as exc:
        logger.debug("normalise: geocode failed for %r: %s", location, exc)
    lat = coords[0] if coords else None
    lon = coords[1] if coords else None

    # Salary
    salary_raw = raw.get("salary_raw") or raw.get("salary") or raw.get("salary_min_str")
    if not salary_raw:
        s_min = raw.get("salary_min") or raw.get("minimum_salary")
        s_max = raw.get("salary_max") or raw.get("maximum_salary")
        if s_min or s_max:
            salary_raw = f"£{s_min or ''} - £{s_max or ''}".strip(" -")
    sal_min, sal_max = parse_salary(salary_raw, domain_pack)
    # If raw already has parsed numeric values, use those as fallback
    if sal_min is None and raw.get("salary_min"):
        try:
            sal_min = int(raw["salary_min"])
        except (ValueError, TypeError):
            pass
    if sal_max is None and raw.get("salary_max"):
        try:
            sal_max = int(raw["salary_max"])
        except (ValueError, TypeError):
            pass

    # Description / JD cleaning
    raw_desc = raw.get("description") or raw.get("job_description") or ""
    cleaned_desc, jd_hash = clean_jd(raw_desc, max_jd_tokens)

    # Closing date
    domain_patterns = None
    if domain_pack:
        domain_patterns = domain_pack.get("closing_date_patterns")
    closes_on = extract_closing_date(cleaned_desc, domain_patterns)

    # Posted date
    posted_date = _parse_date(raw.get("created") or raw.get("posted_date") or raw.get("date_posted"))

    source = raw.get("source") or adapter_name

    return JobRecord(
        job_id=job_id,
        source=source,
        title=title,
        company=company,
        location=location,
        lat=lat,
        lon=lon,
        url=canonical,
        description=cleaned_desc,
        posted_date=posted_date,
        closes_on=closes_on,
        salary_raw=salary_raw,
        salary_min=sal_min,
        salary_max=sal_max,
        matched_query=raw.get("matched_query"),
        jd_content_hash=jd_hash,
    )
