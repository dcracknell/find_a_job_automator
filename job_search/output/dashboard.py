"""Jinja2 → dashboard.html static site.

Produces a single self-contained HTML file (inline CSS, no external requests)
so it works offline on a phone synced via Dropbox/OneDrive/Drive.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import jinja2

from job_search import PROJECT_ROOT
from job_search.util.quota import today_total_gbp

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = PROJECT_ROOT / "templates"


def _load_template(name: str) -> jinja2.Template:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=jinja2.select_autoescape(["html"]),
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

    # New today
    new_today_rows = conn.execute(
        """
        SELECT title, company, location, url, fit_score, closes_on,
               closes_on <= ? AS closes_soon
        FROM jobs
        WHERE first_seen = ? AND status = 'new'
        ORDER BY fit_score DESC NULLS LAST
        LIMIT 50
        """,
        (closing_cutoff, today.isoformat()),
    ).fetchall()

    # Closing soon (any open job)
    closing_soon_rows = conn.execute(
        """
        SELECT title, company, location, url, fit_score, closes_on, status
        FROM jobs
        WHERE closes_on IS NOT NULL
          AND closes_on <= ?
          AND status NOT IN ('rejected', 'ignore', 'archive', 'closed')
        ORDER BY closes_on ASC
        LIMIT 20
        """,
        (closing_cutoff,),
    ).fetchall()

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
        "new_today": [dict(r) for r in new_today_rows],
        "closing_soon": [dict(r) for r in closing_soon_rows],
        "quota": {
            "today": f"{quota_today:.4f}",
            "month": f"{quota_month:.4f}",
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
    tmp_path.rename(output_path)
    logger.info("dashboard: written to %s", output_path)
