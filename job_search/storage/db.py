"""SQLite primary store — schema creation, migration, connection helper, backup."""

from __future__ import annotations

import importlib
import logging
import re
import sqlite3
from pathlib import Path

from job_search import PROJECT_ROOT

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH: Path = PROJECT_ROOT / "data" / "jobs.db"
_MIGRATIONS_PKG = "job_search.storage.migrations"
_MIGRATIONS_DIR: Path = Path(__file__).parent / "migrations"


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open the SQLite database with WAL mode and return the connection.

    Creates the data/ directory if it doesn't exist.
    Caller is responsible for closing the connection.
    """
    path = db_path or _DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


def _ensure_meta_table(conn: sqlite3.Connection) -> None:
    """Bootstrap the meta table if it doesn't exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Read schema_version from the meta table. Returns 0 if not set."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row["value"]) if row else 0


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(version),),
    )
    conn.commit()


def _discover_migrations() -> list[tuple[int, str]]:
    """Return sorted list of (version_number, module_name) for all migration files."""
    pattern = re.compile(r"^(\d{3})_.+\.py$")
    results: list[tuple[int, str]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        match = pattern.match(path.name)
        if match:
            version = int(match.group(1))
            module_name = f"{_MIGRATIONS_PKG}.{path.stem}"
            results.append((version, module_name))
    return results


def migrate(conn: sqlite3.Connection | None = None, db_path: Path | None = None) -> None:
    """Ensure the database schema is up to date.

    If conn is None, opens a connection to db_path (or the default path).
    Runs any migration files whose version number exceeds the current schema_version.
    Each migration is a Python module with a run(conn) function.
    """
    _own_conn = conn is None
    if _own_conn:
        conn = get_connection(db_path)

    try:
        _ensure_meta_table(conn)
        current_version = _get_schema_version(conn)
        logger.debug("Current schema_version: %d", current_version)

        for version, module_name in _discover_migrations():
            if version <= current_version:
                continue
            logger.info("Running migration %03d (%s)", version, module_name)
            mod = importlib.import_module(module_name)
            mod.run(conn)
            _set_schema_version(conn, version)
            logger.info("Migration %03d complete", version)

    finally:
        if _own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def _dedupe_path(path: Path) -> Path:
    """Return path, or path with .1/.2/... inserted before the suffix if it exists."""
    if not path.exists():
        return path

    for i in range(1, 1000):
        candidate = path.with_name(f"{path.stem}.{i}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not find available backup path for {path}")


def backup_db(dest_path: Path, db_path: Path | None = None) -> None:
    """Copy the database to dest_path using SQLite VACUUM INTO (fast, consistent).

    VACUUM INTO works without locking the source database for writes,
    making it safe to run mid-pipeline.
    """
    source = db_path or _DEFAULT_DB_PATH
    if not source.exists():
        logger.warning("backup_db: source DB does not exist: %s", source)
        return

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path = _dedupe_path(dest_path)
    conn = get_connection(source)
    try:
        conn.execute("VACUUM INTO ?", (str(dest_path),))
        logger.info("DB backed up to %s", dest_path)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Meta helpers
# ---------------------------------------------------------------------------


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Read a value from the meta table."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Write a value to the meta table."""
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
    )
    conn.commit()
