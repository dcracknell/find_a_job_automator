"""Tests for building profile settings from GitHub Issue Forms."""

from __future__ import annotations

import json

from job_search.profile import issue_profile


def test_parse_issue_sections() -> None:
    sections = issue_profile.parse_issue_sections(
        "\ufeff"
        """### Domain
general

### Core target roles
Data Analyst
Reporting Analyst
"""
    )

    assert sections["domain"] == "general"
    assert sections["core target roles"] == "Data Analyst\nReporting Analyst"


def test_build_profile_from_issue_applies_manual_preferences(monkeypatch, tmp_path) -> None:
    profile_path = tmp_path / "profile.json"
    monkeypatch.setattr(issue_profile, "_PROFILE_PATH", profile_path)
    monkeypatch.setattr(
        issue_profile,
        "load_profile",
        lambda: {
            "name": "Candidate",
            "domain": "general",
            "location": {"city": "", "lat": None, "lon": None},
            "search_radius_miles": 60,
            "remote_ok": True,
            "core_skills": [],
            "adjacent_skills": [],
            "negative_signals": {
                "title_excludes": [],
                "description_excludes": [],
                "company_blocklist": [],
            },
            "target_roles": {"core": [], "adjacent": [], "stretch": []},
            "filters": {
                "salary_floor_gbp": 0,
                "exclude_companies": [],
            },
        },
    )

    profile = issue_profile.build_profile_from_issue(
        """### Domain
engineering

### CV text
_No response_

### Search city
Leeds

### Search radius miles
25

### Remote jobs
No

### Minimum salary GBP
30000

### Core target roles
Junior Data Analyst
Data Analyst

### Core skills
Python, SQL

### Title words to exclude
senior
lead

### Companies to exclude
Example Ltd
"""
    )

    assert profile["domain"] == "engineering"
    assert profile["location"]["city"] == "Leeds"
    assert profile["search_radius_miles"] == 25
    assert profile["remote_ok"] is False
    assert profile["filters"]["salary_floor_gbp"] == 30000
    assert profile["target_roles"]["core"] == ["Junior Data Analyst", "Data Analyst"]
    assert profile["core_skills"] == ["Python", "SQL"]
    assert profile["negative_signals"]["title_excludes"] == ["senior", "lead"]
    assert profile["filters"]["exclude_companies"] == ["Example Ltd"]
    assert json.loads(profile_path.read_text(encoding="utf-8"))["domain"] == "engineering"
