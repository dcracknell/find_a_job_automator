"""Tests for source enablement from config and secrets."""

from __future__ import annotations

from job_search.cli import _source_enabled


def test_source_enabled_auto_requires_all_secrets(monkeypatch) -> None:
    monkeypatch.setenv("ADZUNA_APP_ID", "real-app-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "sk-real-key")

    assert _source_enabled({"enabled": "auto"}, ("ADZUNA_APP_ID", "ADZUNA_APP_KEY"))


def test_source_enabled_auto_ignores_placeholders(monkeypatch) -> None:
    monkeypatch.setenv("ADZUNA_APP_ID", "your_app_id_here")
    monkeypatch.setenv("ADZUNA_APP_KEY", "sk-real-key")

    assert not _source_enabled({"enabled": "auto"}, ("ADZUNA_APP_ID", "ADZUNA_APP_KEY"))


def test_source_enabled_boolean() -> None:
    assert _source_enabled({"enabled": True})
    assert not _source_enabled({"enabled": False})
