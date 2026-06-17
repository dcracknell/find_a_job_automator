"""Generate search query variants from profile.json.

Claude is preferred when ANTHROPIC_API_KEY is configured because it can turn the
CV-derived profile into more targeted search phrases than the deterministic
fallback. The fallback stays deliberately simple and local so job searches still
run without an API key.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from job_search.util.quota import api_call_wrapper
from job_search.util.secrets import looks_configured_secret

logger = logging.getLogger(__name__)

_JUNIOR_MODIFIERS = ["junior", "graduate", "entry level", "junior/graduate"]
_DEFAULT_MAX_QUERIES = 30


def generate_queries(
    profile: dict,
    settings: dict | None = None,
    domain_context: str = "",
) -> list[str]:
    """Return search query strings derived from the profile.

    When settings are supplied and ANTHROPIC_API_KEY is configured, Claude gets
    first pass at producing precise job-board search queries. If that fails or
    returns too little, deterministic queries are used as a supplement/fallback.
    """
    query_cfg = (settings or {}).get("models", {}).get("queries", {})
    max_queries = int(query_cfg.get("max_queries", _DEFAULT_MAX_QUERIES))
    fallback_queries = _fallback_queries(profile, max_queries=max_queries)

    if not settings or not query_cfg.get("use_claude", True):
        return fallback_queries

    claude_queries = _generate_queries_with_claude(
        profile=profile,
        settings=settings,
        domain_context=domain_context,
        fallback_queries=fallback_queries,
        max_queries=max_queries,
    )
    if not claude_queries:
        return fallback_queries

    return _normalise_queries(
        [*claude_queries, *fallback_queries],
        profile=profile,
        max_queries=max_queries,
    )


def _fallback_queries(profile: dict, max_queries: int = _DEFAULT_MAX_QUERIES) -> list[str]:
    """Deterministic query generation used when Claude is unavailable."""
    target_roles = profile.get("target_roles", {})
    core_roles = target_roles.get("core", [])
    adjacent_roles = target_roles.get("adjacent", [])
    stretch_roles = target_roles.get("stretch", [])

    location_info = profile.get("location", {})
    city = location_info.get("city", "")
    remote_ok = profile.get("remote_ok", True)

    queries: list[str] = []

    # Core roles get full treatment: bare + city + junior modifiers.
    for role in core_roles:
        queries.append(role)
        if city:
            queries.append(f"{role} {city}")
        for mod in _JUNIOR_MODIFIERS:
            queries.append(f"{mod} {role}")
        if remote_ok:
            queries.append(f"{role} remote")

    # Adjacent roles: bare + city + remote.
    for role in adjacent_roles:
        queries.append(role)
        if city:
            queries.append(f"{role} {city}")
        if remote_ok:
            queries.append(f"{role} remote")

    # Stretch roles: bare + city only.
    for role in stretch_roles:
        queries.append(role)
        if city:
            queries.append(f"{role} {city}")

    # Add skill-based queries using core_skills.
    core_skills = profile.get("core_skills", [])
    for skill in core_skills[:5]:
        queries.append(f"{skill} engineer")
        if city:
            queries.append(f"{skill} {city}")
        if remote_ok:
            queries.append(f"{skill} remote")

    return _normalise_queries(queries, profile=profile, max_queries=max_queries)


def _generate_queries_with_claude(
    profile: dict,
    settings: dict,
    domain_context: str,
    fallback_queries: list[str],
    max_queries: int,
) -> list[str]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not looks_configured_secret(api_key):
        logger.info("queries: ANTHROPIC_API_KEY not configured; using deterministic queries")
        return []

    try:
        import anthropic
    except ImportError:
        logger.warning("queries: anthropic package not installed; using deterministic queries")
        return []

    query_cfg = settings.get("models", {}).get("queries", {})
    model = query_cfg.get("model", "claude-haiku-4-5-20251001")
    max_tokens = int(query_cfg.get("max_tokens_response", 512))

    client = anthropic.Anthropic(api_key=api_key)
    prompt = _build_query_prompt(profile, domain_context, fallback_queries, max_queries)

    try:
        with api_call_wrapper("queries") as rec:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=_QUERY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            rec["model"] = model
            rec["input_tokens"] = response.usage.input_tokens
            rec["cached_input_tokens"] = getattr(response.usage, "cache_read_input_tokens", 0)
            rec["output_tokens"] = response.usage.output_tokens
    except Exception as exc:
        logger.warning("queries: Claude query generation failed: %s", exc)
        return []

    text = response.content[0].text.strip()
    queries = _parse_query_response(text)
    return _normalise_queries(queries, profile=profile, max_queries=max_queries)


_QUERY_SYSTEM_PROMPT = (
    "You are a UK job-search strategist. Generate search phrases for job-board APIs "
    "from a candidate CV/profile. Prefer precise role and skill combinations that "
    "match the candidate's proven experience. Avoid excluded seniority, blocked "
    "companies, and vague one-word searches. Return JSON only."
)


def _build_query_prompt(
    profile: dict,
    domain_context: str,
    fallback_queries: list[str],
    max_queries: int,
) -> str:
    profile_json = json.dumps(profile, separators=(",", ":"), sort_keys=True)
    fallback_json = json.dumps(fallback_queries[:12], separators=(",", ":"))
    return (
        f"Create up to {max_queries} unique job search queries for this candidate.\n"
        "Rules:\n"
        "- Output a JSON array of strings only.\n"
        "- Prioritise exact target roles, proven core skills, location, and remote preference.\n"
        "- Include role+skill combinations that would find accurate matches to the CV.\n"
        "- Include adjacent roles only when the profile supports them.\n"
        "- Do not include seniority/title exclude terms or blocked companies.\n"
        "- Keep each query short enough for job boards, ideally under 80 characters.\n"
        "- Use UK-friendly wording.\n\n"
        f"Candidate profile JSON:\n{profile_json}\n\n"
        f"Domain ranking context:\n{domain_context or '(none)'}\n\n"
        f"Fallback examples to improve, not blindly copy:\n{fallback_json}"
    )


def _parse_query_response(raw_text: str) -> list[str]:
    text = raw_text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip()

    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("queries: invalid JSON from Claude: %s; raw=%r", exc, raw_text[:300])
        return []

    if isinstance(parsed, dict):
        parsed = parsed.get("queries", [])
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str) and item.strip()]


def _normalise_queries(
    queries: list[str],
    profile: dict,
    max_queries: int = _DEFAULT_MAX_QUERIES,
) -> list[str]:
    negative = profile.get("negative_signals", {})
    title_excludes = [t.lower() for t in negative.get("title_excludes", [])]
    company_blocklist = [c.lower() for c in negative.get("company_blocklist", [])]
    filters = profile.get("filters", {})
    excluded_companies = [c.lower() for c in filters.get("exclude_companies", [])]

    clean: list[str] = []
    seen: set[str] = set()
    for query in queries:
        query = " ".join(str(query).strip().split())
        if not query:
            continue
        lowered = query.lower()
        if lowered in seen:
            continue
        if any(term and term in lowered for term in title_excludes):
            continue
        if any(company and company in lowered for company in company_blocklist):
            continue
        if any(company and company in lowered for company in excluded_companies):
            continue
        seen.add(lowered)
        clean.append(query[:100])
        if len(clean) >= max_queries:
            break
    return clean
