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
job-search run --rerank-stale     # re-score rows ranked with an older prompt version
job-search discover "Company"     # probe a company's careers site for an ATS feed
job-search ui                     # local editor for profile.json + settings.yaml
job-search migrate
job-search export
job-search search "query"
pytest && ruff check job_search tests
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
4. Load `config/profile.json` and the merged domain pack (`get_active_domain`).
5. Generate search queries with `job_search/profile/queries.py` — stateful: query
   history from the `query_stats` table rotates out recently-tried searches and
   asks Claude for novel phrasings while keeping proven producers.
6. Run enabled adapters from `config/sources.yaml`, merged with
   `data/discovered_sources.yaml` (auto-discovered career sites).
7. Normalize raw results into `JobRecord` objects.
8. Filter records with `job_search/pipeline/filter.py`.
9. Rank ONLY records that need it (new / changed `jd_content_hash` / stale
   `ranker_version`) with `job_search/pipeline/rank.py`. Already-scored,
   unchanged jobs never go back to the API.
10. Sync records into SQLite with `job_search/pipeline/dedup.py`; scores are
    persisted for existing rows only when `record.freshly_ranked` is set.
11. Record query usage/yield (`job_search/pipeline/query_stats.py`) and run
    career-site discovery (`job_search/discovery.py`): companies whose
    board-sourced jobs scored well get probed once for a public ATS feed and,
    on a hit, become permanent direct sources.
12. Mark stale jobs as closed; prune old backups.
13. Regenerate Excel with `job_search/output/workbook_export.py`.
14. Regenerate dashboard HTML with `job_search/output/dashboard.py`.
15. Send email digest with `job_search/output/email_digest.py` when active mode allows it.

## Adapters

Adapters live in `job_search/adapters/` and inherit from `Adapter` in `base.py`.

Each adapter implements:

- `fetch(queries, settings) -> list[RawJob]`
- `normalise(raw) -> JobRecord | None`

Current implemented adapters:

- `adzuna.py`: Adzuna API (credentials from `.env`); fetches date-sorted, recent-only.
- `reed.py`: Reed API; skips detail fetches for excluded titles and already-known URLs.
- `jobspy_adapter.py`: python-jobspy wrapper (Indeed, Google Jobs by default).
- `greenhouse.py`: public Greenhouse board API (HTML entities unescaped).
- `lever.py`: public Lever API.
- `workday.py`: derives Workday CXS API URLs from configured careers URLs
  (locale segments like `/en-US/` are stripped — required, or every request 404s).
- `workable_adapter.py`, `ashby.py`, `recruitee.py`, `smartrecruiters.py`:
  public keyless ATS APIs; companies come from `sources.yaml` and from
  auto-discovery (`data/discovered_sources.yaml`).
- `careers_page.py`: generic schema.org JobPosting JSON-LD reader for pages
  listed under `custom_pages:` in `sources.yaml`.
- `hn_hiring.py`: monthly "Ask HN: Who is hiring?" thread via the Algolia API.
- `remotive.py`, `arbeitnow.py`: keyless aggregator APIs (location-filtered).

Placeholder adapters (raise `NotImplementedError`; not registered in the CLI):

- `gov_uk.py` and `adapters/domain/*.py` (nhs_jobs, civil_service, findaphd,
  charityjob, caterer, mandy, otta, tes).

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
2. Anthropic LLM ranking for jobs above `pre_score_threshold` (config default 0.0
   — every filtered job is LLM-ranked when a key is configured). Batches echo a
   per-job index `"i"` and scores are matched by it, not by position. Keyword
   matching is word-boundary based (skill "C" must not match every word with a c).

Important invariants:

- All LLM calls go through `job_search/util/quota.py:api_call_wrapper()` — it logs
  token usage/cost AND enforces `quota_soft_cap_gbp` (warning at the cap,
  `QuotaExceededError` hard stop at 2x).
- Anthropic clients are constructed with `max_retries=4`.
- A job whose stored `jd_content_hash` and `ranker_version` match the active run
  is never re-sent to the API (see cli.run step 9).

## Persistence

SQLite is the source of truth.

- Connection and migration helpers are in `job_search/storage/db.py`.
- Migration files live in `job_search/storage/migrations/`.
- `001_initial.py`: jobs, jobs_fts (FTS5 + sync triggers), runs, api_calls.
- `002_query_stats.py`: per-query usage/yield for search rotation.
- `003_company_probes.py`: one-probe-per-company record for discovery.

Deduplication and DB sync are in `job_search/pipeline/dedup.py`.

Critical invariants:

- `sync_job()` must not overwrite user-owned `status` or `notes` for existing rows.
  Excel round-tripping depends on this.
- Ranking fields on existing rows are only updated when `record.freshly_ranked`
  is True (set by `rank.py` when a score was actually produced this run).
- An empty `description` never overwrites a stored one (adapters legitimately
  skip detail fetches for known jobs).

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

Tests cover filtering (title/word-boundary, remote/distance, location excludes),
salary parsing, dedup/sync score persistence, ranking pre-score and index
matching, query rotation and stats, quota enforcement, adapter normalisation
(Adzuna/Workday/Greenhouse/Ashby/Recruitee/SmartRecruiters/Remotive/Arbeitnow),
discovery probing, JSON-LD extraction, the settings-editor helper, and the
GitHub workflow files. CI (`.github/workflows/test.yml`) runs `ruff` + `pytest`
on every push/PR.

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

## Other notable modules

- `job_search/discovery.py`: career-site auto-discovery (slug probing across six
  ATS providers, `data/discovered_sources.yaml` persistence, DB-driven candidate
  selection). Probes use `http.get_once` (no retries — a miss is the common case).
- `job_search/pipeline/query_stats.py`: search-query usage/yield persistence.
- `job_search/ui.py`: `job-search ui` local editor server; `update_settings_text`
  does comment-preserving line edits of settings.yaml (never a YAML re-dump).
  The same HTML (templates/preferences.html) is published statically at
  docs/preferences.html.

## Known incomplete areas

- `job-search recover` is currently a placeholder command.
- `gov_uk.py` and the domain-specific adapter modules are placeholders and are
  intentionally not registered in the CLI.

Treat these as planned extension points rather than accidental bugs unless the user asks to implement them.
