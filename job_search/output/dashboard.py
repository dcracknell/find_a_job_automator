"""Jinja2 → dashboard.html static site.

Produces a single self-contained HTML file (inline CSS, no external requests)
so it works offline on a phone synced via Dropbox/OneDrive/Drive.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import jinja2

from job_search import PROJECT_ROOT
from job_search.util.quota import today_total_gbp

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = PROJECT_ROOT / "templates"

_REMOTE_TOKENS = ("remote", "work from home", "wfh", "home based", "anywhere")

# Rough bounding box for the UK and Ireland — used only for the dashboard's
# "UK or remote" filter, so coarse is fine.
_UK_LAT = (49.8, 61.0)
_UK_LON = (-8.7, 2.0)


def _looks_remote(location: str) -> bool:
    loc = location.lower()
    return any(tok in loc for tok in _REMOTE_TOKENS)


def _in_uk(lat: float | None, lon: float | None) -> int | None:
    if lat is None or lon is None:
        return None
    inside = _UK_LAT[0] <= lat <= _UK_LAT[1] and _UK_LON[0] <= lon <= _UK_LON[1]
    return 1 if inside else 0


def _load_template(name: str) -> jinja2.Template:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
        # "j2" must be listed: our templates end in .html.j2, and
        # select_autoescape matches the FINAL extension only. Without it,
        # scraped job titles would be injected into the page unescaped.
        autoescape=jinja2.select_autoescape(["html", "htm", "xml", "j2"]),
    )
    return env.get_template(name)


def _fetch_dashboard_data(conn: sqlite3.Connection, settings: dict) -> dict:
    """Query the DB for all dashboard sections."""
    today = date.today()
    closing_cutoff = (today + timedelta(days=7)).isoformat()

    # Last run info
    last_run = conn.execute(
        "SELECT * FROM runs ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    last_run_dict = dict(last_run) if last_run else {}

    # Every open job — shipped to the page as embedded JSON so search,
    # filters, and sorting operate on the FULL dataset, not a top-N slice.
    open_rows = conn.execute(
        """
        SELECT title, company, location, lat, lon, url, fit_score,
               fit_confidence, fit_reason, matched_keywords, salary_raw,
               salary_min, salary_max, source, status, first_seen, closes_on,
               (closes_on IS NOT NULL AND closes_on <= ?) AS closes_soon,
               (first_seen = ?) AS is_new
        FROM jobs
        WHERE status NOT IN ('rejected', 'ignore', 'archive', 'closed')
        ORDER BY fit_score DESC NULLS LAST, first_seen DESC
        LIMIT 20000
        """,
        (closing_cutoff, today.isoformat()),
    ).fetchall()

    open_jobs: list[dict] = []
    sources: set[str] = set()
    statuses: set[str] = set()
    for row in open_rows:
        try:
            keywords = json.loads(row["matched_keywords"] or "[]")
        except (TypeError, ValueError):
            keywords = []
        salary = row["salary_raw"]
        if not salary and (row["salary_min"] or row["salary_max"]):
            parts = [f"£{int(v):,}" for v in (row["salary_min"], row["salary_max"]) if v]
            salary = " - ".join(parts)
        job = {
            "title": row["title"],
            "company": row["company"],
            "location": row["location"] or "",
            "url": row["url"],
            "score": round(row["fit_score"], 1) if row["fit_score"] is not None else None,
            "conf": round(row["fit_confidence"], 2) if row["fit_confidence"] is not None else None,
            "reason": (row["fit_reason"] or "")[:300],
            "kw": ", ".join(str(k) for k in keywords[:5]),
            "salary": salary or "",
            "source": row["source"],
            "status": row["status"],
            "seen": row["first_seen"] or "",
            "closes": row["closes_on"] or "",
            "new": 1 if row["is_new"] else 0,
            "soon": 1 if row["closes_soon"] else 0,
            "remote": 1 if _looks_remote(row["location"] or "") else 0,
            # 1/0 from geocoded coordinates; null when the location never geocoded
            "uk": _in_uk(row["lat"], row["lon"]),
        }
        sources.add(job["source"] or "")
        statuses.add(job["status"] or "")
        open_jobs.append(job)

    # Stat tiles
    tile_row = conn.execute(
        """
        SELECT
            COUNT(*) AS open_total,
            COALESCE(SUM(fit_score >= 7), 0) AS strong,
            COALESCE(SUM(first_seen = ?), 0) AS new_today,
            COALESCE(SUM(closes_on IS NOT NULL AND closes_on <= ?), 0) AS closing_soon
        FROM jobs
        WHERE status NOT IN ('rejected', 'ignore', 'archive', 'closed')
        """,
        (today.isoformat(), closing_cutoff),
    ).fetchone()

    # Quota stats
    quota_today = today_total_gbp()

    # Month total from api_calls table
    month_start = today.replace(day=1).isoformat()
    month_row = conn.execute(
        "SELECT COALESCE(SUM(est_cost_gbp), 0) AS total FROM api_calls WHERE date(timestamp) >= ?",
        (month_start,),
    ).fetchone()
    quota_month = float(month_row["total"]) if month_row else 0.0

    days_elapsed = today.day
    days_in_month = 30
    projected = (quota_month / days_elapsed * days_in_month) if days_elapsed > 0 else 0.0

    # Cache hit rate from api_calls
    cache_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(cached_input_tokens), 0) AS cached,
            COALESCE(SUM(input_tokens), 0) AS total
        FROM api_calls WHERE date(timestamp) = ?
        """,
        (today.isoformat(),),
    ).fetchone()
    if cache_row and cache_row["total"] > 0:
        cache_hit_rate = f"{cache_row['cached'] / cache_row['total'] * 100:.0f}%"
    else:
        cache_hit_rate = "—"

    return {
        "last_run": last_run_dict,
        "mode": settings.get("mode", "active"),
        "open_jobs": open_jobs,
        "sources": sorted(s for s in sources if s),
        "statuses": sorted(s for s in statuses if s),
        "tiles": {
            "open_total": tile_row["open_total"] if tile_row else 0,
            "strong": tile_row["strong"] if tile_row else 0,
            "new_today": tile_row["new_today"] if tile_row else 0,
            "closing_soon": tile_row["closing_soon"] if tile_row else 0,
        },
        "quota": {
            "today": f"{quota_today:.2f}",
            "month": f"{quota_month:.2f}",
            "projected": f"{projected:.2f}",
            "cache_hit_rate": cache_hit_rate,
        },
        "generated_at": today.isoformat(),
    }


def regenerate_dashboard(
    conn: sqlite3.Connection,
    output_path: Path,
    settings: dict,
) -> None:
    """Render dashboard.html.j2 against current DB state and write to output_path."""
    data = _fetch_dashboard_data(conn, settings)

    template = _load_template("dashboard.html.j2")
    html = template.render(**data)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp.html")
    tmp_path.write_text(html, encoding="utf-8")
    # replace(), not rename(): rename() raises FileExistsError on Windows
    # when the dashboard already exists; replace() overwrites atomically.
    tmp_path.replace(output_path)
    logger.info("dashboard: written to %s", output_path)
