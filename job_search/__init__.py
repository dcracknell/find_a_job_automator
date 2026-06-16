"""UK Job Search Pipeline."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).parent.parent


def load_settings() -> dict:
    """Load config/settings.yaml and return as a plain dict."""
    import yaml

    settings_path = PROJECT_ROOT / "config" / "settings.yaml"
    with settings_path.open() as f:
        return yaml.safe_load(f)


def load_profile() -> dict:
    """Load config/profile.json and return as a plain dict."""
    import json

    profile_path = PROJECT_ROOT / "config" / "profile.json"
    with profile_path.open() as f:
        return json.load(f)
