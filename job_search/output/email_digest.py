"""HTML email digest via smtplib.

Email structure (mobile-first, single-column, max-width 600px):
1. Closing soon (any open job with closes_on within 7 days)
2. New high-fit jobs (score >= 7, sorted by score)
3. New medium-fit jobs (score 5-6.9, top 10 only)
4. Pipeline summary (counts per status)
5. Adapter health footer
6. "View full dashboard" link
7. Quota footer: today's API spend

Heartbeat email: sent instead of regular digest when the system has been quiet
for trigger_after_n_quiet_runs consecutive runs. Suppressed for suppress_days after sending.
"""

from __future__ import annotations

import logging
import os
import smtplib
import sqlite3
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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


def _get_smtp_settings(settings: dict) -> dict:
    """Extract SMTP config from settings, resolving env-var placeholders."""
    email_cfg = settings.get("email", {})
    resolved = {}
    for key, val in email_cfg.items():
        if isinstance(val, str) and val.startswith("${") and val.endswith("}"):
            env_key = val[2:-1]
            resolved[key] = os.environ.get(env_key, "")
        else:
            resolved[key] = val
    return resolved


def _send_email(smtp: dict, subject: str, html_body: str) -> None:
    """Send an HTML email via smtplib."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp.get("smtp_from", "")
    msg["To"] = smtp.get("smtp_to", "")
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    host = smtp.get("smtp_host", "")
    port = int(smtp.get("smtp_port", 587))
    user = smtp.get("smtp_user", "")
    password = smtp.get("smtp_pass", "")
    use_tls = smtp.get("use_tls", True)

    if not host:
        logger.warning("email: no SMTP host configured — skipping send")
        return

    try:
        server = smtplib.SMTP(host, port, timeout=30)
        if use_tls:
            server.starttls()
        if user and password:
            server.login(user, password)
        server.sendmail(smtp.get("smtp_from", ""), smtp.get("smtp_to", ""), msg.as_string())
        server.quit()
        logger.info("email: digest sent to %s", smtp.get("smtp_to", ""))
    except Exception as exc:
        logger.error("email: send failed: %s", exc)
        raise


def _fetch_digest_data(conn: sqlite3.Connection, today: date) -> dict:
    """Query the DB for digest content."""
    closing_soon_cutoff = (today + timedelta(days=7)).isoformat()

    # Closing soon: open jobs with closes_on within 7 days
    closing_soon_rows = conn.execute(
        """
        SELECT title, company, location, url, fit_score, closes_on
        FROM jobs
        WHERE closes_on IS NOT NULL
          AND closes_on <= ?
          AND status NOT IN ('rejected', 'ignore', 'archive', 'closed')
        ORDER BY closes_on ASC
        LIMIT 10
        """,
        (closing_soon_cutoff,),
    ).fetchall()

    # New high-fit jobs (score >= 7), first seen today
    high_fit_rows = conn.execute(
        """
        SELECT title, company, location, url, fit_score, fit_reason, closes_on
        FROM jobs
        WHERE first_seen = ?
          AND fit_score >= 7
          AND status = 'new'
        ORDER BY fit_score DESC
        LIMIT 20
        """,
        (today.isoformat(),),
    ).fetchall()

    # New medium-fit jobs (5-6.9), first seen today
    mid_fit_rows = conn.execute(
        """
        SELECT title, company, location, url, fit_score, fit_reason, closes_on
        FROM jobs
        WHERE first_seen = ?
          AND fit_score >= 5 AND fit_score < 7
          AND status = 'new'
        ORDER BY fit_score DESC
        LIMIT 10
        """,
        (today.isoformat(),),
    ).fetchall()

    # Pipeline summary
    status_rows = conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status ORDER BY cnt DESC"
    ).fetchall()

    return {
        "closing_soon": [dict(r) for r in closing_soon_rows],
        "high_fit": [dict(r) for r in high_fit_rows],
        "mid_fit": [dict(r) for r in mid_fit_rows],
        "status_counts": {r["status"]: r["cnt"] for r in status_rows},
    }


def send_digest(conn: sqlite3.Connection, settings: dict) -> None:
    """Build and send the HTML email digest."""
    today = date.today()
    data = _fetch_digest_data(conn, today)

    jobs_new = len(data["high_fit"]) + len(data["mid_fit"])
    jobs_high_fit = len(data["high_fit"])

    subject = f"Job digest — {jobs_new} new ({jobs_high_fit} high-fit)"

    template = _load_template("email.html.j2")
    html_body = template.render(
        subject=subject,
        run_date=today.isoformat(),
        jobs_new=jobs_new,
        jobs_high_fit=jobs_high_fit,
        closing_soon=data["closing_soon"],
        high_fit=data["high_fit"],
        mid_fit=data["mid_fit"],
        status_counts=data["status_counts"],
        adapter_health=[],  # populated by caller if available
        quota_today=f"{today_total_gbp():.4f}",
        dashboard_url=settings.get("paths", {}).get("dashboard_url", ""),
    )

    smtp = _get_smtp_settings(settings)
    _send_email(smtp, subject, html_body)


def send_heartbeat(settings: dict) -> None:
    """Send a 'system alive but quiet' heartbeat email."""
    subject = "Job search: system alive but quiet"
    html_body = (
        "<html><body>"
        "<p>Your job search pipeline ran today but found no new matches.</p>"
        "<p>This heartbeat confirms the system is working normally.</p>"
        "<p>To stop these emails, set <code>mode: paused</code> in config/settings.yaml.</p>"
        "</body></html>"
    )
    smtp = _get_smtp_settings(settings)
    _send_email(smtp, subject, html_body)
