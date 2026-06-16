"""API token + cost tracking.

Every Anthropic API call MUST go through api_call_wrapper(). This makes it
structurally impossible to hit the API without logging token spend.

Usage:
    with api_call_wrapper("rank") as rec:
        response = client.messages.create(...)
        rec["model"] = settings["models"]["rank"]["model"]
        rec["input_tokens"] = response.usage.input_tokens
        rec["cached_input_tokens"] = getattr(response.usage, "cache_read_input_tokens", 0)
        rec["output_tokens"] = response.usage.output_tokens
    # log_api_call is called automatically on context exit
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from job_search import PROJECT_ROOT

logger = logging.getLogger(__name__)

_QUOTA_JSONL: Path = PROJECT_ROOT / "data" / "quota.jsonl"
_DB_PATH: Path = PROJECT_ROOT / "data" / "jobs.db"
_SETTINGS_PATH: Path = PROJECT_ROOT / "config" / "settings.yaml"

_settings_cache: dict | None = None


def _load_settings() -> dict:
    global _settings_cache
    if _settings_cache is None:
        with _settings_path_resolve().open() as f:
            _settings_cache = yaml.safe_load(f)
    return _settings_cache


def _settings_path_resolve() -> Path:
    return _SETTINGS_PATH


def _get_rate(settings: dict, operation: str, rate_key: str) -> float:
    """Look up a per-token rate from settings.yaml. Returns 0.0 if not found."""
    models = settings.get("models", {})
    op_config = models.get(operation, {})
    return float(op_config.get(rate_key, 0.0))


def _compute_cost(
    settings: dict,
    operation: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
) -> float:
    """Compute estimated cost in GBP using per-token rates from settings.yaml."""
    input_rate = _get_rate(settings, operation, "input_gbp_per_million")
    output_rate = _get_rate(settings, operation, "output_gbp_per_million")
    cached_rate = _get_rate(settings, operation, "cached_input_gbp_per_million")

    # Uncached input = total input minus what was served from cache
    uncached_input = max(0, input_tokens - cached_input_tokens)

    return (
        uncached_input * input_rate / 1_000_000
        + cached_input_tokens * cached_rate / 1_000_000
        + output_tokens * output_rate / 1_000_000
    )


def log_api_call(
    operation: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    """Log an API call to quota.jsonl and the SQLite api_calls table.

    Returns the estimated cost in GBP.
    """
    try:
        settings = _load_settings()
    except Exception as exc:
        logger.warning("quota: could not load settings, using zero rates: %s", exc)
        settings = {}

    est_cost = _compute_cost(settings, operation, input_tokens, output_tokens, cached_input_tokens)
    ts = datetime.utcnow().isoformat() + "Z"

    record: dict[str, Any] = {
        "timestamp": ts,
        "operation": operation,
        "model": model,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "est_cost_gbp": round(est_cost, 6),
    }

    # Append to JSONL log
    try:
        _QUOTA_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with _QUOTA_JSONL.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.error("quota: could not write to %s: %s", _QUOTA_JSONL, exc)

    # Insert into SQLite (best-effort; DB may not exist yet on first run)
    _insert_api_call_row(record)

    return est_cost


def _insert_api_call_row(record: dict[str, Any]) -> None:
    """Insert an api_calls row into SQLite. Silently skips if DB is unavailable."""
    if not _DB_PATH.exists():
        return
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        try:
            conn.execute(
                """
                INSERT INTO api_calls
                    (timestamp, operation, model, input_tokens, cached_input_tokens,
                     output_tokens, est_cost_gbp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["timestamp"],
                    record["operation"],
                    record["model"],
                    record["input_tokens"],
                    record["cached_input_tokens"],
                    record["output_tokens"],
                    record["est_cost_gbp"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("quota: could not insert api_call row: %s", exc)


def today_total_gbp() -> float:
    """Sum estimated GBP cost for all API calls logged today (from quota.jsonl)."""
    if not _QUOTA_JSONL.exists():
        return 0.0

    today_str = date.today().isoformat()
    total = 0.0
    try:
        with _QUOTA_JSONL.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("timestamp", "").startswith(today_str):
                        total += float(entry.get("est_cost_gbp", 0.0))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return 0.0
    return round(total, 6)


@contextmanager
def api_call_wrapper(operation: str) -> Generator[dict[str, Any], None, None]:
    """Context manager that MUST wrap every Anthropic API call.

    Yields a mutable dict ('rec') that the caller fills in with the actual
    token counts and model name returned by the API response. On exit,
    log_api_call() is invoked automatically.

    Example:
        with api_call_wrapper("rank") as rec:
            response = client.messages.create(...)
            rec["model"] = "claude-haiku-4-5-20251001"
            rec["input_tokens"] = response.usage.input_tokens
            rec["cached_input_tokens"] = getattr(response.usage, "cache_read_input_tokens", 0)
            rec["output_tokens"] = response.usage.output_tokens
    """
    rec: dict[str, Any] = {
        "operation": operation,
        "model": "",
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
    }
    try:
        yield rec
    finally:
        log_api_call(
            operation=rec.get("operation", operation),
            model=rec.get("model", "unknown"),
            input_tokens=int(rec.get("input_tokens", 0)),
            output_tokens=int(rec.get("output_tokens", 0)),
            cached_input_tokens=int(rec.get("cached_input_tokens", 0)),
        )
