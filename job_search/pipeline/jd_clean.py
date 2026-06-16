"""Strip and truncate job descriptions before sending to the LLM.

Token-reduction tactics applied here:
- Strip HTML tags
- Normalise whitespace
- Remove boilerplate sections (equal opportunity, benefits, company history)
- Middle-truncate to max_tokens (default 1500), preserving start and end
- Compute jd_content_hash = sha1 of the cleaned text
"""

from __future__ import annotations

import hashlib
import re

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False

_BOILERPLATE_PATTERNS = [
    # Equal opportunities
    r"equal\s+opportunit(?:y|ies)[^.]{0,300}\.",
    r"we\s+(?:are|value|celebrate)\s+(?:an\s+)?divers(?:ity|e)[^.]{0,300}\.",
    r"(?:disability|reasonable\s+adjustment)[^.]{0,200}\.",
    # Benefits boilerplate
    r"(?:we\s+offer|our\s+benefits\s+include|what\s+we\s+offer)[^.]{0,500}\.",
    # Company history
    r"(?:founded\s+in\s+\d{4}|established\s+in\s+\d{4})[^.]{0,300}\.",
    # Application instructions
    r"(?:to\s+apply|how\s+to\s+apply|please\s+(?:send|email|submit))[^.]{0,300}\.",
    r"(?:attach|include)\s+(?:your\s+)?(?:cv|resume|cover\s+letter)[^.]{0,200}\.",
]

_BOILERPLATE_RES = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _BOILERPLATE_PATTERNS]

# Rough token estimate: 1 token ≈ 4 characters for English text
_CHARS_PER_TOKEN = 4


def _strip_html(html: str) -> str:
    if _HAS_BS4:
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator=" ")
    # Fallback: regex-based HTML stripping
    text = re.sub(r"<[^>]+>", " ", html)
    return text


def _remove_boilerplate(text: str) -> str:
    for pattern in _BOILERPLATE_RES:
        text = pattern.sub(" ", text)
    return text


def _normalise_whitespace(text: str) -> str:
    text = re.sub(r"[^\S\n]+", " ", text)   # collapse spaces
    text = re.sub(r"\n{3,}", "\n\n", text)  # max two newlines in a row
    return text.strip()


def _middle_truncate(text: str, max_chars: int) -> str:
    """Keep the first 60% and last 40% of allowed characters, bridged by a marker."""
    if len(text) <= max_chars:
        return text
    keep_start = int(max_chars * 0.60)
    keep_end = max_chars - keep_start
    return text[:keep_start] + "\n[... truncated ...]\n" + text[-keep_end:]


def clean_jd(raw_html: str, max_tokens: int = 1500) -> tuple[str, str]:
    """Strip, truncate, and hash a job description.

    Returns (cleaned_text, jd_content_hash). The hash is used to skip
    re-ranking when the JD hasn't meaningfully changed between scrapes.
    """
    text = _strip_html(raw_html)
    text = _remove_boilerplate(text)
    text = _normalise_whitespace(text)
    text = _middle_truncate(text, max_tokens * _CHARS_PER_TOKEN)
    content_hash = jd_hash(text)
    return text, content_hash


def jd_hash(text: str) -> str:
    """Compute sha1 of normalised JD text."""
    return hashlib.sha1(text.strip().lower().encode()).hexdigest()
