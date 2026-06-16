"""CLI entry point for the UK Job Search Pipeline.

All subcommands are registered here. Use `job-search --help` to see them.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
import yaml

from job_search import PROJECT_ROOT, load_settings

logger = logging.getLogger(__name__)


def _configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=getattr(logging, level.upper(), logging.INFO),
    )


# ---------------------------------------------------------------------------
# Main group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(package_name="job-search")
def main() -> None:
    """UK job search pipeline — scrape, rank, deduplicate, digest.

    Run `job-search COMMAND --help` for details on any subcommand.
    """


# ---------------------------------------------------------------------------
# parse-cv
# ---------------------------------------------------------------------------


@main.command("parse-cv")
@click.argument("cv_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--domain",
    default=None,
    help="Domain pack name (e.g. engineering, healthcare). Defaults to 'general'.",
)
def parse_cv(cv_path: Path, domain: str | None) -> None:
    """Parse a PDF CV and write config/profile.json.

    If --domain is omitted, lists available packs and defaults to 'general'.
    The prompt unconditionally includes 'use only facts present in the provided CV;
    do not invent or infer experience'.
    """
    _configure_logging()
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    from job_search.util.domain import list_packs

    if domain is None:
        packs = list_packs()
        click.echo("Available domain packs:")
        for p in packs:
            click.echo(f"  {p.name:<20} {p.display_name}")
        domain = click.prompt("Choose a domain (or press Enter for 'general')", default="general")

    from job_search.profile.parse_cv import parse_cv as _parse_cv
    click.echo(f"Parsing {cv_path} with domain '{domain}'...")
    try:
        profile = _parse_cv(cv_path, domain)
        click.echo(f"profile.json written. Name: {profile.get('name', '?')}, domain: {profile.get('domain', '?')}")
        click.echo("Review config/profile.json and adjust skills/roles as needed.")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# domains
# ---------------------------------------------------------------------------


@main.group("domains", invoke_without_command=True)
@click.pass_context
def domains(ctx: click.Context) -> None:
    """List available domain packs, or show a specific pack."""
    if ctx.invoked_subcommand is None:
        _configure_logging()
        from job_search.util.domain import list_packs
        packs = list_packs()
        if not packs:
            click.echo("No domain packs found in config/domains/.")
            return
        click.echo(f"{'Name':<20} {'Display Name'}")
        click.echo("-" * 60)
        for p in packs:
            click.echo(f"{p.name:<20} {p.display_name}")


@domains.command("show")
@click.argument("name")
def domains_show(name: str) -> None:
    """Pretty-print a domain pack's full configuration."""
    _configure_logging()
    from job_search.util.domain import load_pack
    try:
        pack = load_pack(name)
    except FileNotFoundError:
        click.echo(
            f"Domain pack '{name}' not found. "
            "Run `job-search domains` to list available packs.",
            err=True,
        )
        sys.exit(1)
    except ValueError as exc:
        click.echo(f"Domain pack '{name}' failed validation: {exc}", err=True)
        sys.exit(1)

    # Pretty-print as YAML (more readable than JSON for nested structures)
    click.echo(
        yaml.dump(
            pack.model_dump(),
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
    )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@main.command("run")
@click.option("--dry-run", is_flag=True, default=False,
              help="Skip DB writes, Excel write, and email send.")
@click.option("--source", default=None, metavar="NAME",
              help="Run only this adapter (e.g. adzuna). For debugging.")
@click.option("--rerank-stale", is_flag=True, default=False,
              help="Re-rank rows scored with an older ranker version.")
@click.option("--save-fixture", default=None, metavar="ADAPTER",
              help="Dump raw adapter response to tests/fixtures/ for use in tests.")
def run(dry_run: bool, source: str | None, rerank_stale: bool, save_fixture: str | None) -> None:
    """Run the full job search pipeline.

    Steps:
    1. Load .env secrets
    2. Open / create + migrate the SQLite DB
    3. Import any user edits from the existing Excel
    4. Generate search queries from profile.json
    5. Run enabled adapters, normalise, dedup, filter, rank
    6. Mark stale jobs as closed
    7. Regenerate Excel from DB
    8. Regenerate dashboard HTML
    9. Send email digest
    """
    _configure_logging()

    # Load secrets from .env
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    settings = load_settings()

    # Respect run mode
    mode = settings.get("mode", "active")
    if mode == "paused":
        click.echo("Run mode is 'paused' — skipping. Set mode: active in config/settings.yaml.")
        return

    from job_search.output.workbook_export import regenerate_workbook
    from job_search.output.workbook_import import import_user_edits
    from job_search.storage.db import get_connection, migrate, set_meta

    db_path = PROJECT_ROOT / settings.get("paths", {}).get("db", "data/jobs.db")
    xlsx_path = PROJECT_ROOT / settings.get("paths", {}).get("xlsx", "data/jobs.xlsx")
    backups_path = PROJECT_ROOT / settings.get("paths", {}).get("backups", "data/backups")
    dashboard_path = PROJECT_ROOT / settings.get("paths", {}).get("dashboard_html", "data/dashboard.html")

    # 1. Open + migrate DB
    conn = get_connection(db_path)
    run_meta: dict = {
        "jobs_scraped": 0, "jobs_new": 0, "jobs_closed": 0,
        "sources_ok": [], "sources_failed": [],
    }

    try:
        migrate(conn=conn)

        # 2. Import any user edits from existing Excel
        if not dry_run:
            edited = import_user_edits(conn, xlsx_path)
            if edited:
                click.echo(f"Imported {edited} user edit(s) from {xlsx_path.name}.")
        else:
            click.echo("[dry-run] Skipping Excel import.")

        # 3. Load profile + generate queries
        try:
            from job_search import load_profile
            profile = load_profile()
        except FileNotFoundError:
            click.echo(
                "config/profile.json not found. Run `job-search parse-cv <cv.pdf>` first.",
                err=True,
            )
            return

        from job_search.profile.queries import generate_queries
        queries = generate_queries(profile)
        click.echo(f"Generated {len(queries)} search queries.")

        # 4. Build adapter registry
        from job_search.util.domain import load_pack
        domain_name = profile.get("domain", "general")
        try:
            domain_pack_obj = load_pack(domain_name)
            domain_pack = domain_pack_obj.model_dump()
            domain_context = domain_pack_obj.ranker_context or ""
        except Exception:
            domain_pack = {}
            domain_context = ""

        from job_search.adapters.adzuna import AdzunaAdapter
        from job_search.adapters.reed import ReedAdapter
        from job_search.adapters.greenhouse import GreenhouseAdapter
        from job_search.adapters.lever import LeverAdapter
        from job_search.adapters.workday import WorkdayAdapter

        sources_cfg = {}
        try:
            sources_path = PROJECT_ROOT / "config" / "sources.yaml"
            with sources_path.open() as f:
                sources_cfg = yaml.safe_load(f) or {}
        except Exception as exc:
            click.echo(f"Warning: could not load sources.yaml: {exc}", err=True)

        all_settings = {**settings, **sources_cfg}

        adapter_registry = {
            "adzuna": (AdzunaAdapter(), sources_cfg.get("apis", {}).get("adzuna", {}).get("enabled", True)),
            "reed": (ReedAdapter(), sources_cfg.get("apis", {}).get("reed", {}).get("enabled", False)),
            "greenhouse": (GreenhouseAdapter(), bool(sources_cfg.get("ats", {}).get("greenhouse", {}).get("companies"))),
            "lever": (LeverAdapter(), bool(sources_cfg.get("ats", {}).get("lever", {}).get("companies"))),
            "workday": (WorkdayAdapter(), bool(sources_cfg.get("ats", {}).get("workday", {}).get("companies"))),
        }

        # 5. Run adapters
        from job_search.pipeline.dedup import sync_job, mark_closed_stale
        from job_search.pipeline.filter import apply_filters
        from job_search.pipeline.rank import rank_jobs
        from datetime import datetime

        all_records = []
        for adapter_name, (adapter, enabled) in adapter_registry.items():
            if source and adapter_name != source:
                continue
            if not enabled:
                logger.debug("adapter %s disabled, skipping", adapter_name)
                continue

            click.echo(f"Running adapter: {adapter_name}...")
            try:
                raw_jobs = adapter.fetch(queries, all_settings)

                # Save fixture if requested
                if save_fixture and save_fixture == adapter_name:
                    import json as _json
                    fixture_dir = PROJECT_ROOT / "tests" / "fixtures"
                    fixture_dir.mkdir(parents=True, exist_ok=True)
                    fixture_path = fixture_dir / f"{adapter_name}_response.json"
                    fixture_path.write_text(_json.dumps(raw_jobs[:20], indent=2, default=str))
                    click.echo(f"Fixture saved: {fixture_path}")

                records = []
                for raw in raw_jobs:
                    rec = adapter.normalise(raw)
                    if rec is not None:
                        records.append(rec)

                click.echo(f"  {adapter_name}: {len(records)} jobs normalised.")
                run_meta["jobs_scraped"] += len(records)
                run_meta["sources_ok"].append(adapter_name)
                all_records.extend(records)
            except Exception as exc:
                click.echo(f"  {adapter_name}: FAILED — {exc}", err=True)
                logger.exception("adapter %s failed", adapter_name)
                run_meta["sources_failed"].append(f"{adapter_name}: {exc}")

        # 6. Filter
        filtered = apply_filters(all_records, profile, conn)
        click.echo(f"After filtering: {len(filtered)}/{len(all_records)} jobs kept.")

        # 7. Rank
        if filtered:
            click.echo(f"Ranking {len(filtered)} jobs...")
            ranked = rank_jobs(filtered, profile, settings, domain_context)
        else:
            ranked = []

        # 8. Sync to DB
        if not dry_run:
            for rec in ranked:
                result = sync_job(conn, rec)
                if result == "inserted":
                    run_meta["jobs_new"] += 1

            # Mark stale rows as closed
            stale_days = settings.get("stale_job_days", 14)
            closed_count = mark_closed_stale(conn, stale_days)
            run_meta["jobs_closed"] = closed_count
            if closed_count:
                click.echo(f"Marked {closed_count} stale jobs as closed.")

            # Log run to runs table
            import json as _json
            conn.execute(
                """
                INSERT INTO runs (started_at, duration_s, sources_ok, sources_failed,
                                  jobs_scraped, jobs_new, jobs_closed, errors)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.utcnow().isoformat(),
                    0,
                    len(run_meta["sources_ok"]),
                    len(run_meta["sources_failed"]),
                    run_meta["jobs_scraped"],
                    run_meta["jobs_new"],
                    run_meta["jobs_closed"],
                    _json.dumps(run_meta["sources_failed"]),
                ),
            )
            conn.commit()
        else:
            click.echo(f"[dry-run] Would have synced {len(ranked)} jobs.")

        click.echo(
            f"Run complete: {run_meta['jobs_scraped']} scraped, "
            f"{run_meta['jobs_new']} new, "
            f"{run_meta['jobs_closed']} closed."
        )

        # 9. Regenerate Excel from DB
        if not dry_run:
            regenerate_workbook(conn, xlsx_path=xlsx_path, backups_dir=backups_path)
            click.echo(f"Excel regenerated: {xlsx_path}")

            # 10. Regenerate dashboard
            try:
                from job_search.output.dashboard import regenerate_dashboard
                regenerate_dashboard(conn, dashboard_path, settings)
                click.echo(f"Dashboard: {dashboard_path}")
            except Exception as exc:
                logger.warning("dashboard generation failed: %s", exc)

            # 11. Email digest (active mode only)
            if mode == "active" and ranked:
                try:
                    from job_search.output.email_digest import send_digest
                    send_digest(conn, settings)
                except Exception as exc:
                    logger.warning("email digest failed: %s", exc)
        else:
            click.echo("[dry-run] Skipping Excel/dashboard/email.")

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@main.command("export")
def export() -> None:
    """Regenerate jobs.xlsx from the current DB without running the pipeline.

    Useful for testing the import/export round-trip or recovering a corrupted xlsx.
    """
    _configure_logging()
    settings = load_settings()

    from job_search.output.workbook_export import regenerate_workbook
    from job_search.storage.db import get_connection, migrate

    db_path = PROJECT_ROOT / settings.get("paths", {}).get("db", "data/jobs.db")
    xlsx_path = PROJECT_ROOT / settings.get("paths", {}).get("xlsx", "data/jobs.xlsx")
    backups_path = PROJECT_ROOT / settings.get("paths", {}).get("backups", "data/backups")

    conn = get_connection(db_path)
    try:
        migrate(conn=conn)
        regenerate_workbook(conn, xlsx_path=xlsx_path, backups_dir=backups_path)
        click.echo(f"Excel regenerated: {xlsx_path}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------


@main.command("rank")
@click.argument("job_id")
def rank(job_id: str) -> None:
    """Re-rank a single job by job_id."""
    _configure_logging()
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    settings = load_settings()

    from job_search.storage.db import get_connection, migrate
    from job_search.pipeline.rank import rank_jobs
    from job_search.adapters.base import JobRecord
    from job_search import load_profile
    import json as _json

    db_path = PROJECT_ROOT / settings.get("paths", {}).get("db", "data/jobs.db")
    conn = get_connection(db_path)
    try:
        migrate(conn=conn)
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            click.echo(f"Job {job_id} not found in DB.", err=True)
            return

        profile = load_profile()
        rec = JobRecord(
            job_id=row["job_id"],
            source=row["source"],
            title=row["title"],
            company=row["company"],
            location=row["location"] or "",
            lat=row["lat"],
            lon=row["lon"],
            url=row["url"],
            description=row["description"] or "",
            posted_date=None,
            closes_on=None,
            salary_raw=row["salary_raw"],
            salary_min=row["salary_min"],
            salary_max=row["salary_max"],
        )
        ranked = rank_jobs([rec], profile, settings)
        r = ranked[0]
        click.echo(f"Score: {r.fit_score} (confidence: {r.fit_confidence})")
        click.echo(f"Reason: {r.fit_reason}")
        click.echo(f"Keywords: {r.matched_keywords}")

        # Update DB
        conn.execute(
            "UPDATE jobs SET fit_score=?, fit_confidence=?, fit_reason=?, matched_keywords=?, ranker_version=? WHERE job_id=?",
            (r.fit_score, r.fit_confidence, r.fit_reason, _json.dumps(r.matched_keywords), r.ranker_version, job_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@main.command("health")
def health() -> None:
    """Run a health check on every configured adapter."""
    _configure_logging()
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    from job_search.adapters.adzuna import AdzunaAdapter
    from job_search.adapters.reed import ReedAdapter
    from job_search.adapters.greenhouse import GreenhouseAdapter
    from job_search.adapters.lever import LeverAdapter

    adapters = [AdzunaAdapter(), ReedAdapter(), GreenhouseAdapter(), LeverAdapter()]
    click.echo(f"{'Adapter':<20} {'Status'}")
    click.echo("-" * 40)
    for adapter in adapters:
        try:
            ok, err = adapter.healthcheck()
            status = "OK" if ok else f"FAIL: {err}"
        except Exception as exc:
            status = f"ERROR: {exc}"
        click.echo(f"{adapter.name:<20} {status}")


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------


@main.command("migrate")
def migrate_cmd() -> None:
    """Run any pending SQLite schema migrations."""
    _configure_logging()
    settings = load_settings()
    db_path = PROJECT_ROOT / settings.get("paths", {}).get("db", "data/jobs.db")

    from job_search.storage.db import migrate
    migrate(db_path=db_path)
    click.echo("Migrations complete.")


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------


@main.command("backup")
def backup() -> None:
    """Manually back up the DB and xlsx to data/backups/."""
    _configure_logging()
    settings = load_settings()

    from datetime import date

    from job_search.output.workbook_export import backup_existing_xlsx
    from job_search.storage.db import backup_db

    db_path = PROJECT_ROOT / settings.get("paths", {}).get("db", "data/jobs.db")
    xlsx_path = PROJECT_ROOT / settings.get("paths", {}).get("xlsx", "data/jobs.xlsx")
    backups_path = PROJECT_ROOT / settings.get("paths", {}).get("backups", "data/backups")

    today = date.today().isoformat()
    dest_db = backups_path / f"jobs.{today}.db"
    backup_db(dest_db, db_path)
    backup_existing_xlsx(xlsx_path, backups_path)
    click.echo(f"Backup complete -> {backups_path}")


# ---------------------------------------------------------------------------
# recover
# ---------------------------------------------------------------------------


@main.command("recover")
def recover() -> None:
    """Rebuild the DB from cached raw adapter responses in data/cache/."""
    click.echo("[recover] DB recovery from cache - not implemented yet (Phase 5).")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@main.command("search")
@click.argument("query")
def search(query: str) -> None:
    """Full-text search over historical jobs using SQLite FTS5.

    Example: job-search search "ward sister manchester"
    """
    _configure_logging()
    settings = load_settings()
    db_path = PROJECT_ROOT / settings.get("paths", {}).get("db", "data/jobs.db")

    from job_search.storage.db import get_connection, migrate
    conn = get_connection(db_path)
    try:
        migrate(conn=conn)
        rows = conn.execute(
            """
            SELECT j.job_id, j.title, j.company, j.location, j.status,
                   j.fit_score, j.url
            FROM jobs_fts
            JOIN jobs j ON jobs_fts.rowid = j.rowid
            WHERE jobs_fts MATCH ?
            ORDER BY rank
            LIMIT 20
            """,
            (query,),
        ).fetchall()

        if not rows:
            click.echo(f"No results for: {query}")
            return

        click.echo(f"{'Title':<35} {'Company':<20} {'Location':<18} {'Status':<10} {'Score'}")
        click.echo("-" * 100)
        for row in rows:
            score = f"{row['fit_score']:.1f}" if row["fit_score"] is not None else "  -"
            click.echo(
                f"{str(row['title'])[:34]:<35} "
                f"{str(row['company'])[:19]:<20} "
                f"{str(row['location'] or '')[:17]:<18} "
                f"{row['status']:<10} {score}"
            )
    finally:
        conn.close()
