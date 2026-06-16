"""Build config/profile.json from a GitHub Issue Form submission."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import requests

from job_search import PROJECT_ROOT, load_profile
from job_search.profile.parse_cv import _read_pdf, parse_cv_text

_PROFILE_PATH = PROJECT_ROOT / "config" / "profile.json"
_NO_RESPONSE = {"", "_No response_", "No response"}
_URL_RE = re.compile(r"https://[^\s)>\"]+")
_SECTION_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)


def _normalise_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def parse_issue_sections(body: str) -> dict[str, str]:
    """Return a mapping of normalised GitHub Issue Form headings to content."""
    body = body.lstrip("\ufeff")
    matches = list(_SECTION_RE.finditer(body))
    sections: dict[str, str] = {}

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[_normalise_heading(match.group(1))] = body[start:end].strip()

    return sections


def _first_section(sections: dict[str, str], names: list[str]) -> str:
    for name in names:
        value = sections.get(_normalise_heading(name), "").strip()
        if value not in _NO_RESPONSE:
            return value
    return ""


def _split_list(value: str) -> list[str]:
    items = re.split(r"[\n,;]+", value)
    return [item.strip(" -*\t\r") for item in items if item.strip(" -*\t\r")]


def _parse_bool(value: str, default: bool) -> bool:
    lowered = value.strip().lower()
    if lowered in {"yes", "true", "y", "1"}:
        return True
    if lowered in {"no", "false", "n", "0"}:
        return False
    return default


def _parse_int(value: str) -> int | None:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


def _download_text_readable_pdf(body: str, work_dir: Path) -> str:
    """Download the first linked PDF-ish attachment and extract text."""
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    for url in _URL_RE.findall(body):
        if "github.com/user-attachments/" not in url and ".pdf" not in url.lower():
            continue

        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()

        pdf_path = work_dir / "cv.pdf"
        pdf_path.write_bytes(response.content)
        text = _read_pdf(pdf_path)
        if text.strip():
            return text

    return ""


def _load_base_profile() -> dict[str, Any]:
    try:
        return load_profile()
    except FileNotFoundError:
        return {
            "name": "Your Name",
            "domain": "general",
            "secondary_domains": [],
            "location": {"city": "", "lat": None, "lon": None},
            "search_radius_miles": 60,
            "remote_ok": True,
            "education": {},
            "experience_years": 0,
            "experience_summary": "",
            "core_skills": [],
            "adjacent_skills": [],
            "negative_signals": {
                "title_excludes": ["senior", "staff", "principal", "lead", "head of", "director"],
                "requires_years_above": 3,
                "description_excludes": [],
                "company_blocklist": [],
            },
            "target_roles": {"core": [], "adjacent": [], "stretch": []},
            "filters": {
                "salary_floor_gbp": 0,
                "salary_unit": "annual",
                "max_days_since_posted": 30,
                "exclude_companies": [],
                "rejected_company_cooldown_days": 90,
            },
        }


def _apply_manual_preferences(profile: dict[str, Any], sections: dict[str, str]) -> dict[str, Any]:
    """Overlay search preferences from the issue form onto a parsed/base profile."""
    domain = _first_section(sections, ["Domain"])
    if domain:
        profile["domain"] = domain.strip().lower()

    city = _first_section(sections, ["Search city"])
    if city:
        profile.setdefault("location", {})["city"] = city.strip()

    radius = _parse_int(_first_section(sections, ["Search radius miles"]))
    if radius is not None:
        profile["search_radius_miles"] = radius

    remote = _first_section(sections, ["Remote jobs"])
    if remote:
        profile["remote_ok"] = _parse_bool(remote, bool(profile.get("remote_ok", True)))

    salary_floor = _parse_int(_first_section(sections, ["Minimum salary GBP"]))
    if salary_floor is not None:
        profile.setdefault("filters", {})["salary_floor_gbp"] = salary_floor

    list_fields = [
        ("Core target roles", ("target_roles", "core")),
        ("Adjacent target roles", ("target_roles", "adjacent")),
        ("Stretch target roles", ("target_roles", "stretch")),
        ("Core skills", ("core_skills",)),
        ("Adjacent skills", ("adjacent_skills",)),
        ("Title words to exclude", ("negative_signals", "title_excludes")),
        ("Description terms to exclude", ("negative_signals", "description_excludes")),
        ("Companies to exclude", ("filters", "exclude_companies")),
    ]

    for section_name, path in list_fields:
        value = _first_section(sections, [section_name])
        if not value:
            continue

        target: dict[str, Any] = profile
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = _split_list(value)

    profile.setdefault("negative_signals", {}).setdefault("company_blocklist", [])
    return profile


def build_profile_from_issue(body: str) -> dict[str, Any]:
    """Parse a GitHub profile issue, update config/profile.json, and return it."""
    sections = parse_issue_sections(body)
    domain = _first_section(sections, ["Domain"]) or "general"
    domain = domain.strip().lower()

    cv_text = _first_section(sections, ["CV text"])
    if not cv_text:
        with tempfile.TemporaryDirectory() as temp_dir:
            cv_text = _download_text_readable_pdf(body, Path(temp_dir))

    if cv_text:
        profile = parse_cv_text(cv_text, domain=domain, write=False)
    else:
        profile = _load_base_profile()
        profile["domain"] = domain

    profile = _apply_manual_preferences(profile, sections)

    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILE_PATH.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    return profile
