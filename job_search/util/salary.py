"""Salary parsing and normalisation.

Handles multiple units:
- Annual: "£35k", "£35,000", "35000-40000"
- Hourly: "£18/hr", "£18 per hour"
- Daily: "£450/day", "£450 per day"
- Sessional: "£X per session"
- NHS Agenda for Change bands: "Band 5", "Band 6 (£35,392 - £42,618)"
- Sentinel values: "Competitive" → None, "DOE" → None, "Negotiable" → None

All salaries are normalised to annual GBP for ranking/filtering.
Original unit preserved in salary_raw for display.
"""

from __future__ import annotations

import re

_SENTINELS = frozenset(
    [
        "competitive", "doe", "negotiable", "tbc", "tba", "market rate",
        "to be confirmed", "depending on experience", "see advert",
    ]
)

# NHS Agenda for Change band midpoints (England 2024/25), keyed by band number.
_AFC_BANDS: dict[int, tuple[int, int]] = {
    1: (22_383, 22_383),
    2: (23_615, 25_674),
    3: (24_071, 25_674),
    4: (26_530, 29_114),
    5: (29_970, 36_483),
    6: (37_338, 44_962),
    7: (46_148, 52_809),
    8: (53_755, 60_504),   # 8a
    9: (62_215, 72_293),   # 8b
    10: (74_290, 85_601),  # 8c
    11: (88_168, 102_585), # 8d / 9
}

_WORKING_HOURS_PER_YEAR = 1_880
_WORKING_DAYS_PER_YEAR = 230

_BAND_RE = re.compile(r"\bband\s+(\d+[abcd]?)\b", re.IGNORECASE)
_SALARY_RE = re.compile(
    r"£\s*([\d,]+(?:\.\d+)?)\s*k?\b"      # £35k, £35,000, £35.5k
    r"|(\b[\d,]+(?:\.\d+)?)\s*k?\s*(?:-|to|–)\s*"  # range start without £
    r"£?\s*([\d,]+(?:\.\d+)?)\s*k?\b",    # range end
    re.IGNORECASE,
)


def _parse_amount(s: str) -> float:
    """Parse a numeric string (possibly with commas or 'k' suffix) to a float."""
    s = s.replace(",", "").strip()
    multiplier = 1000.0 if s.lower().endswith("k") else 1.0
    return float(s.rstrip("kK")) * multiplier


def _find_amounts(text: str) -> list[float]:
    """Extract all salary amounts from a string."""
    amounts: list[float] = []
    # Match £XX,XXX or £XXk patterns, with optional range separator
    for m in re.finditer(
        r"£\s*([\d,]+(?:\.\d+)?)\s*(k)?",
        text,
        re.IGNORECASE,
    ):
        val = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        amounts.append(val)
    # Also catch bare ranges like "30000 - 40000" or "30k - 40k"
    for m in re.finditer(
        r"\b([\d,]+(?:\.\d+)?)(k?)\s*(?:-|to|–)\s*([\d,]+(?:\.\d+)?)(k?)\b",
        text,
        re.IGNORECASE,
    ):
        lo = float(m.group(1).replace(",", "")) * (1000 if m.group(2).lower() == "k" else 1)
        hi = float(m.group(3).replace(",", "")) * (1000 if m.group(4).lower() == "k" else 1)
        if lo > 5000 and hi > 5000:  # ignore hour-sized numbers
            amounts.extend([lo, hi])
    return amounts


def _detect_unit(text: str) -> str:
    """Guess the salary unit from surrounding text."""
    t = text.lower()
    if any(tok in t for tok in ["/hr", "per hour", "p/h", "hourly", "per hr"]):
        return "hourly"
    if any(tok in t for tok in ["/day", "per day", "daily", "p/d"]):
        return "daily"
    if any(tok in t for tok in ["per session", "/session", "sessional"]):
        return "sessional"
    return "annual"


def _annualise(amount: float, unit: str) -> int:
    if unit == "hourly":
        return int(amount * _WORKING_HOURS_PER_YEAR)
    if unit == "daily":
        return int(amount * _WORKING_DAYS_PER_YEAR)
    if unit == "sessional":
        return int(amount * 400)  # rough: 400 sessions/year for GP-type work
    return int(amount)


def parse_salary(
    raw: str | None,
    domain_pack: dict | None = None,
) -> tuple[int | None, int | None]:
    """Parse a salary string into (min_annual_gbp, max_annual_gbp).

    Returns (None, None) for unparseable or sentinel values.
    Uses domain_pack salary config for unit defaults and AfC band lookup.
    """
    if not raw:
        return None, None

    text = raw.strip()

    # Sentinel check
    if text.lower() in _SENTINELS or not any(c.isdigit() for c in text):
        if text.lower() in _SENTINELS:
            return None, None

    # NHS AfC band
    band_match = _BAND_RE.search(text)
    if band_match:
        band_str = band_match.group(1).lower()
        # Map "8a" → 8, "8b" → 9, "8c" → 10, "8d" → 11, "9" → 11
        band_num_map = {"8a": 8, "8b": 9, "8c": 10, "8d": 11}
        if band_str in band_num_map:
            band_num = band_num_map[band_str]
        else:
            try:
                band_num = int(band_str)
            except ValueError:
                band_num = None
        if band_num and band_num in _AFC_BANDS:
            lo, hi = _AFC_BANDS[band_num]
            # If there's also an explicit range in the text, prefer it
            amounts = _find_amounts(text)
            if len(amounts) >= 2:
                return int(min(amounts)), int(max(amounts))
            return lo, hi
        # If AfC band is requested but domain pack doesn't have it, fall through

    # Determine unit from domain_pack defaults then from text
    default_unit = "annual"
    if domain_pack:
        sal_cfg = domain_pack.get("salary", {})
        unit_str = sal_cfg.get("default_unit", "annual_gbp")
        if "hourly" in unit_str:
            default_unit = "hourly"
        elif "daily" in unit_str:
            default_unit = "daily"
        elif "sessional" in unit_str:
            default_unit = "sessional"

    detected_unit = _detect_unit(text)
    unit = detected_unit if detected_unit != "annual" else default_unit

    amounts = _find_amounts(text)
    if not amounts:
        return None, None

    amounts = sorted(amounts)
    lo = amounts[0]
    hi = amounts[-1] if len(amounts) > 1 else amounts[0]

    # Sanity: discard obviously wrong values
    if unit == "annual" and hi < 5_000:
        # looks like it might be hourly — re-detect
        unit = "hourly"
    if unit == "hourly" and hi > 500:
        # looks like annual
        unit = "annual"

    return _annualise(lo, unit), _annualise(hi, unit)
