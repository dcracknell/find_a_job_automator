# UK Job Search Pipeline — Project Specification

A daily pipeline that scrapes UK job boards and company career pages for engineering roles matching a CV, ranks them with an LLM, deduplicates against a persistent Excel workbook, emails a digest of new matches, and regenerates a static HTML dashboard.

This document is the source of truth for architecture and design decisions. Build against this spec; if something here is ambiguous or wrong, fix it here first, then in the code.

---

## 1. Goals and non-goals

**Goals**
- Daily automated scan of UK job listings matched to a CV, across any field (engineering, healthcare, creative, government, finance, legal, education, hospitality, trades, etc.)
- SQLite as durable state (with Excel as the user-facing view, regenerated each run); dedup, status tracking, history
- Email digest of new high-fit jobs each run
- Static HTML dashboard regenerated each run, openable from phone via cloud sync
- Expandable to new sources via config (YAML) without code changes for ATS-based employers
- Expandable to new professional domains via config (YAML) without code changes — `domains/healthcare.yaml`, `domains/creative.yaml`, etc.
- All "tweaking" done by editing config files in a text editor — read-only UI
- Build on existing battle-tested libraries (JobSpy) rather than reimplementing fragile scrapers

**Non-goals (for v1)**
- Auto-applying to jobs
- Interactive UI for editing config (read-only dashboard only)
- Non-UK roles
- LinkedIn / Indeed scraping (high friction, ToS issues, covered by aggregator APIs)
- Mobile app — phone access is via cloud-synced HTML file
- Ranker self-improvement (calibration log captured in v1, acted on manually; auto-tuning is v2)
- LinkedIn connection cross-referencing (v2)
- University careers service scrapers (v2 — Sheffield-specific add later)
- Cover letter / interview prep generation (v2)
- Description-quality flagging (v2)

---

## 1.5 Prior art and architectural learnings

Several open-source projects have built related systems. Worth understanding what they got right and where they failed before reinventing anything:

**JobFunnel** (2.2k stars, archived Dec 2025) — pioneered the YAML-config + master spreadsheet + status workflow pattern that this project broadly follows. Notably archived because direct scraping of LinkedIn/Indeed/Glassdoor became operationally infeasible due to bot detection. **Lesson**: never depend on scraping the big aggregators directly; use libraries that abstract this away and use official APIs where possible.

**JobSpy / python-jobspy** — actively maintained library that wraps LinkedIn, Indeed, Glassdoor, Google Jobs, ZipRecruiter, Bayt, and Naukri with proxy support and rate limiting. Currently the de facto standard for multi-board scraping. **Lesson**: use JobSpy as a dependency rather than rolling our own — if it breaks, their maintainers fix it.

**BjornMelin/ai-job-scraper** — most architecturally similar project. Uses JobSpy for structured boards (~90% of coverage) and ScrapeGraphAI for unstructured company pages (~10%). Storage is SQLite + FTS5, not Excel. Has content-hash-based sync that preserves user-edited fields across re-scrapes. Cost tracking with budget alerts. Local-first LLM with cloud fallback. **Lesson**: SQLite as primary store, Excel as regenerated view; the 90/10 structured/AI split; content hashes for sync.

**ghiarishi/job-scraper** — Lever+Greenhouse to Excel with "Relevant" and "Maybe Relevant" sheets. Confirms the two-pass ranking pattern is a natural fit for this domain.

**Patterns adopted from prior art:**
- SQLite as primary store (Excel regenerated per run)
- JobSpy library dependency for LinkedIn/Indeed/Glassdoor/Google/ZipRecruiter
- Content-hash-based sync that preserves user-edited fields
- Status workflow including `archive` (distinct from `rejected` and `closed`)
- `--recover` command to rebuild DB from cached raw responses
- Cost tracking with budget alerts (we already had this)

