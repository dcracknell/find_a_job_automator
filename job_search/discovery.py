"""Automatic career-site discovery.

When a job board surfaces one opening at a company, that company's own careers
site usually holds more — including roles never posted to any board. Most
careers sites run on an ATS with a public, keyless JSON API, so given just a
company name we can probe the common providers by slug and, on a hit, add the
company as a permanent source.

Discovered companies are written to data/discovered_sources.yaml (persisted by
the GitHub Actions data branch) and merged into sources.yaml at load time.
Every probe result — hit or miss — is recorded in the company_probes table so
no company is probed twice.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date
from pathlib import Path

import yaml

from job_search import PROJECT_ROOT
from job_search.util import http

logger = logging.getLogger(__name__)

DISCOVERED_PATH: Path = PROJECT_ROOT / "data" / "discovered_sources.yaml"

# Provider -> (probe URL template, key holding the job list in the response).
# Probed in this order; the first hit wins.
_ATS_PROBES: list[tuple[str, str, str]] = [
    ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", "jobs"),
    ("lever", "https://api.lever.co/v0/postings/{slug}", ""),  # "" = response IS the list
    ("ashby", "https://api.ashbyhq.com/posting-api/job-board/{slug}", "jobs"),
    ("workable", "https://apply.workable.com/api/v1/widget/accounts/{slug}", "results"),
    ("recruitee", "https://{slug}.recruitee.com/api/offers/", "offers"),
    ("smartrecruiters", "https://api.smartrecruiters.com/v1/companies/{slug}/postings", "content"),
]

_LEGAL_SUFFIXES = re.compile(
    r"\b(ltd|limited|plc|inc|incorporated|llc|llp|gmbh|corp|corporation|co|group|holdings)\b\.?",
    re.IGNORECASE,
)


def slug_candidates(company: str) -> list[str]:
    """Return plausible ATS slugs for a company name, most likely first."""
    base = _LEGAL_SUFFIXES.sub(" ", company).strip()
    base = re.sub(r"[^A-Za-z0-9 ]+", " ", base)
    words = [w.lower() for w in base.split() if w]
    if not words:
        return []
    candidates = ["".join(words)]
    if len(words) > 1:
        candidates.append("-".join(words))
        candidates.append(words[0])
    seen: set[str] = set()
    return [c for c in candidates if len(c) >= 3 and not (c in seen or seen.add(c))]


def probe_company(company: str) -> tuple[str, str, int] | None:
    """Try each ATS provider with each slug candidate.

    Returns (provider, slug, job_count) for the first board that responds with
    at least one live posting, or None. Requiring >=1 job guards against slug
    collisions with unrelated (empty) boards.
    """
    for slug in slug_candidates(company):
        for provider, template, list_key in _ATS_PROBES:
            url = template.format(slug=slug)
            try:
                # get_once: a miss is the common case (404/NXDOMAIN) — never retry
                data = http.get_once(url, timeout=15).json()
            except Exception:
                continue  # not on this provider under this slug
            jobs = data if list_key == "" else data.get(list_key)
            if isinstance(jobs, list) and len(jobs) > 0:
                logger.info(
                    "discovery: %s found on %s as '%s' (%d postings)",
                    company, provider, slug, len(jobs),
                )
                return provider, slug, len(jobs)
    return None


# ---------------------------------------------------------------------------
# discovered_sources.yaml
# ---------------------------------------------------------------------------


def _load_discovered(path: Path | None = None) -> dict:
    path = path or DISCOVERED_PATH
    if not path.exists():
        return {"ats": {}}
    try:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("discovery: could not read %s: %s", path, exc)
        return {"ats": {}}
    data.setdefault("ats", {})
    return data


def add_discovered_company(
    provider: str, name: str, slug: str, path: Path | None = None
) -> bool:
    """Append a company to discovered_sources.yaml. Returns False if present."""
    path = path or DISCOVERED_PATH
    data = _load_discovered(path)
    companies = data["ats"].setdefault(provider, {}).setdefault("companies", [])
    if any(c.get("slug") == slug for c in companies):
        return False
    companies.append({"name": name, "slug": slug})

    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Auto-discovered company career sites (job-search discovery).\n"
        "# Merged into sources.yaml at runtime. Safe to edit; delete an entry\n"
        "# to stop scraping that company.\n"
    )
    path.write_text(header + yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return True


def merge_discovered_sources(sources_cfg: dict, path: Path | None = None) -> dict:
    """Merge discovered ATS companies into a sources.yaml-shaped config dict."""
    discovered = _load_discovered(path)
    ats = sources_cfg.setdefault("ats", {})
    for provider, cfg in discovered.get("ats", {}).items():
        target = ats.setdefault(provider, {}).setdefault("companies", [])
        existing = {c.get("slug") for c in target if isinstance(c, dict)}
        for company in cfg.get("companies", []):
            if isinstance(company, dict) and company.get("slug") not in existing:
                target.append(company)
                existing.add(company.get("slug"))
    return sources_cfg


# ---------------------------------------------------------------------------
# DB-driven discovery
# ---------------------------------------------------------------------------


def _configured_identifiers(sources_cfg: dict) -> set[str]:
    """Lowercased names and slugs of every already-configured ATS company."""
    known: set[str] = set()
    for cfg in (sources_cfg.get("ats") or {}).values():
        for company in cfg.get("companies", []) or []:
            if isinstance(company, dict):
                for key in ("name", "slug"):
                    value = company.get(key)
                    if value:
                        known.add(str(value).lower())
    return known


def discover_from_db(
    conn: sqlite3.Connection,
    sources_cfg: dict,
    max_probes: int = 8,
    min_fit_score: float = 5.0,
    path: Path | None = None,
) -> list[tuple[str, str, str]]:
    """Probe careers sites of promising companies seen on job boards.

    Candidates: companies whose stored jobs came from board/aggregator sources
    (not already an ATS feed) with a decent best fit score, never probed
    before, and not already configured. Returns [(company, provider, slug)].
    """
    configured = _configured_identifiers(sources_cfg)
    probed = {
        row["company_lower"]
        for row in conn.execute("SELECT company_lower FROM company_probes")
    }

    rows = conn.execute(
        """
        SELECT company, MAX(COALESCE(fit_score, 0)) AS best
        FROM jobs
        WHERE status NOT IN ('closed', 'rejected', 'ignore', 'archive')
          AND source NOT LIKE 'greenhouse%' AND source NOT LIKE 'lever%'
          AND source NOT LIKE 'workday%' AND source NOT LIKE 'workable%'
          AND source NOT LIKE 'ashby%' AND source NOT LIKE 'recruitee%'
          AND source NOT LIKE 'smartrecruiters%'
        GROUP BY LOWER(company)
        HAVING best >= ?
        ORDER BY best DESC
        """,
        (min_fit_score,),
    ).fetchall()

    found: list[tuple[str, str, str]] = []
    probes_done = 0
    today = date.today().isoformat()

    for row in rows:
        if probes_done >= max_probes:
            break
        company = (row["company"] or "").strip()
        lower = company.lower()
        if not company or lower in probed or lower in configured:
            continue

        probes_done += 1
        result = probe_company(company)
        provider, slug = (result[0], result[1]) if result else (None, None)
        conn.execute(
            "INSERT OR REPLACE INTO company_probes "
            "(company_lower, company, probed_at, ats, slug) VALUES (?, ?, ?, ?, ?)",
            (lower, company, today, provider, slug),
        )
        if provider and slug:
            if add_discovered_company(provider, company, slug, path):
                found.append((company, provider, slug))

    conn.commit()
    return found
