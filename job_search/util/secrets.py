"""Helpers for distinguishing configured secrets from placeholders."""

from __future__ import annotations


def looks_configured_secret(value: str | None) -> bool:
    """Return True when an env secret looks intentionally configured."""
    if not value:
        return False

    normalised = value.strip()
    if not normalised:
        return False

    lowered = normalised.lower()
    return "..." not in normalised and not lowered.startswith("your_")
