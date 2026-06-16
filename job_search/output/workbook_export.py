"""Regenerate data/jobs.xlsx from SQLite.

Writes are always atomic (build as .tmp, rename on success) and always back up
any existing file first. The atomic_xlsx_write helper is reusable by other modules.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from job_search import PROJECT_ROOT

logger = logging.getLogger(__name__)

_XLSX_PATH: Path = PROJECT_ROOT / "data" / "jobs.xlsx"
_BACKUPS_DIR: Path = PROJECT_ROOT / "data" / "backups"

# Column definitions for the 'jobs' sheet — (header, db_column)
_JOBS_COLUMNS: list[tuple[str, str]] = [
    ("Job ID", "job_id"),
    ("Status", "status"),
    ("Score", "fit_score"),
    ("Confidence", "fit_confidence"),
    ("Title", "title"),
    ("Company", "company"),
    ("Location", "location"),
    ("Salary (raw)", "salary_raw"),
    ("Salary Min", "salary_min"),
    ("Salary Max", "salary_max"),
    ("Posted", "posted_date"),
    ("Closes", "closes_on"),
    ("Source", "source"),
    ("URL", "url"),
    ("Reason", "fit_reason"),
    ("Keywords", "matched_keywords"),
    ("First Seen", "first_seen"),
    ("Last Seen", "last_seen"),
    ("Query", "matched_query"),
    ("Notes", "notes"),
    ("Ranker Ver.", "ranker_version"),
]

# Status values and their row fill colours
_STATUS_FILLS: dict[str, str] = {
    "archive": "D3D3D3",  # light grey
    "closed": "BEBEBE",   # grey
    "ignore": "F0E68C",   # khaki
}

_SCORE_GREEN = "00B050"   # fit_score ≥ 8
_SCORE_AMBER = "FFC000"   # fit_score 5–7.9
_SCORE_RED = "FF0000"     # fit_score < 5
_CLOSES_SOON = "FF0000"   # closes within 7 days


def atomic_xlsx_write(workbook: Workbook, dest_path: Path) -> None:
    """Save a workbook atomically: write to .tmp, then rename to dest.

    This is the ONLY way workbooks should be saved anywhere in the codebase.
    Never call workbook.save(final_path) directly.
    """
    tmp_path = dest_path.with_suffix(".xlsx.tmp")
    try:
        workbook.save(str(tmp_path))
        # On Windows, os.replace is atomic within the same volume
        os.replace(str(tmp_path), str(dest_path))
        logger.debug("Atomically wrote %s", dest_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def backup_existing_xlsx(xlsx_path: Path, backups_dir: Path) -> None:
    """Copy xlsx_path to backups_dir/jobs.YYYY-MM-DD.xlsx (no-op if file missing)."""
    if not xlsx_path.exists():
        return
    backups_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    dest = backups_dir / f"jobs.{today}.xlsx"
    for i in range(1, 1000):
        if not dest.exists():
            break
        dest = backups_dir / f"jobs.{today}.{i}.xlsx"
    else:
        raise RuntimeError(f"Could not find available backup path in {backups_dir}")

    shutil.copy2(str(xlsx_path), str(dest))
    logger.info("Backed up existing workbook to %s", dest)


def _build_jobs_sheet(ws, conn: sqlite3.Connection) -> None:
    """Populate the 'jobs' worksheet from SQLite."""
    headers = [h for h, _ in _JOBS_COLUMNS]
    db_cols = [c for _, c in _JOBS_COLUMNS]

    # Header row
    ws.append(headers)
    header_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    # Column widths (sensible defaults)
    col_widths = {
        "Job ID": 10, "Status": 10, "Score": 7, "Confidence": 10,
        "Title": 35, "Company": 22, "Location": 20,
        "Salary (raw)": 18, "Salary Min": 11, "Salary Max": 11,
        "Posted": 12, "Closes": 12, "Source": 14, "URL": 40,
        "Reason": 40, "Keywords": 25, "First Seen": 12, "Last Seen": 12,
        "Query": 25, "Notes": 35, "Ranker Ver.": 12,
    }
    for i, header in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = col_widths.get(header, 15)

    # Data rows
    rows = conn.execute(
        f"SELECT {', '.join(db_cols)} FROM jobs ORDER BY fit_score DESC NULLS LAST, first_seen DESC"
    ).fetchall()

    for row in rows:
        ws.append(list(row))

    # Freeze the header row
    ws.freeze_panes = "A2"

    # Add auto-filter on the header row
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions

    # Conditional formatting — Score column (column index 3 = column C)
    score_col = get_column_letter(headers.index("Score") + 1)
    last_row = ws.max_row
    if last_row > 1:
        score_range = f"{score_col}2:{score_col}{last_row}"

        # Green fill for score >= 8
        green_fill = PatternFill(
            start_color=_SCORE_GREEN,
            end_color=_SCORE_GREEN,
            fill_type="solid",
        )
        ws.conditional_formatting.add(
            score_range,
            FormulaRule(formula=[f'{score_col}2>=8'], fill=green_fill),
        )
        # Amber fill for 5 <= score < 8
        amber_fill = PatternFill(
            start_color=_SCORE_AMBER,
            end_color=_SCORE_AMBER,
            fill_type="solid",
        )
        ws.conditional_formatting.add(
            score_range,
            FormulaRule(formula=[f'AND({score_col}2>=5,{score_col}2<8)'], fill=amber_fill),
        )
        # Red fill for score < 5
        red_fill = PatternFill(start_color=_SCORE_RED, end_color=_SCORE_RED, fill_type="solid")
        ws.conditional_formatting.add(
            score_range,
            FormulaRule(formula=[f'AND({score_col}2<>"",{score_col}2<5)'], fill=red_fill),
        )

        # Closing soon: bold + red font on Closes column where date is within 7 days
        closes_col = get_column_letter(headers.index("Closes") + 1)
        closes_range = f"{closes_col}2:{closes_col}{last_row}"
        today_serial = (date.today() - date(1899, 12, 30)).days
        soon_font = Font(bold=True, color=_SCORE_RED)
        ws.conditional_formatting.add(
            closes_range,
            FormulaRule(
                formula=[f'AND({closes_col}2<>"",{closes_col}2<={today_serial + 7})'],
                font=soon_font,
            ),
        )


def _build_runs_sheet(ws, conn: sqlite3.Connection) -> None:
    """Populate the 'runs' worksheet with the last 30 runs."""
    headers = ["ID", "Started", "Finished", "Duration (s)",
               "Sources OK", "Sources Failed", "Jobs Scraped",
               "Jobs New", "Jobs Closed", "Errors"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    rows = conn.execute(
        "SELECT id, started_at, finished_at, duration_s, sources_ok, sources_failed, "
        "jobs_scraped, jobs_new, jobs_closed, errors FROM runs ORDER BY id DESC LIMIT 30"
    ).fetchall()
    for row in rows:
        ws.append(list(row))

    ws.freeze_panes = "A2"


def _build_profile_sheet(ws) -> None:
    """Populate the 'profile' worksheet as a read-only mirror of profile.json."""
    import json
    profile_path = PROJECT_ROOT / "config" / "profile.json"
    ws.append(["Key", "Value"])
    for cell in ws[1]:
        cell.font = Font(bold=True)

    if not profile_path.exists():
        ws.append(["(profile.json not found)", ""])
        return

    try:
        with profile_path.open() as f:
            profile = json.load(f)
    except Exception as exc:
        ws.append(["(error reading profile.json)", str(exc)])
        return

    def _flatten(d: dict, prefix: str = "") -> list[tuple[str, str]]:
        items = []
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                items.extend(_flatten(v, key))
            else:
                items.append((key, json.dumps(v) if not isinstance(v, str) else v))
        return items

    for k, v in _flatten(profile):
        ws.append([k, v])

    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 60


def _build_quota_sheet(ws, conn: sqlite3.Connection) -> None:
    """Populate the 'quota' worksheet with last 30 days of API spend."""
    headers = ["Date", "Operation", "Model", "Calls",
               "Input Tokens", "Cached Tokens", "Output Tokens", "Est. Cost (GBP)"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    rows = conn.execute(
        """
        SELECT
            DATE(timestamp) as day,
            operation,
            model,
            COUNT(*) as calls,
            SUM(input_tokens) as input_tokens,
            SUM(cached_input_tokens) as cached_tokens,
            SUM(output_tokens) as output_tokens,
            ROUND(SUM(est_cost_gbp), 4) as est_cost_gbp
        FROM api_calls
        WHERE DATE(timestamp) >= DATE('now', '-30 days')
        GROUP BY day, operation, model
        ORDER BY day DESC, est_cost_gbp DESC
        """
    ).fetchall()
    for row in rows:
        ws.append(list(row))

    ws.freeze_panes = "A2"


def regenerate_workbook(
    conn: sqlite3.Connection,
    xlsx_path: Path | None = None,
    backups_dir: Path | None = None,
) -> None:
    """Regenerate the Excel workbook from the current SQLite state.

    Steps:
    1. Back up any existing xlsx to data/backups/jobs.YYYY-MM-DD.xlsx
    2. Build a new Workbook with four sheets (jobs, runs, profile, quota)
    3. Write atomically via atomic_xlsx_write (tmp → rename)
    """
    xlsx_path = xlsx_path or _XLSX_PATH
    backups_dir = backups_dir or _BACKUPS_DIR

    backup_existing_xlsx(xlsx_path, backups_dir)

    wb = Workbook()

    # jobs sheet (default active sheet)
    ws_jobs = wb.active
    ws_jobs.title = "jobs"
    _build_jobs_sheet(ws_jobs, conn)

    # runs sheet
    ws_runs = wb.create_sheet("runs")
    _build_runs_sheet(ws_runs, conn)

    # profile sheet
    ws_profile = wb.create_sheet("profile")
    _build_profile_sheet(ws_profile)

    # quota sheet
    ws_quota = wb.create_sheet("quota")
    _build_quota_sheet(ws_quota, conn)

    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_xlsx_write(wb, xlsx_path)
    logger.info("Workbook regenerated: %s", xlsx_path)
