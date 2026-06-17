"""Pre-ranking job filter — applied before the LLM to save API spend.

Drops jobs where:
- salary_max < salary_floor_gbp (but keeps nulls — many real jobs omit salary)
- posted_date < today - max_days_since_posted
- company in exclude_companies or in cooldown (rejected within last N days)
- distance from home > search_radius_miles AND remote_ok=False AND location != "Remote"
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date, timedelta

from job_search.adapters.base import JobRecord

logger = logging.getLogger(__name__)

_REMOTE_TOKENS = frozenset(["remote", "work from home", "wfh", "fully remote", "home based"])


def _is_remote(location: str) -> bool:
    return any(tok in location.lower() for tok in _REMOTE_TOKENS)


def _company_in_cooldown(
    conn: sqlite3.Connection,
    company: str,
    cooldown_days: int,
) -> bool:
    """Return True if the company was rejected recently and is in cooldown."""
    cutoff = (date.today() - timedelta(days=cooldown_days)).isoformat()
    row = conn.execute(
        """
        SELECT 1 FROM jobs
        WHERE LOWER(company) = LOWER(?)
          AND status = 'rejected'
          AND last_seen >= ?
        LIMIT 1
        """,
        (company, cutoff),
    ).fetchone()
    return row is not None



# Broad set of role families that are clearly NOT software development
_IRRELEVANT_TITLE_PATTERNS = re.compile(
    r"\b("
    r"hardware|electrical|electronics|electronic|mechanical|silicon|soc\b|asic|fpga|firmware|"
    r"lab technician|lab support|lab engineer|"
    r"retail|sales( associate| manager| exec)?|account manager|account executive|"
    r"marketing|compliance manager|global sourcing|sourcing manager|procurement|"
    r"field engineer|field service|"
    r"network engineer|network admin|"
    r"hardware validation|hardware reliability|hardware development|"
    r"board test|gpu architect|processor|silicon engineer|"
    r"chip design|vlsi|rtl|verilog|vhdl|"
    r"operations manager|programme manager|program manager|project manager|"
    r"product manager|product owner|"
    r"legal|finance|accounting|hr manager|talent|recruitment"
    r")\b",
    re.IGNORECASE,
)

# Minimum positive signal — at least one of these must appear in title
_SOFTWARE_TITLE_PATTERNS = re.compile(
    r"\b("
    r"software|developer|engineer|data|analyst|python|backend|frontend|full.?stack|"
    r"devops|cloud|platform|ml|machine learning|ai|api|web developer|"
    r"site reliability|sre|infrastructure|automation|application"
    r")\b",
    re.IGNORECASE,
)

def _title_is_relevant(title: str, target_roles: list[str]) -> bool:
    """Return False if title is clearly a non-software role."""
    if _IRRELEVANT_TITLE_PATTERNS.search(title):
        return False
    # Allow through if it matches a target role keyword
    if _SOFTWARE_TITLE_PATTERNS.search(title):
        return True
    # If the title doesn't have any software signal but also isn't blocked,
    # let it through (it might be a generic posting)
    return True

def apply_filters(
    records: list[JobRecord],
    profile: dict,
    conn: sqlite3.Connection,
    today: date | None = None,
) -> list[JobRecord]:
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

    passed: list[JobRecord] = []

    # Get target role keywords for title relevance check
    target_core = profile.get("target_roles", {}).get("core", [])
    target_adjacent = profile.get("target_roles", {}).get("adjacent", [])
    target_all_roles = target_core + target_adjacent

    for rec in records:
        # Salary floor (keep nulls)
        if rec.salary_max is not None and salary_floor and rec.salary_max < salary_floor:
            logger.debug("filter: dropped %s (salary %s < floor %s)", rec.title, rec.salary_max, salary_floor)
            continue

        # Stale post
        if rec.posted_date and (today - rec.posted_date).days > max_days:
            logger.debug("filter: dropped %s (posted %s > %d days ago)", rec.title, rec.posted_date, max_days)
            continue

        # Company blocklist / excludes
        company_lower = rec.company.lower()
        if company_lower in exclude_companies or company_lower in company_blocklist:
            logger.debug("filter: dropped %s (company blocklisted)", rec.company)
            continue

        # Title relevance — drop clearly non-software roles
        if not _title_is_relevant(rec.title or "", target_all_roles):
            logger.debug("filter: dropped %s (title not software-relevant)", rec.title)
            continue

        # Cooldown
        if _company_in_cooldown(conn, rec.company, cooldown_days):
            logger.debug("filter: dropped %s (company in rejection cooldown)", rec.company)
            continue

        # Distance filter
        if not remote_ok and not _is_remote(rec.location or ""):
            if rec.lat is not None and rec.lon is not None and home_lat and home_lon:
                from job_search.util.geocode import distance_miles
                dist = distance_miles(home_lat, home_lon, rec.lat, rec.lon)
                if dist > search_radius:
                    logger.debug("filter: dropped %s (%.0f miles > %d radius)", rec.title, dist, search_radius)
                    continue

        passed.append(rec)

    return passed
