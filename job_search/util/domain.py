"""Load, validate, and merge domain packs from config/domains/.

Domain packs are YAML files in config/domains/ validated against DomainPack (Pydantic).
Users select a domain in profile.json:domain. Multi-domain users set secondary_domains.
"""

from __future__ import annotations

import logging
from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from job_search import PROJECT_ROOT

logger = logging.getLogger(__name__)

DOMAINS_DIR: Path = PROJECT_ROOT / "config" / "domains"


# ---------------------------------------------------------------------------
# Pydantic schema for domain packs
# ---------------------------------------------------------------------------


class DomainAdapterConfig(BaseModel):
    enabled: bool = True
    base_url: str | None = None


class DomainSalary(BaseModel):
    default_unit: str = "annual_gbp"
    also_parse: list[str] = Field(default_factory=list)
    agenda_for_change_bands: bool = False
    hourly_to_annual_multiplier: int = 1880
    daily_to_annual_multiplier: int = 230


class DomainSeniorityWords(BaseModel):
    junior: list[str] = Field(default_factory=list)
    mid: list[str] = Field(default_factory=list)
    senior: list[str] = Field(default_factory=list)
    exclude_above_default: str = "senior"


class DomainRecommendedSources(BaseModel):
    enable_by_default: list[str] = Field(default_factory=list)
    disable_by_default: list[str] = Field(default_factory=list)


class DomainExampleSkills(BaseModel):
    core: list[str] = Field(default_factory=list)
    adjacent: list[str] = Field(default_factory=list)


class DomainExampleRoles(BaseModel):
    core: list[str] = Field(default_factory=list)
    adjacent: list[str] = Field(default_factory=list)
    stretch: list[str] = Field(default_factory=list)


class DomainPack(BaseModel):
    name: str
    display_name: str
    recommended_sources: DomainRecommendedSources = Field(default_factory=DomainRecommendedSources)
    domain_adapters: dict[str, DomainAdapterConfig] = Field(default_factory=dict)
    salary: DomainSalary = Field(default_factory=DomainSalary)
    seniority_words: DomainSeniorityWords = Field(default_factory=DomainSeniorityWords)
    closing_date_patterns: list[str] = Field(default_factory=list)
    ranker_context: str = ""
    cv_parser_context: str = ""
    example_skills: DomainExampleSkills = Field(default_factory=DomainExampleSkills)
    example_target_roles: DomainExampleRoles = Field(default_factory=DomainExampleRoles)


# ---------------------------------------------------------------------------
# Loading and caching
# ---------------------------------------------------------------------------


@cache
def _load_pack_cached(name: str) -> DomainPack:
    """Load a single domain pack by name, caching the result."""
    path = DOMAINS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Domain pack not found: {path}")
    with path.open() as f:
        raw = yaml.safe_load(f)
    try:
        return DomainPack.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Domain pack '{name}' failed validation:\n{exc}") from exc


def load_pack(name: str) -> DomainPack:
    """Load and validate a domain pack by name."""
    return _load_pack_cached(name)


def list_packs() -> list[DomainPack]:
    """Return all available domain packs, sorted by name."""
    packs = []
    for path in sorted(DOMAINS_DIR.glob("*.yaml")):
        try:
            packs.append(load_pack(path.stem))
        except Exception as exc:
            logger.warning("Skipping invalid domain pack %s: %s", path.name, exc)
    return packs


# ---------------------------------------------------------------------------
# Merging (primary + secondary domains)
# ---------------------------------------------------------------------------


def _merge_packs(primary: DomainPack, secondary: DomainPack) -> DomainPack:
    """Merge a secondary domain pack into the primary.

    Merging rules:
    - Scalar fields: primary wins (secondary fills in only if primary is empty/default)
    - ranker_context: concatenated with a section separator
    - cv_parser_context: concatenated with a section separator
    - example_skills: lists merged (primary first, secondary deduplicated)
    - example_target_roles: lists merged (primary first, secondary deduplicated)
    - closing_date_patterns: lists merged (deduplicated)
    - domain_adapters: secondary adapters added only if not already present in primary
    - recommended_sources: enable_by_default merged (deduplicated)
    """

    def _merge_lists(a: list[str], b: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in a + b:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    # Merged ranker and CV contexts
    ranker_context = primary.ranker_context
    if secondary.ranker_context:
        separator = "\n\n--- Additional context from secondary domain ---\n"
        ranker_context = (ranker_context + separator + secondary.ranker_context).strip()

    cv_parser_context = primary.cv_parser_context
    if secondary.cv_parser_context:
        separator = "\n\n--- Additional extraction hints from secondary domain ---\n"
        cv_parser_context = (cv_parser_context + separator + secondary.cv_parser_context).strip()

    # Merged skills and roles
    merged_skills = DomainExampleSkills(
        core=_merge_lists(primary.example_skills.core, secondary.example_skills.core),
        adjacent=_merge_lists(primary.example_skills.adjacent, secondary.example_skills.adjacent),
    )
    merged_roles = DomainExampleRoles(
        core=_merge_lists(primary.example_target_roles.core, secondary.example_target_roles.core),
        adjacent=_merge_lists(
            primary.example_target_roles.adjacent,
            secondary.example_target_roles.adjacent,
        ),
        stretch=_merge_lists(
            primary.example_target_roles.stretch,
            secondary.example_target_roles.stretch,
        ),
    )

    # Merged adapters (primary wins on conflicts)
    merged_adapters = dict(secondary.domain_adapters)
    merged_adapters.update(primary.domain_adapters)

    # Merged recommended sources
    merged_sources = DomainRecommendedSources(
        enable_by_default=_merge_lists(
            primary.recommended_sources.enable_by_default,
            secondary.recommended_sources.enable_by_default,
        ),
        disable_by_default=_merge_lists(
            primary.recommended_sources.disable_by_default,
            secondary.recommended_sources.disable_by_default,
        ),
    )

    # Merged closing date patterns
    merged_patterns = _merge_lists(
        primary.closing_date_patterns, secondary.closing_date_patterns
    )

    return DomainPack(
        name=primary.name,
        display_name=primary.display_name,
        recommended_sources=merged_sources,
        domain_adapters=merged_adapters,
        salary=primary.salary,
        seniority_words=primary.seniority_words,
        closing_date_patterns=merged_patterns,
        ranker_context=ranker_context,
        cv_parser_context=cv_parser_context,
        example_skills=merged_skills,
        example_target_roles=merged_roles,
    )


def get_active_domain(profile: dict) -> DomainPack:
    """Load and return the effective domain pack for the given profile.

    If profile has secondary_domains, merges them into the primary pack.
    Secondary domains extend the primary; primary fields take precedence.
    """
    primary_name: str = profile.get("domain", "general")
    secondary_names: list[str] = profile.get("secondary_domains", [])

    primary = load_pack(primary_name)

    for sec_name in secondary_names:
        try:
            secondary = load_pack(sec_name)
            primary = _merge_packs(primary, secondary)
        except Exception as exc:
            logger.warning("Could not load secondary domain '%s': %s", sec_name, exc)

    return primary
