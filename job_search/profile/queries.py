"""Generate search query variants from profile.json.

Pure function — no API call. Combines role names with locations and seniority words
to produce 10-30 query strings used by every adapter.
"""

from __future__ import annotations


_JUNIOR_MODIFIERS = ["junior", "graduate", "entry level", "junior/graduate"]
_GENERAL_MODIFIERS = ["", "UK"]


def generate_queries(profile: dict) -> list[str]:
    """Return a list of search query strings derived from the profile.

    Combines target_roles (core/adjacent/stretch) with location and
    relevant seniority modifiers. Pure function, no side effects.
    """
    target_roles = profile.get("target_roles", {})
    core_roles = target_roles.get("core", [])
    adjacent_roles = target_roles.get("adjacent", [])
    stretch_roles = target_roles.get("stretch", [])

    location_info = profile.get("location", {})
    city = location_info.get("city", "")
    remote_ok = profile.get("remote_ok", True)

    negative = profile.get("negative_signals", {})
    title_excludes = [t.lower() for t in negative.get("title_excludes", [])]

    queries: list[str] = []
    seen: set[str] = set()

    def add_query(q: str) -> None:
        q = q.strip()
        if q and q not in seen:
            # Don't generate queries containing excluded seniority terms
            if not any(excl in q.lower() for excl in title_excludes):
                seen.add(q)
                queries.append(q)

    # Core roles get full treatment: bare + city + junior modifiers
    for role in core_roles:
        add_query(role)
        if city:
            add_query(f"{role} {city}")
        for mod in _JUNIOR_MODIFIERS:
            add_query(f"{mod} {role}")
        if remote_ok:
            add_query(f"{role} remote")

    # Adjacent roles: bare + city
    for role in adjacent_roles:
        add_query(role)
        if city:
            add_query(f"{role} {city}")
        if remote_ok:
            add_query(f"{role} remote")

    # Stretch roles: bare only
    for role in stretch_roles:
        add_query(role)
        if city:
            add_query(f"{role} {city}")

    # Add skill-based queries using core_skills (up to 3)
    core_skills = profile.get("core_skills", [])
    for skill in core_skills[:3]:
        add_query(f"{skill} engineer")
        if city:
            add_query(f"{skill} {city}")

    return queries[:30]  # cap at 30 to avoid excessive API calls
