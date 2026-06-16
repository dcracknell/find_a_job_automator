# AI README

This file is written for AI coding assistants working on this repository. It explains the codebase shape, the important invariants, and the safest places to make changes.

## Project in one sentence

This is a UK job search automation pipeline: it fetches jobs from configured sources, normalises them into one shared schema, filters and ranks them against a user profile, stores them in SQLite, regenerates an Excel workbook/dashboard, and can send an email digest.

## Entry points

- `job_search/cli.py` is the main orchestration layer. The installed command is `job-search`.
- `job_search/__init__.py` defines `PROJECT_ROOT`, `load_settings()`, and `load_profile()`.
- `pyproject.toml` registers the CLI script as `job-search = "job_search.cli:main"`.

Useful local commands:

```bash
job-search --help
job-search domains
job-search run --dry-run
job-search migrate
job-search export
job-search search "query"
pytest
```

## Runtime data and secrets

Never commit runtime data or secrets.

- `.env` is intentionally ignored and holds API keys/SMTP credentials.
- `.env.example` is only a template.
- `data/` is ignored and holds `jobs.db`, `jobs.xlsx`, logs, backups, cached responses, and generated dashboard output.

## Core data model

The central object is `JobRecord` in `job_search/adapters/base.py`.

Every adapter must eventually produce a `JobRecord` with:

- stable identity: `job_id`, `source`, `url`
- posting fields: `title`, `company`, `location`, `description`, dates
- parsed salary fields: `salary_raw`, `salary_min`, `salary_max`
- ranking fields populated later: `fit_score`, `fit_reason`, `fit_confidence`, `matched_keywords`, `ranker_version`

Important invariant:

```text
job_id = sha1(company.lower() + title.lower() + canonical_url)
```

Keep this stable unless you also write a migration and a compatibility plan.

## Pipeline flow

The main `job-search run` command in `job_search/cli.py` does this:

1. Load `.env` and `config/settings.yaml`.
2. Open and migrate SQLite via `job_search/storage/db.py`.
3. Import user edits from the existing Excel workbook via `job_search/output/workbook_import.py`.
4. Load `config/profile.json`.
5. Generate search queries with `job_search/profile/queries.py`.
6. Run enabled adapters from `config/sources.yaml`.
7. Normalize raw results into `JobRecord` objects.
8. Filter records with `job_search/pipeline/filter.py`.
9. Rank records with `job_search/pipeline/rank.py`.
10. Sync records into SQLite with `job_search/pipeline/dedup.py`.
11. Mark stale jobs as closed.
12. Regenerate Excel with `job_search/output/workbook_export.py`.
13. Regenerate dashboard HTML with `job_search/output/dashboard.py`.
14. Send email digest with `job_search/output/email_digest.py` when active mode allows it.

## Adapters

Adapters live in `job_search/adapters/` and inherit from `Adapter` in `base.py`.

Each adapter implements:

- `fetch(queries, settings) -> list[RawJob]`
- `normalise(raw) -> JobRecord | None`

Current implemented adapters:

- `adzuna.py`: uses Adzuna API credentials from `.env`.
- `reed.py`: uses Reed API key from `.env`.
- `greenhouse.py`: public Greenhouse board API, company slugs from `sources.yaml`.
- `lever.py`: public Lever API, company slugs from `sources.yaml`.
- `workday.py`: derives Workday API URLs from configured careers URLs.

Partially implemented or placeholder adapters:

- `jobspy_adapter.py`: intentionally raises `NotImplementedError`.
- `adapters/domain/*.py`: domain-specific placeholder adapters.

When adding a new adapter, prefer mapping its raw response into the generic shape expected by `job_search/pipeline/normalise.py`, then call `normalise(mapped, self.name)`.

## Normalisation and cleaning

`job_search/pipeline/normalise.py` centralises common cleanup:

- strips non-essential URL query parameters
- builds `job_id`
- geocodes locations with cache support
- parses salary text and numeric salary fields
- cleans job descriptions
- extracts closing dates
- parses posted dates

`job_search/pipeline/jd_clean.py` strips HTML, removes boilerplate, normalises whitespace, truncates long descriptions, and returns a content hash. Ranking code assumes descriptions have already gone through this cleanup.

## Filtering and ranking

`job_search/pipeline/filter.py` removes jobs before LLM ranking to reduce cost:

- salary below the configured floor
- stale postings
- excluded companies
- companies recently rejected by the user
- too far away when remote work is not acceptable

`job_search/pipeline/rank.py` uses a two-pass ranking system:

1. Free keyword pre-score based on `core_skills`, `adjacent_skills`, and negative signals.
2. Anthropic LLM ranking for jobs above `pre_score_threshold`.

Important invariant: all LLM calls should go through `job_search/util/quota.py:api_call_wrapper()` so token usage and estimated cost are logged.

## Persistence

SQLite is the source of truth.

- Connection and migration helpers are in `job_search/storage/db.py`.
- Migration files live in `job_search/storage/migrations/`.
- The initial schema is `001_initial.py`.
- `jobs_fts` is an SQLite FTS5 virtual table kept in sync by triggers.
- `runs` stores pipeline history.
- `api_calls` stores model usage and cost estimates.

Deduplication and DB sync are in `job_search/pipeline/dedup.py`.

Critical invariant: `sync_job()` must not overwrite user-owned `status` or `notes` for existing rows. Excel round-tripping depends on this.

## Excel, dashboard, and email

- `job_search/output/workbook_export.py` regenerates the Excel workbook from SQLite.
- `job_search/output/workbook_import.py` imports user edits from Excel back into SQLite.
- `job_search/output/dashboard.py` renders `templates/dashboard.html.j2`.
- `job_search/output/email_digest.py` renders `templates/email.html.j2` and sends SMTP mail.

Excel is a user-editable view, not the primary database.

## Configuration

Config files live in `config/`:

- `settings.yaml`: paths, email, run mode, model choices, cost rates.
- `sources.yaml`: enabled APIs and ATS/company sources.
- `profile.json`: user profile, skills, filters, target roles.
- `ranker.yaml`: ranking prompts, scoring rubric, thresholds.
- `domains/*.yaml`: domain packs loaded and validated by `job_search/util/domain.py`.

Do not hard-code settings that already belong in YAML unless there is a strong reason.

## Tests

Existing tests cover salary parsing, Adzuna fixture normalisation, and dedup behavior.

```bash
pytest
```

When changing a parser, adapter, filter, or DB sync behavior, add or update focused tests in `tests/`.

## Safe change checklist for AI agents

Before editing:

- Check `git status -sb`.
- Read the nearby module before changing it.
- Preserve user-owned files and uncommitted changes.

When editing:

- Keep `JobRecord` compatibility in mind.
- Do not commit `.env` or anything in `data/`.
- Do not bypass `api_call_wrapper()` for model calls.
- Do not let adapter refreshes overwrite existing job `status` or `notes`.
- Prefer config changes in `config/*.yaml` over hard-coded values.

Before finishing:

- Run the smallest relevant tests, usually `pytest`.
- Check `git status -sb`.
- If asked to publish, commit and push to `origin/main`.

## Known incomplete areas

- `job_search/adapters/jobspy_adapter.py` is a placeholder.
- `job-search recover` is currently a placeholder command.
- Some domain-specific adapter modules are placeholders.

Treat these as planned extension points rather than accidental bugs unless the user asks to implement them.
