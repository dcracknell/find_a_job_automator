# UK Job Search Pipeline

A daily pipeline that scrapes UK job boards and company career pages, ranks matches against your CV with an LLM, deduplicates against a persistent Excel workbook, and emails a digest of new high-fit jobs.

For AI assistant orientation, see [AI_README.md](AI_README.md).

## Current status

This repository currently implements the Phase 1 scaffold: installation, CLI wiring,
domain-pack loading, SQLite migrations, empty Excel export, search, and backups.
The actual scraping, CV parsing, salary parsing, dedup sync, LLM ranking, dashboard,
and email digest are still Phase 2+ placeholders.

---

## Quick start

```bash
# 1. Clone
git clone <repo-url> job-search
cd job-search

# 2. Create and activate a virtual environment
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

# 3. Install
pip install -e .

# 4. Configure secrets
cp .env.example .env
# Edit .env and fill in ANTHROPIC_API_KEY, ADZUNA_APP_ID/KEY, REED_API_KEY, SMTP_*

# 5. Parse your CV (choose a domain, or omit for 'general')
job-search parse-cv path/to/your-cv.pdf --domain engineering

# 6. Review / edit config/profile.json, config/sources.yaml

# 7. Run the pipeline
job-search run
```

Output files land in `data/` (gitignored):
- `data/jobs.db` — SQLite primary store
- `data/jobs.xlsx` — regenerated Excel view (edit `status` / `notes` here)
- `data/runs.log` — per-run log
- `data/quota.jsonl` — API token + cost log

---

## Available commands

```
job-search parse-cv <cv.pdf> [--domain <name>]  # regenerate profile.json from CV
job-search domains                               # list available domain packs
job-search domains show <name>                   # print a domain pack's details
job-search run [--dry-run] [--source <name>] [--rerank-stale] [--save-fixture <src>]
job-search rank <job_id>                         # re-rank a single job
job-search health                                # adapter health check
job-search migrate                               # run pending DB schema migrations
job-search backup                                # manual DB + Excel backup
job-search recover                               # rebuild DB from cached raw responses
job-search export                                # regenerate Excel from DB (no pipeline)
job-search search "<query>"                      # FTS5 full-text search over historical jobs
```

---

## Running on GitHub Actions

The repository includes `.github/workflows/daily_run.yml`, so the pipeline can run
entirely on GitHub's hosted runners without a local computer.

1. Push the repository to GitHub. Keep it private if job data, notes, your CV, or your profile are sensitive.
2. In GitHub, open **Settings > Secrets and variables > Actions** and add any secrets you use:
   `ANTHROPIC_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `REED_API_KEY`,
   `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`, and `SMTP_TO`.
   Adzuna and Reed searches auto-enable when their API secrets are present.
3. Open **Actions > Publish Setup UI > Run workflow** once to publish the setup
   page to GitHub Pages. After it deploys, use that page to paste your CV text,
   fill in roles, skills, location, salary, and exclusions, then click
   **Open GitHub issue**. You can also use **Issues > New issue > Job search
   profile setup** directly.
4. Submitting the generated issue runs **Configure Job Search Profile**, which
   commits the generated `config/profile.json` and starts **Daily Job Search**.
5. You can also open **Actions > Daily Job Search > Run workflow** to start a
   manual run at any time.
6. Download `job-search-output-<run_number>` from the completed workflow run to get
   `jobs.xlsx`, `dashboard.html`, `jobs.db`, and `runs.log`.

The workflow also runs every day at 07:00 UTC. Successful non-dry runs save `data/`
to a `job-search-data` branch, so the SQLite database and workbook survive between
GitHub-hosted runs. If GitHub blocks the branch push, enable read/write workflow
permissions in **Settings > Actions > General > Workflow permissions**.

To adjust what it searches for later, go back to the setup page or open a new
**Job search profile setup** issue and change the roles, skills, location, salary,
or exclusion fields. You can also edit `config/profile.json`, `config/sources.yaml`,
and `config/settings.yaml` directly in GitHub's web editor. Set `mode: active` in
`config/settings.yaml` if you want email digests sent from GitHub Actions; otherwise
the workflow still saves downloadable artifacts.

---

## Local Scheduling

The pipeline can also run daily via cron (Linux/macOS) or Task Scheduler (Windows).

**Linux/macOS cron** (runs at 07:00 every day):

```cron
0 7 * * * cd /path/to/job-search && .venv/bin/job-search run >> data/runs.log 2>&1
```

**Windows Task Scheduler**: create a basic daily task that runs:
```
C:\path\to\job-search\.venv\Scripts\job-search.exe run
```
with Start In set to `C:\path\to\job-search`.

**Tip**: point your `data/` folder at a cloud-synced directory (Dropbox, OneDrive, Google Drive) so `data/jobs.xlsx` and the generated `dashboard.html` are accessible from your phone.

---

## Domain packs

The pipeline supports any professional field. Run `job-search domains` to see available packs, or `job-search domains show engineering` to inspect one.

Available packs: `engineering`, `healthcare`, `creative`, `government`, `finance`, `legal`, `education`, `hospitality`, `trades`, `science`, `general`.

To add a new domain, create a single YAML file in `config/domains/` matching the schema of an existing pack. No code changes required.

---

## Prior art

This project's design was informed by:

- **[JobFunnel](https://github.com/PaulMcInnis/JobFunnel)** (archived Dec 2025) — pioneered the YAML-config + master spreadsheet workflow. Archived because direct LinkedIn/Indeed scraping became infeasible; we use JobSpy and official APIs instead.
- **[python-jobspy](https://github.com/Bunsly/JobSpy)** — actively maintained multi-board scraping library (LinkedIn, Indeed, Glassdoor, Google Jobs, ZipRecruiter). We use it as a dependency rather than rolling our own fragile scrapers.
- **[BjornMelin/ai-job-scraper](https://github.com/BjornMelin/ai-job-scraper)** — most architecturally similar: SQLite + FTS5, content-hash sync, cost tracking, 90/10 structured/AI adapter split. Key lessons adopted here.

---

## Configuration

All non-secret config lives in `config/`:
- `profile.json` — your CV data, skills, target roles, filters. Generated by `parse-cv`, hand-editable.
- `sources.yaml` — which job boards and company ATS pages to scrape (on/off per source).
- `ranker.yaml` — LLM ranking prompt, scoring rubric, pre-score threshold.
- `settings.yaml` — email, paths, schedule, model selection, API cost rates.
- `domains/` — one YAML per profession (seeded defaults, fully overridable).
