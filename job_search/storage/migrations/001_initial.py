"""Migration 001 — initial schema.

Creates: jobs, jobs_fts (FTS5), runs, api_calls tables plus FTS sync triggers.
The meta table is created by db.migrate() before running any migration.
"""

from __future__ import annotations

import sqlite3


def run(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        -- Primary job store
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,

            -- Identity & provenance
            source TEXT NOT NULL,
            matched_query TEXT,
            first_seen DATE NOT NULL,
            last_seen DATE NOT NULL,

            -- Status (user-editable, round-trips via Excel)
            status TEXT NOT NULL DEFAULT 'new',
            notes TEXT DEFAULT '',

            -- Posting
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT,
            lat REAL,
            lon REAL,
            url TEXT NOT NULL,
            description TEXT,
            posted_date DATE,
            closes_on DATE,

            -- Salary
            salary_raw TEXT,
            salary_min INTEGER,
            salary_max INTEGER,

            -- Ranking
            fit_score REAL,
            fit_confidence REAL,
            fit_reason TEXT,
            matched_keywords TEXT,      -- JSON array
            ranker_version TEXT,

            -- Hashes for sync
            jd_content_hash TEXT,

            -- Bookkeeping
            last_user_edit TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
        CREATE INDEX IF NOT EXISTS idx_jobs_fit_score ON jobs(fit_score DESC);
        CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs(last_seen);

        -- Full-text search with porter stemming
        CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
            title, company, description,
            content='jobs', content_rowid='rowid',
            tokenize='porter'
        );

        -- FTS sync triggers
        CREATE TRIGGER IF NOT EXISTS jobs_ai AFTER INSERT ON jobs BEGIN
            INSERT INTO jobs_fts(rowid, title, company, description)
            VALUES (new.rowid, new.title, new.company, new.description);
        END;

        CREATE TRIGGER IF NOT EXISTS jobs_ad AFTER DELETE ON jobs BEGIN
            INSERT INTO jobs_fts(jobs_fts, rowid, title, company, description)
            VALUES ('delete', old.rowid, old.title, old.company, old.description);
        END;

        CREATE TRIGGER IF NOT EXISTS jobs_au AFTER UPDATE ON jobs BEGIN
            INSERT INTO jobs_fts(jobs_fts, rowid, title, company, description)
            VALUES ('delete', old.rowid, old.title, old.company, old.description);
            INSERT INTO jobs_fts(rowid, title, company, description)
            VALUES (new.rowid, new.title, new.company, new.description);
        END;

        -- Run history
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TIMESTAMP NOT NULL,
            finished_at TIMESTAMP,
            duration_s REAL,
            sources_ok INTEGER DEFAULT 0,
            sources_failed INTEGER DEFAULT 0,
            jobs_scraped INTEGER DEFAULT 0,
            jobs_new INTEGER DEFAULT 0,
            jobs_closed INTEGER DEFAULT 0,
            errors TEXT DEFAULT '[]'   -- JSON array of error strings
        );

        -- API call log (also materialised in quota.jsonl)
        CREATE TABLE IF NOT EXISTS api_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP NOT NULL,
            operation TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            cached_input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            est_cost_gbp REAL NOT NULL DEFAULT 0.0
        );

        CREATE INDEX IF NOT EXISTS idx_api_calls_timestamp ON api_calls(timestamp);
        CREATE INDEX IF NOT EXISTS idx_api_calls_operation ON api_calls(operation);
        """
    )
