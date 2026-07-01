"""Pre-ranking job filter applied before the LLM to save API spend.

Drops jobs where:
- salary_max < salary_floor_gbp (keeps nulls -- many real jobs omit salary)
- posted_date < today - max_days_since_posted
- company in exclude_companies or in cooldown (rejected within last N days)
- location matches a word in profile["filters"]["location_excludes"]
- the job is remote and remote_ok=False
- the job is on-site with known coordinates further than search_radius_miles
  from home (remote_ok does not bypass the radius for on-site jobs)
- title matches a word in profile["negative_signals"]["title_excludes"] (word-boundary matched)
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_REMOTE_TOKENS = frozenset(["remote", "work from home", "wfh", "fully remote", "home based"])


def _is_remote(location: str) -> bool:
    return any(tok in location.lower() for tok in _REMOTE_TOKENS)


def _cooldown_companies(
    conn: sqlite3.Connection,
    cooldown_days: int,
    today: date,
) -> set[str]:
    """Return lowercase company names rejected within the cooldown window."""
    cutoff = (today - timedelta(days=cooldown_days)).isoformat()
    rows = conn.execute(
        """
        SELECT DISTINCT LOWER(company) AS company FROM jobs
        WHERE status = 'rejected' AND last_seen >= ?
        """,
        (cutoff,),
    ).fetchall()
    return {row["company"] for row in rows}


def _build_title_exclude_pattern(title_excludes: list[str]) -> re.Pattern | None:
    """Compile a word-boundary regex from the profile title_excludes list.

    Uses \b so that e.g. "lead" in title_excludes does NOT block "leading"
    or "electrical" -- only the exact word "lead".
    Returns None if the list is empty (no exclusions apply).
    """
    if not title_excludes:
        return None
    escaped = [re.escape(term.strip()) for term in title_excludes if term.strip()]
    if not escaped:
        return None
    pattern = r"\b(?:" + "|".join(escaped) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


def _title_is_relevant(title: str, title_exclude_pattern: object) -> bool:
    """Return False if the title contains any word from profile title_excludes.

    All other titles are allowed through. Hard exclusion authority belongs
    solely to the user-controlled profile -- never hard-coded here.
    """
    if title_exclude_pattern is not None and title_exclude_pattern.search(title):
        return False
    return True


def apply_filters(
    records: list,
    profile: dict,
    conn: sqlite3.Connection,
    today: date | None = None,
) -> list:
    """Return the subset of records that pass all configured filters."""
    if today is None:
        today = date.today()

    filters = profile.get("filters", {})
    negative = profile.get("negative_signals", {})

    salary_floor = filters.get("salary_floor_gbp", 0)
    max_days = filters.get("max_days_since_posted", 30)
    exclude_companies = {c.lower() for c in filters.get("exclude_companies", [])}
    cooldown_days = filters.get("rejected_company_cooldown_days", 90)
    search_radius = profile.get("search_radius_miles", 60)
    remote_ok = profile.get("remote_ok", True)

    home_lat = profile.get("location", {}).get("lat")
    home_lon = profile.get("location", {}).get("lon")

    company_blocklist = {c.lower() for c in negative.get("company_blocklist", [])}

    title_excludes = negative.get("title_excludes", [])
    title_exclude_pattern = _build_title_exclude_pattern(title_excludes)

    # Locations to hard-drop (word-boundary matched), e.g. foreign countries
    # from worldwide ATS boards. User-controlled via filters.location_excludes.
    location_exclude_pattern = _build_title_exclude_pattern(
        filters.get("location_excludes", [])
    )

    cooldown = _cooldown_companies(conn, cooldown_days, today)

    passed = []

    for rec in records:
        if rec.salary_max is not None and salary_floor and rec.salary_max < salary_floor:
            logger.debug(
                "filter: dropped %s (salary %s < floor %s)",
                rec.title, rec.salary_max, salary_floor,
            )
            continue

        if rec.posted_date and (today - rec.posted_date).days > max_days:
            logger.debug(
                "filter: dropped %s (posted %s > %d days ago)",
                rec.title, rec.posted_date, max_days,
            )
            continue

        company_lower = rec.company.lower()
        if company_lower in exclude_companies or company_lower in company_blocklist:
            logger.debug("filter: dropped %s (company blocklisted)", rec.company)
            continue

        if not _title_is_relevant(rec.title or "", title_exclude_pattern):
            logger.debug("filter: dropped %s (title matches title_excludes)", rec.title)
            continue

        if company_lower in cooldown:
            logger.debug("filter: dropped %s (company in rejection cooldown)", rec.company)
            continue

        location = rec.location or ""
        if location_exclude_pattern is not None and location_exclude_pattern.search(location):
            logger.debug("filter: dropped %s (location %r excluded)", rec.title, location)
            continue

        if _is_remote(location):
            # Remote job: keep only if the profile wants remote roles.
            if not remote_ok:
                logger.debug("filter: dropped %s (remote, remote_ok=false)", rec.title)
                continue
        else:
            # On-site job: apply the search radius whenever coordinates are
            # known, regardless of remote_ok. Jobs without coordinates get the
            # benefit of the doubt.
            if rec.lat is not None and rec.lon is not None and home_lat and home_lon:
                from job_search.util.geocode import distance_miles
                dist = distance_miles(home_lat, home_lon, rec.lat, rec.lon)
                if dist > search_radius:
                    logger.debug(
                        "filter: dropped %s (%.0f miles > %d radius)",
                        rec.title, dist, search_radius,
                    )
                    continue

        passed.append(rec)

    return passed
