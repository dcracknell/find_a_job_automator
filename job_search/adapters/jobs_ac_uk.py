"""jobs.ac.uk adapter — UK academic and research jobs (free, keyless).

The canonical board for university posts: PhD studentships, EngD/CDT places,
research associate/fellow roles, lecturer and research-software positions.
FindAPhD sits behind a Cloudflare JS challenge, so this is the academic
channel that is actually scrapable with plain requests.

Server-rendered HTML, parsed with BeautifulSoup. robots.txt permits /search/
and /job/ (checked 2026-07-07) and the site serves the pipeline User-Agent;
all requests go through util/http (retry + 1 req/s per-host politeness).

Fetch strategy (freshness-first, same idea as Reed):
- one search request per query (cheap);
- the per-job detail page (full JD + dated fields) is fetched only for jobs
  that are new to the DB and not title-excluded, capped per run;
- jobs over the detail budget are left out entirely so a later run picks
  them up fresh, rather than inserting them with an empty description.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

from job_search.adapters.base import Adapter, JobRecord, RawJob
from job_search.pipeline.normalise import normalise
from job_search.util import http

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.jobs.ac.uk"
_SEARCH_URL = f"{_BASE_URL}/search/"

# One search request per query; details are the expensive part and have
# their own budget, so this mainly bounds wall-clock (1s politeness/request).
_MAX_QUERIES = 20

_ORDINAL_RE = re.compile(r"(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)


def _parse_full_date(text: str) -> date | None:
    """Parse detail-page dates like '22nd June 2026' to a date."""
    cleaned = _ORDINAL_RE.sub(r"\1", (text or "").strip())
    try:
        return datetime.strptime(cleaned, "%d %B %Y").date()
    except ValueError:
        return None


class JobsAcUkAdapter(Adapter):
    """Fetches academic/research jobs from jobs.ac.uk."""

    name = "jobs_ac_uk"

    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]:
        cfg = settings.get("aggregators", {}).get("jobs_ac_uk", {})
        page_size = int(cfg.get("results_per_query", 25))
        detail_budget = int(cfg.get("max_detail_fetches", 80))

        from job_search.pipeline.filter import _build_title_exclude_pattern
        profile = settings.get("_profile", {})
        title_exclude_pattern = _build_title_exclude_pattern(
            profile.get("negative_signals", {}).get("title_excludes", [])
        )
        known_urls: set[str] = settings.get("_known_job_urls") or set()

        seen_urls: set[str] = set()
        raw_jobs: list[RawJob] = []
        deferred = 0

        for query in queries[:_MAX_QUERIES]:
            try:
                resp = http.get(
                    _SEARCH_URL,
                    params={"keywords": query, "pageSize": page_size},
                )
                listings = self._parse_search_page(resp.text)
            except Exception as exc:
                logger.warning("jobs_ac_uk: search failed for %r: %s", query, exc)
                continue

            for item in listings:
                url = item["url"]
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                title = item.get("title", "")
                excluded = (
                    title_exclude_pattern is not None
                    and title_exclude_pattern.search(title)
                )
                if url in known_urls or excluded:
                    # Known: touch last_seen only (stored JD survives, see
                    # sync_job). Excluded: the filter drops it before ranking.
                    item["matched_query"] = query
                    raw_jobs.append(item)
                    continue

                if detail_budget <= 0:
                    # Out of budget — leave it for a later run so it gets a
                    # full JD then, instead of being stored with an empty one.
                    deferred += 1
                    continue

                detail_budget -= 1
                try:
                    detail_resp = http.get(url)
                    item.update(self._parse_detail_page(detail_resp.text))
                except Exception as exc:
                    logger.debug("jobs_ac_uk: detail fetch failed for %s: %s", url, exc)
                item["matched_query"] = query
                raw_jobs.append(item)

        if deferred:
            logger.info(
                "jobs_ac_uk: %d new jobs deferred to a later run (detail budget spent)",
                deferred,
            )
        logger.info(
            "jobs_ac_uk: %d jobs across %d queries",
            len(raw_jobs), min(len(queries), _MAX_QUERIES),
        )
        return raw_jobs

    @staticmethod
    def _parse_search_page(html: str) -> list[RawJob]:
        """Extract listing stubs from a search results page."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        listings: list[RawJob] = []
        for block in soup.select("div.j-search-result__result"):
            link = block.find("a", href=re.compile(r"^/job/"))
            if link is None:
                continue
            title = link.get_text(strip=True)
            url = _BASE_URL + link["href"]

            employer_el = block.select_one(".j-search-result__employer")
            employer = employer_el.get_text(strip=True) if employer_el else ""

            department_el = block.select_one(".j-search-result__department")
            department = department_el.get_text(strip=True) if department_el else ""

            location = ""
            for div in block.find_all("div"):
                text = div.get_text(" ", strip=True)
                if text.startswith("Location:"):
                    location = text.removeprefix("Location:").strip()
                    break

            salary_el = block.select_one(".j-search-result__info")
            salary_raw = None
            if salary_el:
                # Collapse internal newlines/indentation to single spaces.
                salary_text = " ".join(salary_el.get_text(" ", strip=True).split())
                salary_text = salary_text.removeprefix("Salary:").strip()
                salary_raw = salary_text or None

            listings.append(
                {
                    "title": title,
                    "url": url,
                    "company": employer,
                    "department": department,
                    "location": location,
                    "salary_raw": salary_raw,
                }
            )
        return listings

    @staticmethod
    def _parse_detail_page(html: str) -> dict:
        """Extract JD and dated fields from a /job/ detail page."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        out: dict = {}

        jd_el = soup.find(id="job-description")
        if jd_el is not None:
            # Inner HTML — clean_jd strips the tags during normalisation.
            out["description"] = jd_el.decode_contents()

        for row in soup.select("tr"):
            th, td = row.find("th"), row.find("td")
            if th is None or td is None:
                continue
            label = th.get_text(strip=True).rstrip(":").lower()
            value = td.get_text(" ", strip=True)
            if label == "location" and value:
                out["location"] = value
            elif label == "salary" and value:
                out["salary_raw"] = value
            elif label == "placed on":
                placed = _parse_full_date(value)
                if placed:
                    out["created"] = placed.isoformat()
            elif label in ("closes", "closing date"):
                closes = _parse_full_date(value)
                if closes:
                    out["closes"] = closes.isoformat()
        return out

    def normalise(self, raw: RawJob) -> JobRecord | None:
        description = raw.get("description", "")
        department = raw.get("department", "")
        if department and description:
            description = f"Department: {department}\n{description}"
        elif department:
            description = f"Department: {department}"

        mapped: RawJob = {
            "title": raw.get("title", ""),
            "company": raw.get("company", ""),
            "url": raw.get("url", ""),
            "location": raw.get("location", ""),
            "description": description,
            "salary_raw": raw.get("salary_raw"),
            "created": raw.get("created", ""),
            "matched_query": raw.get("matched_query"),
            "source": self.name,
        }
        record = normalise(mapped, self.name)
        if record is not None and record.closes_on is None and raw.get("closes"):
            try:
                record.closes_on = date.fromisoformat(raw["closes"])
            except ValueError:
                pass
        return record

    def healthcheck(self) -> tuple[bool, str | None]:
        try:
            resp = http.get(_SEARCH_URL, params={"keywords": "research", "pageSize": 1})
            resp.raise_for_status()
            return True, None
        except Exception as exc:
            return False, str(exc)
