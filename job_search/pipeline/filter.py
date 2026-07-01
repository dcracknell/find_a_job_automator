"""Pre-ranking job filter applied before the LLM to save API spend.

Drops jobs where:
- salary_max < salary_floor_gbp (keeps nulls -- many real jobs omit salary)
- posted_date < today - max_days_since_posted
- company in exclude_companies or in cooldown (rejected within last N days)
- distance from home > search_radius_miles AND remote_ok=False AND not remote location
- title matches a word in profile["negative_signals"]["title_excludes"] (word-boundary matched)
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


def _build_title_exclude_pattern(title_excludes: list[str]) -> re.Pattern | None:
        """Compile a word-boundary regex from the profile's title_excludes list.

            Uses \\b so that e.g. "lead" in title_excludes does NOT block "leading"
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


def _title_is_relevant(title: str, title_exclude_pattern: re.Pattern | None) -> bool:
        """Return False if the title contains any word from profile title_excludes.

            All other titles are allowed through -- the function is intentionally
                permissive so that hardware/FPGA/embedded roles are never silently dropped
                    based on hard-coded assumptions about what constitutes a relevant role.
                        Hard exclusion authority belongs solely to the user-controlled profile.
                            """
    if title_exclude_pattern is not None and title_exclude_pattern.search(title):
                return False
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

    title_excludes: list[str] = negative.get("title_excludes", [])
    title_exclude_pattern = _build_title_exclude_pattern(title_excludes)

    passed: list[JobRecord] = []

    for rec in records:
                if rec.salary_max is not None and salary_floor and rec.salary_max < salary_floor:
                                logger.debug("filter: dropped %s (salary %s < floor %s)", rec.title, rec.salary_max, salary_floor)
                                continue

                if rec.posted_date and (today - rec.posted_date).days > max_days:
                                logger.debug("filter: dropped %s (posted %s > %d days ago)", rec.title, rec.posted_date, max_days)
                                continue

                company_lower = rec.company.lower()
                if company_lower in exclude_companies or company_lower in company_blocklist:
                                logger.debug("filter: dropped %s (company blocklisted)", rec.company)
                                continue

                if not _title_is_relevant(rec.title or "", title_exclude_pattern):
                                logger.debug("filter: dropped %s (title matches title_excludes)", rec.title)
                                continue

                if _company_in_cooldown(conn, rec.company, cooldown_days):
                                logger.debug("filter: dropped %s (company in rejection cooldown)", rec.company)
                                continue

                if not remote_ok and not _is_remote(rec.location or ""):
                                if rec.lat is not None and rec.lon is not None and home_lat and home_lon:
                                                    from job_search.util.geocode import distance_miles
                                                    dist = distance_miles(home_lat, home_lon, rec.lat, rec.lon)
                                                    if dist > search_radius:
                                                                            logger.debug("filter: dropped %s (%.0f miles > %d radius)", rec.title, dist, search_radius)
                                                                            continue

                                            passed.append(rec)

            return passed