**Patterns deliberately rejected:**
- Direct hand-rolled LinkedIn/Indeed scrapers (JobFunnel's mistake)
- Hardware lock-in to local LLMs (BjornMelin's RTX 4090 dependency) — cloud API + caching is more portable and the cost math is fine
- Live Streamlit UI (couples scraper to a running server) — our cron + static HTML is simpler and survives a closed laptop

---

## 2. High-level architecture

```
                 ┌──────────────┐
                 │  CV (PDF)    │
                 └──────┬───────┘
                        │  (one-off, regenerate on CV update)
                        ▼
                 ┌──────────────┐
                 │ profile.json │◄────── editable by hand
                 └──────┬───────┘
                        │
                        ▼
              ┌───────────────────┐
              │ query generator   │
              └─────────┬─────────┘
                        │
                        ▼
   ┌────────────────────────────────────────────┐
   │ source adapters (parallel)                 │
   │  - api: adzuna, reed, gov.uk find-a-job    │
   │  - jobspy: linkedin, indeed, glassdoor,    │
   │            google, ziprecruiter (opt-in)   │
   │  - ats: greenhouse, lever, workday         │
   │  - aggregators: hn-hiring, etc.            │
   └────────────────┬───────────────────────────┘
                    │
                    ▼
            ┌───────────────┐
            │ jd_clean      │   strip HTML, boilerplate, hash
            └───────┬───────┘
                    │
                    ▼
            ┌───────────────┐
            │ normalise     │   common JobRecord schema
            └───────┬───────┘
                    │
                    ▼
            ┌───────────────────┐
            │ sync vs SQLite    │   content-hash diff, preserves
            │  (jobs.db)        │   user-edited status/notes
            └───────┬───────────┘
                    │
                    ▼
            ┌───────────────┐
            │ ranker (API)  │   pass 1: keyword pre-score (free)
            │               │   pass 2: LLM (cached, batched)
            └───────┬───────┘
                    │
        ┌───────────┼───────────┬────────────┐
        ▼           ▼           ▼            ▼
    jobs.xlsx   digest.eml   dashboard.html  runs.log
    (regenerated view; user edits round-trip back via next run's import)
```

### 2.1 Storage philosophy

**SQLite (`data/jobs.db`)** is the source of truth. All dedup, history, status, notes, and ranking lives here.

**Excel (`data/jobs.xlsx`)** is a regenerated view. It's how the user *interacts* with their pipeline (because it's familiar, opens on any device, syncs via cloud), but it's not the database. Each run does:
1. Read user-edited columns (`status`, `notes`) from the existing Excel, write changes back to SQLite
2. Run the pipeline, updating SQLite
3. Regenerate Excel from SQLite

This round-trip is the only sane way to combine "Excel as the UX" with "SQLite as the data store". Concurrent edits during a run are detected via modification timestamp and surfaced as a warning rather than silently overwritten.

---

## 3. Repository layout

```
job-search/
├── PROJECT.md                 # this file
├── README.md                  # quick-start for the user
├── pyproject.toml             # dependencies, entry points
├── .env.example               # template for secrets
├── .gitignore
│
├── config/
│   ├── profile.json           # parsed CV (regenerable, editable)
│   ├── sources.yaml           # source on/off + ATS company list
│   ├── ranker.yaml            # ranker prompt + scoring rubric
│   ├── settings.yaml          # email, paths, schedule, filters, models
│   └── domains/               # one YAML per profession; user picks one in profile.json
│       ├── engineering.yaml
│       ├── healthcare.yaml
│       ├── creative.yaml
│       ├── government.yaml
│       ├── finance.yaml
│       ├── legal.yaml
│       ├── education.yaml
│       ├── hospitality.yaml
│       ├── trades.yaml
│       ├── science.yaml
│       └── general.yaml       # no presets, blank slate
│
├── job_search/
│   ├── __init__.py
│   ├── cli.py                 # entry point: `job-search run|parse-cv|dry-run`
│   │
│   ├── profile/
│   │   ├── __init__.py
│   │   ├── parse_cv.py        # PDF → profile.json via Anthropic API
│   │   └── queries.py         # profile.json → list of search queries
│   │
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py            # Adapter ABC + JobRecord dataclass
│   │   ├── adzuna.py          # REFERENCE IMPLEMENTATION — fully built
│   │   ├── reed.py
│   │   ├── gov_uk.py
│   │   ├── jobspy_adapter.py  # wraps python-jobspy library
│   │   ├── greenhouse.py      # generic ATS, takes a slug
│   │   ├── lever.py           # generic ATS, takes a slug
│   │   ├── workday.py         # generic ATS, takes a URL
│   │   ├── hn_hiring.py
│   │   └── domain/            # domain-specific adapters
│   │       ├── __init__.py
│   │       ├── nhs_jobs.py    # healthcare
│   │       ├── civil_service.py  # government
│   │       ├── tes.py         # education (Times Educational Supplement)
│   │       ├── caterer.py     # hospitality
│   │       ├── charityjob.py  # non-profit
│   │       ├── findaphd.py    # science/academia
│   │       ├── otta.py        # tech/creative/product
│   │       └── mandy.py       # creative (film/TV)
│   │
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── normalise.py       # raw adapter output → JobRecord
│   │   ├── dedup.py           # hashing + DB diff
│   │   ├── jd_clean.py        # strip boilerplate, truncate, hash JDs
│   │   ├── rank.py            # API call: JD + profile → score
│   │   └── filter.py          # salary floor, closing date, location
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── db.py              # SQLite schema, sync, queries (primary store)
│   │   ├── migrations/        # SQLite schema migrations, one file per version
│   │   │   ├── __init__.py
│   │   │   └── 001_initial.py
│   │   └── recovery.py        # rebuild DB from cached raw responses
│   │
│   ├── output/
│   │   ├── __init__.py
│   │   ├── workbook_export.py # SQLite → jobs.xlsx (regenerated view)
│   │   ├── workbook_import.py # jobs.xlsx user-edits → SQLite (round-trip)
│   │   ├── email_digest.py    # smtplib HTML digest + heartbeat
│   │   └── dashboard.py       # Jinja2 → dashboard.html
│   │
│   └── util/
│       ├── __init__.py
│       ├── http.py            # retry, backoff, rate limit, polite delay
│       ├── geocode.py         # Nominatim wrapper (cached)
│       ├── salary.py          # "£35k-40k" / "£18/hr" / "£450/day" → normalised
│       ├── dates.py           # closing-date extraction helpers (regex + domain patterns)
│       ├── domain.py          # load + merge domain packs from config/domains/
│       └── quota.py           # API token + cost tracking
│
├── templates/
│   ├── dashboard.html.j2
│   └── email.html.j2
│
├── data/
│   ├── jobs.db                # SQLite, primary store (gitignored)
│   ├── jobs.db-wal            # SQLite write-ahead log (gitignored)
│   ├── jobs.xlsx              # regenerated view, user-editable (gitignored)
│   ├── jobs.xlsx.tmp          # in-progress write, atomically renamed (gitignored)
│   ├── runs.log               # plaintext run log (gitignored)
│   ├── quota.jsonl            # token + cost log, one line per API call (gitignored)
│   ├── backups/               # rolling 7-day DB+xlsx backups (gitignored)
│   ├── cache/                 # raw scrape snapshots, namespaced per adapter (gitignored)
│   │   └── {adapter}/{YYYY-MM-DD}/
│   └── drafts/                # future: cover letter drafts (gitignored)
│
└── tests/
    ├── test_adapters_adzuna.py
    ├── test_dedup.py
    ├── test_salary.py
    └── fixtures/
        └── adzuna_response.json
```

---

## 4. Data model

### 4.1 `JobRecord` (in-memory, dataclass)

```python
@dataclass
class JobRecord:
    # Identity
    job_id: str               # sha1(company.lower() + title.lower() + canonical_url)
    source: str               # adapter name, e.g. "adzuna", "greenhouse:graphcore"

    # Posting
    title: str
    company: str
    location: str             # raw string from source
    lat: float | None         # geocoded
    lon: float | None
    url: str                  # canonical, query params stripped
    description: str          # full JD text
    posted_date: date | None
    closes_on: date | None    # extracted from description by API

    # Salary
    salary_raw: str | None    # original string
    salary_min: int | None    # GBP, annual
    salary_max: int | None

    # Ranking (filled in later stages)
    fit_score: float | None   # 0-10
    fit_reason: str | None    # one-line explanation
    fit_confidence: float | None  # 0-1, how sure the ranker is
    matched_keywords: list[str]   # which of the user's keywords appeared
    ranker_version: str | None    # which prompt version produced the score

    # Provenance
    matched_query: str | None # which search query surfaced this
    first_seen: date
    last_seen: date
    jd_content_hash: str | None  # sha1 of normalised JD text; used to skip re-rank on unchanged scrapes
```

### 4.2 SQLite database: `data/jobs.db`

The primary store. Created on first run with `meta.schema_version = 1`.

**Table `jobs`** — primary table, one row per unique posting:

```sql
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    -- Identity & provenance
    source TEXT NOT NULL,
    matched_query TEXT,
    first_seen DATE NOT NULL,
    last_seen DATE NOT NULL,
    -- Status (user-editable, round-trips via Excel)
    status TEXT NOT NULL DEFAULT 'new',  -- new/applied/interview/offer/rejected/ignore/archive/closed
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
    matched_keywords TEXT,           -- JSON array
    ranker_version TEXT,
    -- Hashes for sync
    jd_content_hash TEXT,
    -- Bookkeeping
    last_user_edit TIMESTAMP         -- when status or notes last changed via Excel round-trip
);

CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_company ON jobs(company);
CREATE INDEX idx_jobs_fit_score ON jobs(fit_score DESC);
CREATE INDEX idx_jobs_last_seen ON jobs(last_seen);

-- Full-text search (porter stemming) for "find that job from last month" queries
CREATE VIRTUAL TABLE jobs_fts USING fts5(
    title, company, description,
    content='jobs', content_rowid='rowid',
    tokenize='porter'
);
```

**Table `runs`** — one row per pipeline run (timestamp, duration_s, sources_ok, sources_failed, jobs_scraped, jobs_new, jobs_closed, errors as JSON).

**Table `meta`** — single-row key/value: `schema_version`, `ranker_version_at_last_run`, `last_successful_run`, `last_heartbeat_sent`.

**Table `api_calls`** — token-usage log (timestamp, operation, model, input_tokens, cached_input_tokens, output_tokens, est_cost_gbp). Sourced from `quota.jsonl`, materialized into the DB for query convenience.

### 4.2a Excel as a regenerated view: `data/jobs.xlsx`

The workbook is **not** the source of truth. It is regenerated from SQLite at the end of each run. The user edits `status` and `notes`; on the next run, those edits are imported back into SQLite before the pipeline starts.

**Sheets:**
- `jobs` — all rows, columns matching the SQLite `jobs` table; conditional formatting applied (green ≥8, amber 5-7.9, red <5, closes-soon bold red, archived greyed)
- `runs` — last 30 runs
- `profile` — read-only mirror of `profile.json`
- `quota` — last 30 days of API spend, grouped by day and operation

**Status values** (for the `status` column in both DB and Excel):
- `new` — default; never been actioned
- `applied` — application submitted
- `interview` — at any interview stage
- `offer` — offer received (may still decline)
- `rejected` — rejection from the company (triggers cooldown for that company)
- `ignore` — user dismissed; don't surface in digests, but keep the row
- `archive` — user no longer interested but wants the historical record (hides from default view in Excel via filter, kept in DB)
- `closed` — system-set: job not seen in 14+ days, presumed filled or removed

**Import round-trip rules (Excel → SQLite):**
1. At run start, read the existing Excel; if it exists, import any user changes to `status` or `notes` into SQLite, using Excel file modification time vs DB `last_user_edit` to detect changes
2. On true conflicts (both sides changed since last sync), prefer Excel and log a warning to the `runs` table
3. If Excel is missing or unreadable, log a warning and proceed (the DB is authoritative)
4. At run end, regenerate Excel from DB

This is the cost of having Excel as the UI; it's worth it for the device-agnostic, cloud-syncable, familiar UX.

### 4.3 `profile.json`

Output of CV parser, hand-editable. Domain-neutral schema — the *content* depends on the user's field, but the *fields themselves* work for any profession:

```json
{
  "name": "...",
  "domain": "engineering",
  "secondary_domains": [],

  "location": {"city": "Sheffield", "lat": 53.38, "lon": -1.47},
  "search_radius_miles": 60,
  "remote_ok": true,

  "education": {
    "highest_qualification": "MEng Electronic and Computer Engineering",
    "institution": "University of Sheffield",
    "completion_year": 2026,
    "grade": "first-class (projected)",
    "registrations": []
  },

  "experience_years": 1,
  "experience_summary": "Two summer internships at AMRC Cymru...",

  "core_skills": ["FPGA", "VHDL", "Verilog", "embedded C", "Python", "MATLAB"],
  "adjacent_skills": ["digital design", "SoC validation", "DSP", "ML"],

  "negative_signals": {
    "title_excludes": ["senior", "staff", "principal", "lead", "head of", "director"],
    "requires_years_above": 3,
    "description_excludes": ["security clearance required", "SC cleared", "must have UK SC"],
    "company_blocklist": []
  },

  "target_roles": {
    "core": ["FPGA Engineer", "Digital Design Engineer", "SoC Validation Engineer"],
    "adjacent": ["ASIC Verification", "Embedded Engineer", "RTL Designer"],
    "stretch": ["DSP Engineer", "Hardware Engineer", "Signal Processing Engineer"]
  },

  "filters": {
    "salary_floor_gbp": 30000,
    "salary_unit": "annual",
    "max_days_since_posted": 30,
    "exclude_companies": [],
    "rejected_company_cooldown_days": 90
  }
}
```

**Field notes for non-engineering domains:**
- `domain` selects which `domains/{name}.yaml` pack is loaded. Default `general` = no presets.
- `education.registrations` holds professional bodies that matter in your field — e.g. `[{"body": "NMC", "pin": "12A3456E", "expires": "2027-04"}]` for nursing, `[{"body": "SRA", "number": "..."}]` for law, `[{"body": "IET", "level": "MIET"}]` for engineering. The domain pack guides the CV parser to extract these.
- `core_skills` / `adjacent_skills` are free-form strings — they're keywords the ranker looks for. Domain packs seed sensible examples (`patient assessment` for healthcare, `LaTeX` for academia, `commis chef` for hospitality, `bookkeeping` for finance).
- `filters.salary_unit` — `annual` / `hourly` / `daily` / `sessional`. Default annual. Domain packs set this (hospitality and trades default to `hourly`; some healthcare and consulting to `daily` or `sessional`).

### 4.4 `sources.yaml`

```yaml
apis:
  adzuna:
    enabled: true
    country: gb
    results_per_query: 50
  reed:
    enabled: true
  gov_uk_find_a_job:
    enabled: true

jobspy:
  # Wraps python-jobspy. Aggressive bot detection means these can break;
  # JobSpy's maintainers fix it, not us. Off by default; enable when needed.
  enabled: false
  sites: [indeed, linkedin, glassdoor, google, zip_recruiter]
  country: uk
  results_wanted_per_query: 25
  proxies: []  # optional, populate when scraping at scale

aggregators:
  hn_hiring:
    enabled: true

ats:
  greenhouse:
    companies:
      - {name: Graphcore, slug: graphcore}
      - {name: Cerebras, slug: cerebras}
  lever:
    companies:
      - {name: SiFive, slug: sifive}
  workday:
    companies:
      - {name: Arm, url: https://arm.wd3.myworkdayjobs.com/Careers}
      - {name: Dyson, url: https://careers.dyson.com/...}
```

### 4.5 `ranker.yaml`

Externalised so the prompt is editable without code changes. Includes prompt template, version string (for history), scoring rubric, and few-shot examples.

### 4.6 `settings.yaml`

Email config (SMTP host, from, to), output paths, log level, schedule expression (read by cron — this file just documents what was set), dry-run flag, **run mode** (`active` / `passive` / `paused`), **quota soft cap** (daily GBP threshold for warning, default `2.00`), **heartbeat** settings, and a `models` block that selects model + per-token rates per operation:

```yaml
models:
  parse_cv:
    model: claude-sonnet-4-6
    input_gbp_per_million: 2.40   # update when rates change
    output_gbp_per_million: 12.00
  rank:
    model: claude-haiku-4-5-20251001
    input_gbp_per_million: 0.64
    output_gbp_per_million: 3.20
    cached_input_gbp_per_million: 0.064  # cached reads ~90% cheaper
    max_jd_tokens: 1500
    batch_size: 5
    max_tokens_response: 200
  queries:
    model: claude-haiku-4-5-20251001
  extract_closing_date:
    model: claude-haiku-4-5-20251001
```

Rates are placeholders in the config; user updates them from Anthropic pricing docs. Code reads these for the quota tracker — never hard-codes pricing.

### 4.7 `domains/*.yaml` — domain packs

The system supports any professional field, not just engineering. A "domain pack" is a YAML file in `config/domains/` that seeds sensible defaults for a given field. The user picks one in `profile.json:domain` (or `general` for no presets). Everything in a domain pack can be overridden by the user's profile and other config files; the pack is a starting point, not a constraint.

**Schema:**

```yaml
# config/domains/healthcare.yaml
name: healthcare
display_name: "Healthcare & Nursing"

# Recommended source priorities (overlaid on sources.yaml)
recommended_sources:
  enable_by_default:
    - nhs_jobs
    - gov_uk_find_a_job
    - reed
    - adzuna
  disable_by_default:
    - jobspy           # LinkedIn etc less useful here
    - hn_hiring        # tech-specific

# Domain-specific adapters (referenced from sources.yaml when enabled)
domain_adapters:
  nhs_jobs:
    enabled: true
    base_url: "https://www.healthjobsuk.com"

# Salary parsing hints
salary:
  default_unit: annual_gbp      # annual_gbp / hourly_gbp / daily_gbp / sessional_gbp
  also_parse: [hourly_gbp]
  agenda_for_change_bands: true # special: parse NHS pay bands like "Band 5", "Band 6"

# Seniority taxonomy used in negative_signals
seniority_words:
  junior: [newly qualified, nqn, foundation, "band 5"]
  mid:    [senior, "band 6", "band 7", specialist]
  senior: [advanced, "band 8a", "band 8b", consultant, lead, matron]
  exclude_above_default: senior

# Closing-date regex patterns (extended beyond commercial defaults)
closing_date_patterns:
  - "closing date[:\\s]+([^\\n]+)"
  - "applications close[:\\s]+([^\\n]+)"
  - "deadline[:\\s]+([^\\n]+)"
  - "interviews to be held"     # signal that deadline is imminent

# Ranker prompt overlay — injected into the system prompt as context
ranker_context: |
  This is a healthcare role search. Pay attention to:
    - NMC/HCPC registration requirements
    - Agenda for Change bands (Band 5 = newly qualified nurse, Band 7+ = senior)
    - Specialism (ITU, paeds, mental health, district, etc.)
    - Shift patterns and on-call requirements
  Penalise jobs requiring a different specialism than the candidate's stated one.

# CV parser prompt overlay
cv_parser_context: |
  Extract: NMC/HCPC PIN if present, current band, specialism(s), wards/settings
  worked, mandatory training currency, revalidation status, postgraduate
  qualifications, leadership roles. Do not infer specialism from generic terms.

# Example skills taxonomy (seeded into profile.json on `parse-cv --domain healthcare`)
example_skills:
  core: ["patient assessment", "medication administration", "clinical documentation"]
  adjacent: ["mentoring", "audit", "infection control"]

# Example role taxonomy
example_target_roles:
  core: ["Staff Nurse", "Specialist Nurse", "Charge Nurse"]
  adjacent: ["Practice Nurse", "Community Nurse"]
  stretch: ["Nurse Practitioner", "Clinical Specialist"]
```

**Domain packs shipped in v1** (`config/domains/`):
- `engineering.yaml` — FPGA/SoC/embedded/software, etc. (the original profile pattern)
- `healthcare.yaml` — nursing, allied health professions, NHS-flavoured
- `creative.yaml` — design, writing, marketing, film/TV, music
- `government.yaml` — civil service, local government, public sector
- `finance.yaml` — banking, accounting, fintech, investment
- `legal.yaml` — solicitors, paralegals, in-house counsel
- `education.yaml` — teaching, lecturing, academia, EdTech
- `hospitality.yaml` — chefs, FOH, hotel management, events
- `trades.yaml` — electricians, plumbers, carpenters, HGV
- `science.yaml` — research, lab, R&D, biotech, pharma
- `general.yaml` — no presets, everything user-defined

**How packs interact with the rest of the config:**

1. On `parse-cv --domain <name>`, the parser is given the domain's `cv_parser_context` to guide extraction, and the resulting `profile.json` has `domain: <name>` set and example skills/roles seeded from the pack.
2. On every run, the pipeline loads `domains/{profile.domain}.yaml` and:
   - Applies `recommended_sources` only on first run (or via `--reset-sources` flag); user changes to `sources.yaml` take precedence afterwards
   - Adds `ranker_context` to the system prompt
   - Applies domain salary parsing rules in `util/salary.py`
   - Adds `closing_date_patterns` to the regex list
   - Uses `seniority_words` as defaults if `negative_signals.title_excludes` is empty
3. The user can override anything from the pack in `profile.json` or other configs — the pack is only consulted for fields the user hasn't explicitly set.

**Adding a new domain** is creating one YAML file in `config/domains/`. No code change. The file is validated against a Pydantic schema at load time.

**Multi-domain users** (e.g. "I'm an engineer but open to product roles") set `profile.json:domain` to the primary and add `secondary_domains: [product]`. Adapters from both packs are merged; ranker_context is concatenated.

Email config (SMTP host, from, to), output paths, log level, schedule expression (read by cron — this file just documents what was set), dry-run flag, **run mode** (`active` / `passive` / `paused`), **quota soft cap** (daily GBP threshold for warning, default `2.00`), **heartbeat** settings, and a `models` block that selects model + per-token rates per operation:

```yaml
models:
  parse_cv:
    model: claude-sonnet-4-6
    input_gbp_per_million: 2.40   # update when rates change
    output_gbp_per_million: 12.00
  rank:
    model: claude-haiku-4-5-20251001
    input_gbp_per_million: 0.64
    output_gbp_per_million: 3.20
    cached_input_gbp_per_million: 0.064  # cached reads ~90% cheaper
    max_jd_tokens: 1500
    batch_size: 5
    max_tokens_response: 200
  queries:
    model: claude-haiku-4-5-20251001
  extract_closing_date:
    model: claude-haiku-4-5-20251001
```

Rates are placeholders in the config; user updates them from Anthropic pricing docs. Code reads these for the quota tracker — never hard-codes pricing.

---

## 5. Source adapters

### 5.1 Adapter contract

```python
class Adapter(ABC):
    name: str

    @abstractmethod
    def fetch(self, queries: list[str], settings: dict) -> list[RawJob]: ...

    @abstractmethod
    def normalise(self, raw: RawJob) -> JobRecord: ...

    def healthcheck(self) -> tuple[bool, str | None]:
        """Return (ok, error_message). Default: try a trivial fetch."""
```

Each adapter returns a list of raw responses; the pipeline calls `normalise` to convert to `JobRecord`. List rather than iterator so the raw responses can be cached to `data/cache/{adapter_name}/{YYYY-MM-DD}/` for replay during debugging — adapters that stream would defeat caching.

### 5.2 Priority order for implementation

1. **Adzuna** — fully built as the reference. UK jobs API, free tier ~250 calls/day, returns JSON with title/company/location/salary/url/description.
2. **Reed** — UK graduate/engineering coverage. Free with registration. JSON API.
3. **Greenhouse** — generic ATS. Endpoint: `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`. Public, no auth.
4. **Lever** — generic ATS. Endpoint: `https://api.lever.co/v0/postings/{slug}?mode=json`. Public, no auth.
5. **Workday** — generic ATS, harder (URL varies per tenant). Often `{tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` accepts POST with JSON.
6. **GOV.UK Find a Job** — official API, XML feed. Covers some engineering roles.
7. **JobSpy adapter** — thin wrapper around the `python-jobspy` library. Covers LinkedIn, Indeed, Glassdoor, Google Jobs, ZipRecruiter. **Off by default in `sources.yaml`** — these sites have aggressive bot detection and even with the library wrapping it, results can be inconsistent. Enable when the structured APIs above aren't producing enough volume; accept that you might need proxies and that some sites may fail any given run.
8. **HN Who is Hiring** — scrape monthly thread on `news.ycombinator.com`. Filter for UK + relevant tech.

### 5.3 Generic ATS adapters

Greenhouse, Lever, Workday adapters each accept a list of companies from `sources.yaml` and iterate them. Adding a new employer = one line in YAML.

---

## 6. Pipeline stages

### 6.1 CV parsing (`profile/parse_cv.py`)

- Run on demand (`job-search parse-cv path/to/cv.pdf --domain healthcare`), not every run
- The `--domain` flag selects which pack to use; the pack's `cv_parser_context` is prepended to the system prompt to guide extraction (e.g. for healthcare: extract NMC PIN, band, specialism; for law: extract SRA number, practice areas; for engineering: extract tech stack, projects)
- Without `--domain`, the parser asks the user to pick from the available packs, or defaults to `general`
- Reads PDF, sends text to Anthropic API with a strict JSON-only system prompt
- **Critical**: prompt must say "use only facts present in the provided CV; do not invent or infer experience". This is a hard requirement — application materials must never be grounded in assumed context.
- Writes `config/profile.json` with `domain` set; seeds example skills/roles from the pack as placeholders the user can refine; prompts user to review

### 6.2 Query generation (`profile/queries.py`)

Reads `profile.json`, generates 10–30 search query variants by combining role names with locations and seniority words. Output is a `list[str]` used by every adapter. Pure function, no API call.

### 6.3 Normalisation (`pipeline/normalise.py`)

- Canonical URL: strip query parameters except those required for the listing to load
- Salary parsing via `util/salary.py` — handles multiple units depending on `profile.filters.salary_unit` and the domain pack:
  - Annual: `£35k`, `£35,000`, `35000-40000`
  - Hourly: `£18/hr`, `£18 per hour` (common in hospitality, trades, retail, healthcare bank shifts)
  - Daily: `£450/day`, `£450 per day` (common in contracting, consulting, locum work)
  - Sessional: `£X per session` (healthcare, especially GP and consultant work)
  - NHS Agenda for Change bands: `Band 5`, `Band 6 (£35,392 - £42,618)` — looked up against the current AfC table loaded from the domain pack
  - Sentinel values: `Competitive` → None, `DOE` → None, `Negotiable` → None
- Each salary is normalised to *both* an annual GBP estimate (for ranking and filtering) and its original unit (preserved in `salary_raw` for display). Annualisation rules from the domain pack (default: hourly × 1880 working hours/year, daily × 230 working days/year).
- Geocode location once and cache (`util/geocode.py`, Nominatim free tier)
- Closing date extraction: regex first using both the universal patterns and the domain pack's `closing_date_patterns`; API fallback for high-fit jobs only

### 6.4 Dedup and sync (`pipeline/dedup.py` + `storage/db.py`)

- Compute `job_id = sha1(company.lower() + title.lower() + canonical_url)`
- For each scraped job, query SQLite for existing `job_id`:
  - **Not in DB** → insert with current date as `first_seen` and `last_seen`; mark as new (will be ranked)
  - **In DB, JD unchanged** (`jd_content_hash` matches) → update `last_seen` only; preserve all other fields including ranking and user edits
  - **In DB, JD changed** → update `last_seen`, `description`, `jd_content_hash`, `salary_*`; preserve `status`, `notes`, `fit_score` (re-rank only if `--rerank-stale` or score is >30 days old)
- After processing: any row where `last_seen < today - 14 days` and `status = new` → set `status = closed`
- Content-hash sync ensures user edits never get clobbered by re-scrapes (the key thing JobFunnel and forks sometimes got wrong)

### 6.5 Filtering (`pipeline/filter.py`)

Applied **before** ranking to save API calls. Drop:
- `salary_max < salary_floor_gbp` (but keep nulls — many real jobs omit salary)
- `posted_date < today - max_days_since_posted`
- Company in `exclude_companies` or in cooldown (rejected within last N days, found by scanning workbook for `status = rejected`)
- Distance from home > `search_radius_miles` AND `remote_ok = false` AND location isn't "Remote"

### 6.6 Ranking (`pipeline/rank.py`)

Two-pass design to control API spend, with multiple token-reduction tactics layered on top.

**Pass 1 — keyword pre-score (no API call, free).** For each new job, compute a preliminary score from:
- Count of `core_skills` matches in JD (weight ×3)
- Count of `adjacent_skills` matches in JD (weight ×1)
- Penalty for any `negative_signals.title_excludes` match in title (large negative)
- Penalty for any `negative_signals.description_excludes` match in JD (large negative)

This produces a `pre_score` in roughly the same 0–10 range. Anything scoring below a threshold (default 3, configurable in `ranker.yaml`) skips pass 2 and is stored with `fit_score = pre_score`, `fit_confidence = low`, `fit_reason = "filtered by keyword pre-scan"`.

**Pass 2 — LLM rank (API call).** Only for jobs surviving pass 1. Applies *all* of the following:

1. **Model selection per operation** (set in `settings.yaml`):
   - `parse_cv` → Sonnet (rare, needs accuracy)
   - `queries` → Haiku (trivial templating)
   - `rank` → Haiku by default, configurable
   - `extract_closing_date` → Haiku (regex fallback)

2. **Domain context overlay**: the system prompt is concatenated from the universal ranker template (`ranker.yaml`) and the active domain pack's `ranker_context`. This is part of the cached prefix so it costs almost nothing per call.

3. **JD preprocessing before send** (`pipeline/jd_clean.py`):
   - Strip HTML, normalise whitespace
   - Remove boilerplate sections matching configured regexes (equal opportunity, benefits, company history, application instructions)
   - Truncate to `max_jd_tokens` (default 1,500); long JDs get middle-truncation with `[... truncated ...]` marker preserving start and end
   - Result typically 40–60% of original size

4. **Prompt caching via `cache_control`**:
   - System prompt + scoring rubric + domain context: `cache_control: ephemeral` (cached across all calls in a run)
   - Profile JSON: `cache_control: ephemeral` (cached across all calls in a run)
   - JD: not cached (varies per call)
   - Break-even at ~3 calls; warm cache reduces input tokens 60–75% on subsequent calls

5. **Batched calls**:
   - Send up to 5 JDs per API call, ask for JSON array of scores
   - On malformed response, retry the failed batch with batch size 1 to isolate
   - Cap: 5 per batch (keeps retry cost contained)

6. **Compact output schema** — use short keys to minimise output tokens:
   ```json
   {"s": 8.2, "c": 0.7, "r": "FPGA + SoC validation match", "k": ["FPGA", "RTL"]}
   ```
   Internally remapped to `fit_score`, `fit_confidence`, `fit_reason`, `matched_keywords`. Prompt instructs: "reason: one sentence, max 20 words, no preamble; keywords: max 5".

7. **`max_tokens` ceiling**: set to 200 for ranking calls. Prevents runaway generation.

8. **Token logging**: every call records `{input_tokens, output_tokens, cached_input_tokens, model, operation}` to `quota.jsonl` via `util/quota.py`.

**Re-rank policy and cache invalidation.** On each run:
- Compute `prompt_content_hash` from `ranker.yaml` excluding comments and whitespace — minor edits don't invalidate cache
- If stored `ranker_version` matches AND `prompt_content_hash` matches, skip re-rank
- If `--rerank-stale` flag set, re-rank rows where either differs
- Without the flag, log count: `"42 jobs scored with v1 (current v2) — pass --rerank-stale to update"`

**Skip re-rank on minor JD changes**: store a `jd_content_hash` per job (sha1 of normalised JD text). If a re-scrape produces a JD whose hash differs by less than ~10% similarity (via simple shingling), keep the existing score.

### 6.6a Quota tracking (`util/quota.py`)

Every Anthropic API call logs `{timestamp, operation, model, input_tokens, cached_input_tokens, output_tokens, est_cost_gbp}` to `data/quota.jsonl`. Cost calculation uses per-model rates from `settings.yaml` (so rate changes don't require code changes). Dashboard surfaces today's total, this-month's total, projected month-end cost, and a breakdown by operation. Per-day soft cap configurable in `settings.yaml` (default £2/day) — exceeding it doesn't stop the run but logs a prominent warning to `runs` sheet.

### 6.6b Batch API mode (optional)

For non-urgent expansion runs (e.g. weekly catch-up across all configured sources), the Anthropic Batch API offers ~50% discount with 24-hour SLA. Opt-in via:
- `job-search run --batch` submits jobs to Batch API, exits without populating workbook
- `job-search collect-batch` (run next day or in a follow-up cron) retrieves results and populates workbook

Not used for daily runs (latency too high). Useful for one-off bulk re-ranks after a prompt change.

### 6.7 Output

**Workbook regeneration (`output/workbook_export.py`)**: openpyxl. SQLite → `jobs.xlsx`. Apply conditional formatting. Atomic write (build as `jobs.xlsx.tmp`, rename on success). Backup before overwrite to `data/backups/jobs.YYYY-MM-DD.xlsx`; keep last 7 daily backups rolling.

**Workbook import (`output/workbook_import.py`)**: openpyxl, read-only. At the start of each run, read existing `jobs.xlsx` if present and import any user-edited `status` or `notes` back to SQLite, using `last_user_edit` timestamps to detect changes and resolve conflicts (Excel wins on true conflict, with warning logged).

**Database backup**: alongside the Excel backup, copy `jobs.db` to `data/backups/jobs.YYYY-MM-DD.db`. SQLite's `VACUUM INTO` makes this fast and consistent without needing to lock the database.

**Schema migrations**: SQLite schema is versioned via the `meta` table. On startup, code reads `schema_version` and runs any pending migrations from `storage/migrations/`. Migrations are forward-only, idempotent, and tested.

**Email (`output/email_digest.py`)**: HTML email via smtplib. Mobile-first CSS — single column, large tap targets, no horizontal tables, max-width 600px. Subject: `Job digest — N new (X high-fit)`. Body sections in order:
1. Closing soon (any open job with `closes_on` within 7 days, regardless of newness)
2. New high-fit jobs (score ≥ 7, sorted by score)
3. New medium-fit jobs (score 5–6.9, top 10 only)
4. Pipeline summary (counts per status)
5. Adapter health footer
6. "View full dashboard" link to the cloud-synced `dashboard.html`
7. Quota footer: today's API spend

**Heartbeat email**: If a run produces zero new jobs AND the last 2 prior runs also produced zero new jobs, send a single "system alive but quiet" email instead of the regular digest. Suppresses for 7 days after sending. Tracked via `meta.last_heartbeat_sent`.

**Dashboard (`output/dashboard.py`)**: Jinja2 → `dashboard.html`. Single file, inline CSS, no external requests so it works offline on phone. Mobile-first CSS. Sections:
- Last run banner (timestamp, jobs found, errors at a glance, run mode)
- Quota panel (today, month, projected, soft-cap status, cache hit rate)
- New today table
- Closing soon table
- Full pipeline kanban-style (read-only columns)
- Top employers this month
- Per-source health (green/amber/red with last error)
- Ranker calibration hint: count of "high-scored but you rejected" pairs
- Search box (deferred to v2 — for now `job-search search` CLI handles FTS5 queries)

---

## 7. Quality-of-life features (v1 scope)

These are confirmed in scope for the initial build:

- **Closing-date detection** — regex first (free), LLM fallback only for high-fit jobs
- **Salary normalisation + floor filter** — drops obvious noise
- **Negative signals** in profile — title/description excludes, year requirements, blocklist
- **Per-company cooldown after rejection** — 90 days default, configurable
- **Per-adapter health logging** — visible in `runs` table and dashboard
- **Excel conditional formatting** — score colours, status formatting, closing-soon highlight
- **Stale row auto-archive** — `last_seen` > 14 days → `status = closed`
- **Raw response cache** — 7-day TTL, enables replay during debugging AND DB recovery
- **`job-search recover`** — rebuild entire DB from cached raw responses (learned from JobFunnel)
- **Dry-run mode** — `--dry-run` skips DB writes and email send
- **Two-pass ranking** — keyword pre-score filters before LLM, cuts API spend ~70%
- **Quota tracking** — token + cost log, daily/monthly view on dashboard, soft cap warning
- **SQLite schema versioning + migrations** — safe to refactor; never lose user data
- **Content-hash-based sync** — re-scrapes never clobber user-edited `status` and `notes`
- **Atomic DB + Excel writes + 7-day backups** — protects against corruption and bad runs
- **Heartbeat email** — sent only when system has been quiet 3+ days, suppresses for 7 after
- **Mobile-first CSS** — email and dashboard usable on phone
- **Run modes** — `active` / `passive` / `paused` in `settings.yaml`, switchable without code
- **Status workflow with `archive`** — distinct from `rejected` and `closed`
- **FTS5 search** — `job-search search "ward sister manchester"` runs porter-stemmed full-text query against historical jobs
- **JobSpy as optional adapter** — wraps LinkedIn/Indeed/Glassdoor/Google/ZipRecruiter; off by default, single flag to enable
- **Domain packs** — works for any field (healthcare, creative, government, finance, legal, education, hospitality, trades, science, engineering, general). Each pack ships in `config/domains/` and seeds source recommendations, salary parsing rules, seniority taxonomy, closing-date patterns, ranker context, and CV parser context. Adding a new domain is one YAML file.
- **Multi-unit salary parsing** — annual, hourly, daily, sessional, NHS Agenda for Change bands. Normalised to annual GBP for ranking, original unit preserved for display.
- **Domain-specific adapters** — NHS Jobs (healthcare), Civil Service Jobs (government), TES (education), CatererGlobal (hospitality), CharityJob (non-profit), FindAPhD (science), Otta (tech/creative), Mandy (creative/film). Each enabled/disabled per domain pack.

**Token-reduction tactics in v1 (all in `pipeline/rank.py`):**
- Per-operation model selection (Haiku for cheap, Sonnet for hard) — cost reduction ~80% on ranking
- Prompt caching via `cache_control` on system + profile — input token reduction ~65% after warm-up
- JD preprocessing (HTML strip, boilerplate regex removal, middle-truncate to 1,500 tokens) — input token reduction ~50%
- Batched ranking calls (up to 5 JDs per call) — overhead reduction ~35%
- Compact short-key output schema with strict `max_tokens` ceiling — output token reduction ~40%
- JD content hash to skip re-rank on unchanged scrapes
- Prompt content hash (ignoring whitespace/comments) to avoid accidental cache invalidation
- Configurable per-token rates in `settings.yaml` so cost tracking stays accurate when prices change

Deferred to v2:
- Cover letter / interview prep generation
- Discovery mode (auto-find new employers)
- Diff awareness (description changes between runs, surfaced in digest)
- Saved-search personas
- Ranker calibration auto-tuning (manual hint visible on dashboard from v1)
- LinkedIn connection cross-referencing (CSV import)
- University careers service scrapers (Sheffield first)
- One-tap mailto/tel actions in email
- Description-quality flagging
- Batch API mode for bulk re-ranks
- ScrapeGraphAI-based adapter for unstructured company career pages (the 10% JobSpy doesn't cover)
- Streamlit-based search UI (CLI search is in v1)

---

## 8. Configuration and secrets

- All non-secret config in `config/*.yaml` and `config/profile.json`
- Secrets in `.env`, loaded via `python-dotenv`: `ANTHROPIC_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `REED_API_KEY`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`, `SMTP_TO`
- `.env.example` committed, `.env` gitignored

---

## 9. CLI

```
job-search parse-cv <path/to/cv.pdf> --domain <name>   # regenerate profile.json
job-search domains                                      # list available domain packs
job-search domains show <name>                          # print a domain pack's contents
job-search run                                          # full pipeline
job-search run --dry-run                                # no DB write, no Excel write, no email
job-search run --source adzuna                          # one adapter only, for debugging
job-search run --rerank-stale                           # re-rank rows scored with old prompt
job-search run --save-fixture adzuna                    # dump raw response for tests
job-search rank <job_id>                                # re-rank a single job
job-search health                                       # run healthcheck on every adapter
job-search migrate                                      # run any pending DB schema migrations
job-search backup                                       # manual DB + Excel backup
job-search recover                                      # rebuild DB from cached raw responses
job-search export                                       # regenerate Excel from DB without running pipeline
job-search search "ward sister manchester"              # FTS5 search over historical jobs
```

---

## 10. Scheduling

Out of scope for the code itself. Document in `README.md`:
- **Linux/macOS**: cron entry, e.g. `0 7 * * * cd /path/to/job-search && .venv/bin/job-search run`
- **Windows**: Task Scheduler equivalent
- Output to a cloud-synced folder (Dropbox/OneDrive/Drive) so dashboard.html and jobs.xlsx are accessible from phone

---

## 11. Dependencies

Core:
- `anthropic` — API client
- `python-jobspy` — wraps LinkedIn/Indeed/Glassdoor/Google/ZipRecruiter scrapers
- `openpyxl` — Excel read/write
- `jinja2` — dashboard templates
- `pydantic` — config validation
- `pyyaml` — config files
- `requests` + `tenacity` — HTTP with retry
- `python-dotenv` — secrets
- `click` — CLI
- `beautifulsoup4` — HTML scrapers (HN aggregator)
- `pypdf` or `pdfplumber` — CV reading

(No external SQLite dependency needed — Python's stdlib `sqlite3` handles everything including FTS5.)

Dev:
- `pytest`, `pytest-mock`
- `ruff` — lint + format

Python 3.11+.

---

## 12. Build order (acceptance criteria per phase)

### Phase 1 — Scaffolding
- Repo structure created, dependencies installed (including `python-jobspy`)
- `.env.example`, `.gitignore`, `README.md` quick-start present
- CLI runs `job-search --help` successfully, all subcommands listed
- `data/jobs.db` created with schema from section 4.2 (including FTS5 virtual table and `meta` row with `schema_version = 1`)
- Empty `jobs.xlsx` regenerated from empty DB with correct headers, conditional formatting, status filter
- Atomic write helpers in place for both DB (transaction-based) and Excel (.tmp + rename)
- Quota log module stubbed
- The Excel round-trip module is present but reading an empty/missing Excel is a no-op
- All 11 domain packs (`engineering`, `healthcare`, `creative`, `government`, `finance`, `legal`, `education`, `hospitality`, `trades`, `science`, `general`) present in `config/domains/` with full schema filled in — adapters they reference can be stubs at this stage
- `util/domain.py` loads + validates packs against a Pydantic schema; `job-search domains` and `job-search domains show <name>` work

**Done when**: `pip install -e .` works and `job-search run` produces `jobs.db` + empty `jobs.xlsx` without error. `job-search export` regenerates the Excel from DB. `job-search domains` lists all 11 packs with their `display_name`.

### Phase 2 — CV parser + Adzuna end-to-end
- `parse-cv --domain <name>` produces a valid `profile.json` from a PDF, including `negative_signals` section, with `domain` set and pack-seeded example skills/roles
- The CV parser prompt includes the active domain's `cv_parser_context`
- Adzuna adapter fetches real listings via the official API
- `pipeline/jd_clean.py` strips HTML and obvious boilerplate, computes `jd_content_hash`, applies middle-truncation
- `util/salary.py` handles annual/hourly/daily/sessional units per the active domain pack's defaults; NHS AfC bands parse correctly under `domain: healthcare`
- Pipeline syncs scraped jobs into SQLite (content-hash-based dedup; preserves user-edited `status` and `notes` if any existed)
- Keyword pre-scoring (pass 1 of ranking) runs and populates `fit_score` with confidence `low`
- Excel regenerated from DB after each run, with conditional formatting
- No LLM ranking yet, no email yet

**Done when**: running on a real CV under both `engineering` and `healthcare` domains produces sensible profiles, ~20+ Adzuna rows in SQLite per run, hourly-salary jobs are correctly annualised under `healthcare` defaults, and editing a `status` cell in Excel then re-running preserves the edit.

### Phase 3 — Reed + LLM ranker + email + heartbeat
- Reed adapter added (same pattern as Adzuna — official API)
- LLM ranker (pass 2) calls Anthropic API only for jobs above pre-score threshold
- Ranker system prompt includes the universal `ranker.yaml` template AND the active domain pack's `ranker_context` — both inside the `cache_control` block so the per-call cost is unchanged
- Ranker uses Haiku from `settings.yaml`, batches up to 5 JDs per call, applies `cache_control`, uses compact short-key output schema with `max_tokens=200`
- `fit_confidence` and `ranker_version` populated; `prompt_content_hash` computed and stored (and includes the domain hash so swapping domains correctly invalidates the cache)
- Quota log captures every API call including `cached_input_tokens`
- Dashboard shows quota panel with today/month spend and cache hit rate
- Email digest sends successfully; mobile-first CSS verified
- Heartbeat logic in place (triggers only on 3 consecutive zero-job runs)
- `runs` table populated

**Done when**: a real run under both `engineering` and `healthcare` domains produces sensible fit scores that reflect domain-specific criteria (e.g. healthcare ranker correctly penalises a role demanding a different specialism); cache savings visible from second batch onwards; total daily cost for a ~50-job run under £0.10.

### Phase 4 — ATS adapters + run modes
- Greenhouse adapter, configurable via `sources.yaml` (one-line YAML to add a company)
- Lever adapter, same pattern
- Workday adapter (accept slower progress here)
- At least 5 companies configured across the three
- `settings.yaml` `mode` field respected: `paused` produces no output, `passive` produces weekly score-9+ digest only, `active` is default

**Done when**: adding a new company is genuinely a one-line YAML change for Greenhouse/Lever, and `mode: paused` cleanly skips a run.

### Phase 5 — JobSpy integration + domain-specific adapters + aggregators + final polish
- JobSpy adapter wraps the library; configurable per-site in `sources.yaml` (off by default given fragility)
- GOV.UK Find a Job adapter
- HN Who is Hiring scraper (tech/creative)
- Domain-specific adapters (each referenced by its corresponding domain pack):
  - `nhs_jobs` (healthcare)
  - `civil_service` (government)
  - `tes` (education)
  - `caterer` (hospitality)
  - `charityjob` (non-profit, cross-domain)
  - `findaphd` (science/academia)
  - `otta` (creative + tech)
  - `mandy` (creative/film)
- Closing-date detection live (regex + LLM fallback for high-fit jobs), patterns extended from active domain pack
- Cooldown logic for rejected companies (90-day default)
- Stale-row archive (`last_seen` > 14 days → `status = closed`)
- `recover` command rebuilds DB from cached raw responses
- `search` command runs FTS5 queries against historical jobs
- Dashboard "ranker calibration hint" showing high-scored-but-rejected counts
- Documentation: README updated with all features, troubleshooting section, JobSpy fragility caveats, domain pack tutorial, instructions for adding a new domain

**Done when**: the system is the user's primary job-discovery tool across at least 3 distinct domains (engineering, healthcare, creative tested end-to-end); the user can hand the README to someone in a different profession who could run it; adding a new domain is genuinely a one-file YAML change.

---

## 13. Testing

- Unit tests for `util/salary.py` (covering annual, hourly, daily, sessional, AfC bands), `util/dates.py`, `util/domain.py` (pack loading + merging), `pipeline/dedup.py` (pure functions, easy)
- Each domain pack must validate against the Pydantic schema in tests (`tests/test_domain_packs.py`) — covers all 11
- Adapter tests use recorded fixtures in `tests/fixtures/` — no live API calls in CI
- One integration test per domain at minimum: run the full pipeline against fixtures with `engineering`, `healthcare`, and `creative` profiles, assert DB contents differ in sensible ways (e.g. healthcare run includes hourly-salary rows correctly annualised)

Coverage target is not the goal; the goal is "the things most likely to break silently have tests". Salary parsing, dedup hashing, and domain pack schema validation in particular — getting any wrong is invisible until users notice duplicates, miscategorised salaries, or rejected configs.

---

## 14. Open questions / decisions deferred

- **Cover letter generation**: punted to v2 once the core loop is stable
- **Multiple profile personas**: partially addressed via multi-domain profiles (primary + secondary); separate-personality saved searches still v2
- **Web UI for editing config**: explicitly out of scope — read-only HTML dashboard only, all editing via text editor on YAML/JSON files
- **Self-hosting vs cloud**: deferred — the code is identical, only the cron config differs
- **Domain packs beyond UK**: shipped packs assume UK terminology and pay structures (NHS bands, civil service grades, GCSE/A-Level, etc.). International packs (e.g. `healthcare-us`, `legal-au`) would be a sensible v2 expansion; the schema already supports them.

---

## 15. Style and conventions

- Type hints everywhere, `from __future__ import annotations` at the top of every module
- Logging via `logging` module, not `print` (except in CLI user-facing output)
- All HTTP calls go through `util/http.py` — no direct `requests.get` in adapters
- All API calls to Anthropic go through a single wrapper in `pipeline/rank.py` and `profile/parse_cv.py` — these are the only two places that touch the model
- Adapters never write to disk directly; they yield records, the pipeline writes

### 15.1 Token discipline rules

These apply to every Anthropic API call site:

- **Model selection comes from `settings.yaml`**, never hard-coded. Adding a new operation requires adding an entry to `settings.yaml:models`.
- **Every call MUST go through `util/quota.py:log_api_call`** — quota tracking is not optional. The wrapper should make it impossible to call the API without logging.
- **JDs sent for ranking MUST be passed through `pipeline/jd_clean.py` first** — no raw HTML, no untrimmed text reaches the model.
- **`cache_control` MUST be applied to static blocks** (system prompt, profile JSON) for any operation called more than once per run.
- **Output JSON uses short keys** (`s`, `c`, `r`, `k`) internally; mapped to long names only at the JobRecord boundary.
- **Before adding an LLM call, check if regex can do it.** Closing-date extraction is the model for this: regex first, LLM fallback only when regex fails AND the call is justified by job score.
- **`max_tokens` is always set** explicitly per operation, never left to default.
