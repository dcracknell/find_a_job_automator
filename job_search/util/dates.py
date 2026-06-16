"""Closing-date extraction helpers.

Uses regex first (free); LLM fallback only for high-fit jobs.
Domain packs extend the regex pattern list via closing_date_patterns.
"""

from __future__ import annotations

import re
from datetime import date, datetime

_DEFAULT_PATTERNS = [
    r"closing date[:\s]+([^\n]+)",
    r"applications? close[:\s]+([^\n]+)",
    r"deadline[:\s]+([^\n]+)",
    r"apply by[:\s]+([^\n]+)",
    r"closes[:\s]+([^\n]+)",
    r"closing on[:\s]+([^\n]+)",
    r"expiry date[:\s]+([^\n]+)",
    r"expires[:\s]+([^\n]+)",
]

_DATE_FORMATS = [
    "%d %B %Y",       # 31 December 2025
    "%d %b %Y",       # 31 Dec 2025
    "%d/%m/%Y",       # 31/12/2025
    "%d-%m-%Y",       # 31-12-2025
    "%Y-%m-%d",       # 2025-12-31
    "%d %B",          # 31 December (no year)
    "%d %b",          # 31 Dec (no year)
    "%d/%m/%y",       # 31/12/25
]

_ORDINAL_RE = re.compile(r"(\d+)(st|nd|rd|th)\b", re.IGNORECASE)


def _try_parse_date(text: str) -> date | None:
    """Attempt to parse a date string from various formats."""
    text = text.strip().rstrip(".")
    text = _ORDINAL_RE.sub(r"\1", text)  # strip ordinal suffixes

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year == 1900:
                # strptime fills missing year with 1900; use current/next year
                today = date.today()
                candidate = dt.replace(year=today.year).date()
                if candidate < today:
                    candidate = candidate.replace(year=today.year + 1)
                return candidate
            return dt.date()
        except ValueError:
            continue
    return None


def extract_closing_date(text: str, domain_patterns: list[str] | None = None) -> date | None:
    """Try to extract a closing date from job description text using regex.

    Returns None if no date found; caller may then use LLM fallback.
    domain_patterns extends the default pattern list from the active domain pack.
    """
    patterns = _DEFAULT_PATTERNS + (domain_patterns or [])

    for pattern in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            candidate_text = m.group(1).strip()
            # Grab just the first line / reasonable segment
            candidate_text = candidate_text.split("\n")[0][:60].strip()
            parsed = _try_parse_date(candidate_text)
            if parsed:
                return parsed

    return None
