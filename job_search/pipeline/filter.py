"""Pre-ranking job filter applied before the LLM to save API spend.

Drops jobs where:
- salary_max < salary_floor_gbp (keeps nulls -- many real jobs omit salary)
- posted_date < today - max_days_since_posted
- closes_on is already in the past (application window has closed)
- company in exclude_companies or in cooldown (rejected within last N days)
- location matches a word in profile["filters"]["location_excludes"]
- location carries a clear non-UK signal (foreign country/state/city) and
  filters.drop_foreign_locations is not set to false -- ATS boards list
  worldwide postings and geocoding is GB-restricted, so this text guard is
  the only thing keeping foreign jobs away from the LLM
- the job is remote and remote_ok=False
- the job is on-site with known coordinates further than search_radius_miles
  from home (remote_ok does not bypass the radius for on-site jobs)
- title matches a word in profile["negative_signals"]["title_excludes"] (word-boundary matched)
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import Counter
from datetime import date, timedelta

from job_search.util.geocode import distance_miles

logger = logging.getLogger(__name__)

_REMOTE_TOKENS = frozenset(["remote", "work from home", "wfh", "fully remote", "home based"])

# ---------------------------------------------------------------------------
# Foreign-location guard
#
# The pipeline is UK-scoped (geocoding, Adzuna /gb/, Reed), but the generic
# ATS adapters (Greenhouse/Lever/Workday) return every posting worldwide.
# Geocoding is restricted to countrycodes=gb, so foreign jobs arrive with no
# coordinates and would otherwise get the benefit of the doubt. This guard
# drops locations with an unambiguous foreign signal. Anything ambiguous
# (e.g. "Boston", "Cambridge", "Perth" -- all real UK towns) is deliberately
# NOT listed: precision over recall, missed jobs just cost one LLM score.
# Opt out with profile filters.drop_foreign_locations: false.
# ---------------------------------------------------------------------------

# A UK signal anywhere in the location short-circuits the guard, so
# "Belfast, Northern Ireland" is never caught by "Ireland" below.
_UK_SIGNAL = re.compile(
    r"\b(?:uk|gb|united kingdom|great britain|england|scotland|wales|northern ireland)\b",
    re.IGNORECASE,
)

_FOREIGN_COUNTRIES = (
    "united states", "usa", "america", "canada", "mexico", "brazil", "argentina",
    "chile", "colombia", "peru", "uruguay", "ecuador", "venezuela", "bolivia",
    "costa rica", "panama", "guatemala", "puerto rico",
    "ireland", "france", "germany", "netherlands", "belgium", "luxembourg",
    "spain", "portugal", "italy", "switzerland", "austria", "poland",
    "czech republic", "czechia", "slovakia", "hungary", "romania", "bulgaria",
    "greece", "croatia", "serbia", "ukraine", "estonia", "latvia", "lithuania",
    "denmark", "sweden", "norway", "finland", "iceland", "slovenia", "malta",
    "cyprus", "albania", "belarus", "moldova",
    "turkey", "israel", "united arab emirates", "uae", "saudi arabia", "qatar",
    "kuwait", "bahrain", "oman", "jordan", "lebanon", "egypt",
    "south africa", "nigeria", "kenya", "ghana", "morocco", "tunisia",
    "india", "pakistan", "bangladesh", "sri lanka", "nepal",
    "china", "hong kong", "taiwan", "japan", "south korea", "singapore",
    "malaysia", "thailand", "vietnam", "philippines", "indonesia",
    "kazakhstan", "uzbekistan", "armenia", "azerbaijan",
    "australia", "new zealand",
)

# Full US state names that have no UK namesake. Washington (Tyne and Wear),
# Virginia (Virginia Water) and Maryland (London) are deliberately omitted.
_FOREIGN_STATES = (
    "california", "texas", "florida", "arizona", "oregon", "nevada", "utah",
    "colorado", "oklahoma", "alabama", "alaska", "hawaii", "michigan",
    "wisconsin", "minnesota", "illinois", "ohio", "kentucky", "tennessee",
    "massachusetts", "connecticut", "pennsylvania", "new york", "new jersey",
    "new hampshire", "new mexico", "north carolina", "south carolina",
    "north dakota", "south dakota", "nebraska", "kansas", "missouri",
    "arkansas", "louisiana", "mississippi", "montana", "wyoming", "idaho",
    "iowa", "indiana", "vermont", "maine", "delaware", "rhode island",
)

# Major foreign cities that ATS boards list without a country and that have
# no UK town of the same name. Waterloo (London), Portland (Dorset),
# Auckland (Bishop Auckland), Denver (Norfolk), Philadelphia (Tyne & Wear),
# Dallas (Moray) and Houston (Renfrewshire) are deliberately omitted --
# their US/foreign forms almost always carry a state/country anyway.
_FOREIGN_CITIES = (
    "san francisco", "san jose", "santa clara", "palo alto", "mountain view",
    "sunnyvale", "los angeles", "san diego", "seattle", "austin",
    "chicago", "atlanta", "phoenix",
    "pittsburgh", "minneapolis", "miami", "nashville", "raleigh",
    "toronto", "vancouver", "montreal", "ottawa",
    "dublin", "cork", "galway", "amsterdam", "eindhoven", "rotterdam",
    "paris", "munich", "berlin", "hamburg", "frankfurt", "stuttgart",
    "zurich", "geneva", "vienna", "madrid", "barcelona", "milan", "rome",
    "lisbon", "warsaw", "krakow", "wroclaw", "gdansk", "prague", "budapest",
    "bucharest", "stockholm", "copenhagen", "oslo", "helsinki", "tallinn",
    "tel aviv", "dubai", "abu dhabi", "riyadh",
    "bengaluru", "bangalore", "hyderabad", "mumbai", "pune", "chennai",
    "gurgaon", "noida", "new delhi",
    "shanghai", "beijing", "shenzhen", "tokyo", "osaka", "seoul", "taipei",
    "hsinchu", "sydney", "brisbane",
)

_FOREIGN_NAMES = re.compile(
    r"\b(?:" + "|".join(
        re.escape(t) for t in _FOREIGN_COUNTRIES + _FOREIGN_STATES + _FOREIGN_CITIES
    ) + r")\b",
    re.IGNORECASE,
)

# ", CA" / ", TX 78701" style US-state and Canadian-province codes. Matched
# case-sensitively and only after a comma so UK postcode areas ("NE1 4ST" --
# no comma+space boundary, digit blocks \b) can't false-positive.
_FOREIGN_STATE_CODES = re.compile(
    r",\s?(?:"
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|MA|MD|ME|MI|MN|MO|"
    "MS|MT|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|"
    "WV|WY|DC|ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU"
    r")\b"
)

# Standalone "US" / "U.S." / "U.S.A." -- case-sensitive to be safe.
_FOREIGN_US_ABBREV = re.compile(r"\b(?:US|U\.S\.A?\.?)(?![A-Za-z])")


def _is_foreign_location(location: str) -> bool:
    """Return True if the location text carries a clear non-UK signal."""
    if not location:
        return False
    if _UK_SIGNAL.search(location):
        return False
    return bool(
        _FOREIGN_NAMES.search(location)
        or _FOREIGN_STATE_CODES.search(location)
        or _FOREIGN_US_ABBREV.search(location)
    )


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
    drop_foreign = filters.get("drop_foreign_locations", True)
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
    drops: Counter[str] = Counter()

    for rec in records:
        if rec.salary_max is not None and salary_floor and rec.salary_max < salary_floor:
            logger.debug(
                "filter: dropped %s (salary %s < floor %s)",
                rec.title, rec.salary_max, salary_floor,
            )
            drops["salary_below_floor"] += 1
            continue

        if rec.posted_date and (today - rec.posted_date).days > max_days:
            logger.debug(
                "filter: dropped %s (posted %s > %d days ago)",
                rec.title, rec.posted_date, max_days,
            )
            drops["posted_too_old"] += 1
            continue

        if rec.closes_on and rec.closes_on < today:
            logger.debug(
                "filter: dropped %s (closed %s)", rec.title, rec.closes_on,
            )
            drops["closing_date_passed"] += 1
            continue

        company_lower = rec.company.lower()
        if company_lower in exclude_companies or company_lower in company_blocklist:
            logger.debug("filter: dropped %s (company blocklisted)", rec.company)
            drops["company_blocklisted"] += 1
            continue

        if not _title_is_relevant(rec.title or "", title_exclude_pattern):
            logger.debug("filter: dropped %s (title matches title_excludes)", rec.title)
            drops["title_excluded"] += 1
            continue

        if company_lower in cooldown:
            logger.debug("filter: dropped %s (company in rejection cooldown)", rec.company)
            drops["company_in_cooldown"] += 1
            continue

        location = rec.location or ""
        if location_exclude_pattern is not None and location_exclude_pattern.search(location):
            logger.debug("filter: dropped %s (location %r excluded)", rec.title, location)
            drops["location_excluded"] += 1
            continue

        if drop_foreign and _is_foreign_location(location):
            logger.debug("filter: dropped %s (foreign location %r)", rec.title, location)
            drops["foreign_location"] += 1
            continue

        if _is_remote(location):
            # Remote job: keep only if the profile wants remote roles.
            if not remote_ok:
                logger.debug("filter: dropped %s (remote, remote_ok=false)", rec.title)
                drops["remote_not_ok"] += 1
                continue
        else:
            # On-site job: apply the search radius whenever coordinates are
            # known, regardless of remote_ok. Jobs without coordinates get the
            # benefit of the doubt.
            if rec.lat is not None and rec.lon is not None and home_lat and home_lon:
                dist = distance_miles(home_lat, home_lon, rec.lat, rec.lon)
                if dist > search_radius:
                    logger.debug(
                        "filter: dropped %s (%.0f miles > %d radius)",
                        rec.title, dist, search_radius,
                    )
                    drops["beyond_radius"] += 1
                    continue

        passed.append(rec)

    if drops:
        logger.info(
            "filter: dropped %d/%d jobs (%s)",
            sum(drops.values()),
            len(records),
            ", ".join(f"{reason}: {n}" for reason, n in drops.most_common()),
        )

    return passed
