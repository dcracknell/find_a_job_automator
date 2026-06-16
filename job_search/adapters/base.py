"""Adapter ABC and shared data model."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# Raw response from an adapter — format varies per source.
RawJob = dict[str, Any]


@dataclass
class JobRecord:
    """Normalised job posting — the common schema every adapter produces."""

    # --- Identity ---
    job_id: str                    # sha1(company.lower() + title.lower() + canonical_url)
    source: str                    # e.g. "adzuna", "greenhouse:graphcore"

    # --- Posting ---
    title: str
    company: str
    location: str
    lat: float | None
    lon: float | None
    url: str                       # canonical, query params stripped
    description: str               # full JD text
    posted_date: date | None
    closes_on: date | None

    # --- Salary ---
    salary_raw: str | None
    salary_min: int | None         # GBP, annual
    salary_max: int | None

    # --- Ranking (filled in later stages) ---
    fit_score: float | None = None
    fit_reason: str | None = None
    fit_confidence: float | None = None
    matched_keywords: list[str] = field(default_factory=list)
    ranker_version: str | None = None

    # --- Provenance ---
    matched_query: str | None = None
    first_seen: date = field(default_factory=date.today)
    last_seen: date = field(default_factory=date.today)
    jd_content_hash: str | None = None

    @staticmethod
    def make_job_id(company: str, title: str, url: str) -> str:
        """Compute a stable job_id from the three identity fields."""
        raw = company.lower() + title.lower() + url
        return hashlib.sha1(raw.encode()).hexdigest()


class Adapter(ABC):
    """Base class for all job-board adapters."""

    name: str

    @abstractmethod
    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        """Fetch raw job postings for the given search queries."""
        raise NotImplementedError

    @abstractmethod
    def normalise(self, raw: RawJob) -> JobRecord:
        """Convert a raw adapter response into a normalised JobRecord."""
        raise NotImplementedError

    def healthcheck(self) -> tuple[bool, str | None]:
        """Return (ok, error_message). Default implementation tries a trivial fetch."""
        try:
            self.fetch(["test"], {})
            return True, None
        except Exception as exc:
            return False, str(exc)
