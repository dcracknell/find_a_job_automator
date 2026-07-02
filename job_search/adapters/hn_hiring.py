"""HN "Ask HN: Who is hiring?" adapter — monthly thread via the Algolia API.

Free, keyless. Finds the latest monthly thread, pulls its top-level comments
(each one is a job post), keeps those matching the configured location tokens
(UK/remote by default), and parses the conventional
"Company | Role | Location | ..." first line.
"""

from __future__ import annotations

import logging
import re

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http

logger = logging.getLogger(__name__)

_ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
_ITEM_URL = "https://news.ycombinator.com/item?id={comment_id}"

_DEFAULT_LOCATION_TOKENS = [
    "uk", "united kingdom", "london", "cambridge", "oxford", "manchester",
    "edinburgh", "bristol", "leeds", "sheffield", "remote",
]

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub(" ", html or "")


def parse_hiring_comment(comment_text: str) -> dict | None:
    """Parse the conventional "Company | Role | Location | ..." first line.

    Returns {"company", "title", "location"} or None when the comment doesn't
    follow the pipe convention closely enough to identify a job.
    """
    text = _strip_tags(comment_text).strip()
    if not text:
        return None
    first_line = text.split("\n", 1)[0]
    # Comments are HTML; paragraphs arrive as <p>, already stripped to spaces,
    # so cap the "first line" at a sane length.
    first_line = first_line[:200]
    parts = [p.strip() for p in first_line.split("|") if p.strip()]
    if len(parts) < 2:
        return None
    company = parts[0][:80]
    title = parts[1][:120]
    location = parts[2][:80] if len(parts) > 2 else ""
    if not company or not title:
        return None
    return {"company": company, "title": title, "location": location}


class HNHiringAdapter(Adapter):
    """Scrapes the monthly 'Ask HN: Who is Hiring?' thread on Hacker News."""

    name = "hn_hiring"

    def _latest_thread_id(self) -> str | None:
        resp = http.get(
            _ALGOLIA_SEARCH,
            params={
                "tags": "story,author_whoishiring",
                "query": "Ask HN: Who is hiring?",
                "hitsPerPage": 5,
            },
        )
        for hit in resp.json().get("hits", []):
            if "who is hiring" in (hit.get("title") or "").lower():
                return str(hit.get("objectID", "")) or None
        return None

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        cfg = settings.get("aggregators", {}).get("hn_hiring", {})
        tokens = [t.lower() for t in cfg.get("locations", _DEFAULT_LOCATION_TOKENS)]

        try:
            thread_id = self._latest_thread_id()
        except Exception as exc:
            logger.warning("hn_hiring: could not locate the monthly thread: %s", exc)
            return []
        if not thread_id:
            logger.warning("hn_hiring: no 'Who is hiring?' thread found")
            return []

        raw_jobs: list[RawJob] = []
        page = 0
        while page < 3:  # up to 3000 comments — the thread never exceeds this
            try:
                resp = http.get(
                    _ALGOLIA_SEARCH,
                    params={
                        "tags": f"comment,story_{thread_id}",
                        "hitsPerPage": 1000,
                        "page": page,
                    },
                )
                data = resp.json()
            except Exception as exc:
                logger.warning("hn_hiring: comment fetch failed: %s", exc)
                break

            hits = data.get("hits", [])
            for hit in hits:
                # Only top-level comments are job posts; replies are discussion
                if str(hit.get("parent_id", "")) != str(thread_id):
                    continue
                comment_text = hit.get("comment_text") or ""
                parsed = parse_hiring_comment(comment_text)
                if not parsed:
                    continue
                haystack = f"{parsed['location']} {comment_text}".lower()
                if not any(tok in haystack for tok in tokens):
                    continue
                raw_jobs.append(
                    {
                        "title": parsed["title"],
                        "company": parsed["company"],
                        "location": parsed["location"] or "Remote",
                        "url": _ITEM_URL.format(comment_id=hit.get("objectID", "")),
                        "description": _strip_tags(comment_text),
                        "created": (hit.get("created_at") or "")[:10],
                        "source": self.name,
                    }
                )

            page += 1
            if page >= int(data.get("nbPages", 1)):
                break

        logger.info("hn_hiring: %d location-matched posts from thread %s",
                    len(raw_jobs), thread_id)
        return raw_jobs

    def normalise(self, raw: RawJob) -> JobRecord | None:
        return normalise(raw, self.name)

    def healthcheck(self) -> tuple[bool, str | None]:
        try:
            return (self._latest_thread_id() is not None, None)
        except Exception as exc:
            return False, str(exc)
