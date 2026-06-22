# UK Job Search Automator

A daily pipeline that scrapes UK job boards and company career pages, ranks every result against your CV using Claude, and delivers a scored Excel workbook of matches. Set it up once; it runs automatically every day on GitHub — no server required.

> **Want to use this yourself?** Jump straight to [Setup for your own use](#setup-for-your-own-use).

---


---

## 📥 Download your results

> These files are updated automatically every day at 07:00 UTC.

| File | Description | Link |
|---|---|---|
| **jobs.xlsx** | Scored job matches — open in Excel or Google Sheets | [⬇️ Download](https://github.com/dcracknell/find_a_job_automator/raw/job-search-data/data/jobs.xlsx) |
| **dashboard.html** | Visual summary of scores and sources | [🔗 View](https://github.com/dcracknell/find_a_job_automator/blob/job-search-data/data/dashboard.html) |
| **jobs.db** | SQLite database (advanced use) | [⬇️ Download](https://github.com/dcracknell/find_a_job_automator/raw/job-search-data/data/jobs.db) |

> **jobs.xlsx not updating?** Check [Actions](https://github.com/dcracknell/find_a_job_automator/actions) to see if the latest run succeeded.

---

## How it works

1. **Parses your CV** into a structured profile (`config/profile.json`) using Claude
2. **Generates search queries** tailored to your skills and target roles
3. **Scrapes** Indeed, LinkedIn, Google Jobs, Adzuna, Reed, and 40+ company career pages
4. **Scores every job** against your CV — a 0–10 fit score with a one-line reason
5. **Saves results** to a deduplicated Excel workbook you can filter, sort, and annotate
6. **Emails a daily digest** of new high-scoring jobs (optional)

---

## Setup for your own use

You don't need to write any code. The whole setup happens through GitHub's web interface.

### What you'll need

| Requirement | Cost | Notes |
|---|---|---|
| GitHub account | Free | The pipeline runs on GitHub Actions |
| Anthropic API key | ~£0.10–0.50/day | Powers CV parsing and job ranking. Get one at [console.anthropic.com](https://console.anthropic.com/) |
| Adzuna API key | Free | [developer.adzuna.com](https://developer.adzuna.com/) — takes 2 minutes to register |
| Reed API key | Free | [reed.co.uk/developers/jobseeker](https://www.reed.co.uk/developers/jobseeker) |
| Email (optional) | Free | Gmail works. Only needed if you want daily email digests |

You can run with **just the Anthropic key** to start — Adzuna and Reed auto-enable when their keys are added.

---

### Step 1 — Fork the repository

Click **Fork** at the top right of this page. Keep it **private** if your CV or job notes are sensitive (recommended).

---

### Step 2 — Add your API keys

In your forked repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/) |
| `ADZUNA_APP_ID` | [developer.adzuna.com](https://developer.adzuna.com/) |
| `ADZUNA_APP_KEY` | Same as above |
| `REED_API_KEY` | [reed.co.uk/developers/jobseeker](https://www.reed.co.uk/developers/jobseeker) |

**Optional — for email digests:**

| Secret name | Example value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_USER` | `you@gmail.com` |
| `SMTP_PASS` | Your Gmail app password ([how to create one](https://support.google.com/accounts/answer/185833)) |
| `SMTP_FROM` | `you@gmail.com` |
| `SMTP_TO` | `you@gmail.com` |

---

### Step 3 — Set up your profile

Go to **Issues → New issue** and choose **Job search profile setup**. Fill in the form with your CV text, target roles, skills, location, and salary. Submitting it automatically generates your `config/profile.json` and kicks off the first run.

You can re-submit this issue any time to update your profile (e.g. new skills, different roles, changed location).

---

### Step 4 — Enable email digests (optional)

Open `config/settings.yaml` in GitHub's web editor and change:

```yaml
mode: passive   # change this to: active
```

With `mode: active` and SMTP secrets added, you'll get a daily email of new high-fit jobs.

---

### Step 5 — Get your results

After the first run completes (check **Actions** to see progress), download your results from the workflow run's **Artifacts** section:

- `jobs.xlsx` — the main workbook. Use the **Status** column to track applications, and **Notes** for follow-ups
- `dashboard.html` — visual summary of scores and sources
- `jobs.db` — SQLite database if you want to query it directly

The pipeline runs automatically at **07:00 UTC every day**. Results accumulate — previously seen jobs are deduplicated, and your status/notes are never overwritten.

---

## Customising your profile

Your profile lives in `config/profile.json`. You can edit it directly in GitHub's web editor at any time — no code knowledge needed, just edit the file and commit.

### target_roles

What job titles to search for. Split into three tiers — `core` (best matches, searched first), `adjacent` (related roles), and `stretch` (long shots worth catching).

```json
"target_roles": {
  "core": ["Graduate Hardware Engineer", "Graduate FPGA Engineer"],
  "adjacent": ["Firmware Engineer", "Digital Design Engineer"],
  "stretch": ["Software Developer", "Test Engineer"]
}
```

### core_skills and adjacent_skills

Skills from your CV. `core_skills` are things you're confident in; `adjacent_skills` are things you've touched but aren't your main selling point. These drive both search queries and job scoring.

### filters

Hard limits — jobs outside these are discarded before any scoring:

```json
"filters": {
  "salary_floor_gbp": 25000,
  "max_days_since_posted": 30
}
```

### negative_signals

Words in job titles or descriptions that should disqualify a role. `"senior"` in `title_excludes` means no senior roles will score well; `"5+ years"` in `description_excludes` filters out roles requiring too much experience.

### search_radius_miles and remote_ok

Controls location filtering. Set `remote_ok: true` to include fully remote roles anywhere in the UK regardless of distance.

---

## Customising which companies are searched

Open `config/sources.yaml` in GitHub's web editor. You can add companies that use Greenhouse, Lever, Workday, or Workable ATS systems — scraped directly from career pages, not just what appears on public job boards.

To add a company, find which ATS they use (usually visible in their careers page URL) and add an entry:

```yaml
greenhouse:
  companies:
    - {name: Your Company, slug: yourcompanyslug}

workday:
  companies:
    - {name: Your Company, url: "https://yourcompany.wd1.myworkdayjobs.com/careers"}
```

---

## Running locally (optional)

If you prefer to run on your own machine instead of GitHub Actions:

```bash
# 1. Clone your fork
git clone https://github.com/YOUR_USERNAME/find_a_job_automator
cd find_a_job_automator

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

# 3. Install
pip install -e .

# 4. Add your secrets
cp .env.example .env
# Edit .env and fill in your API keys

# 5. Parse your CV to generate profile.json
job-search parse-cv path/to/your-cv.pdf --domain engineering

# 6. Run the pipeline
job-search run
```

Results land in `data/jobs.xlsx`.

**Useful commands:**

```
job-search run              # full pipeline run
job-search run --dry-run    # fetch and score jobs, but don't save anything
job-search export           # regenerate Excel from the database without fetching new jobs
job-search search "FPGA"    # full-text search over all stored jobs
job-search health           # check all configured sources are reachable
job-search domains          # list available domain packs
```

---

## Domain packs

Domain packs tune the pipeline for different professions — adjusting seniority detection, closing date patterns, and ranker hints. Available packs:

`engineering` · `healthcare` · `finance` · `legal` · `government` · `education` · `science` · `creative` · `hospitality` · `trades` · `general`

To use a pack, set `"domain": "engineering"` in `config/profile.json`. When parsing a CV locally, pass `--domain engineering` to `parse-cv`.

To create a new pack for an unlisted profession, add a YAML file to `config/domains/` — no code changes needed.

---

## Frequently asked questions

**How much does it cost to run?**  
A typical daily run costs around £0.10–0.30 in Anthropic API credits. The pipeline tracks spend in `data/quota.jsonl` and logs a warning if the daily soft cap is exceeded (default: £2.00, set in `settings.yaml`).

**Will it overwrite my notes or application status in the spreadsheet?**  
No. The `Status` and `Notes` columns are user-owned. The pipeline imports your changes before each run and never overwrites them.

**The pipeline ran but I got no results — what's wrong?**  
Check the run log in **Actions**. Common causes: no API keys set (Adzuna/Reed both disabled), `profile.json` not yet generated (submit the Issue Form first), or `pre_score_threshold` in `ranker.yaml` is too high.

**Can I use this outside the UK?**  
JobSpy (Indeed, LinkedIn, Google Jobs) works globally. Change `"city"` and coordinates in `profile.json`, remove UK-only sources from `sources.yaml`, and it'll work for most English-speaking markets.

**I want to search for PhD or research roles — is that supported?**  
Yes — add them to `target_roles` in `profile.json` just like any other title (e.g. `"PhD Studentship Electronics"`, `"Research Engineer"`). The ranker scores them against your CV like any other job.

**How do I pause it while I'm on holiday or have accepted a job?**  
Set `mode: paused` in `config/settings.yaml`. The workflow will still trigger daily but skip every run immediately.

---

## Configuration reference

| File | What it controls |
|---|---|
| `config/profile.json` | Your CV data, skills, target roles, location, salary floor, exclusions |
| `config/sources.yaml` | Which job boards and company ATS pages to scrape |
| `config/settings.yaml` | Email, run mode, cost caps, model selection |
| `config/ranker.yaml` | Scoring rubric and LLM prompt — rarely needs changing |
| `config/domains/*.yaml` | Profession-specific tuning packs |

---

## Credits

Built on top of [python-jobspy](https://github.com/Bunsly/JobSpy) for multi-board scraping, inspired by [JobFunnel](https://github.com/PaulMcInnis/JobFunnel) and [ai-job-scraper](https://github.com/BjornMelin/ai-job-scraper).

For developer/AI assistant documentation, see [AI_README.md](AI_README.md).
