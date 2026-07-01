# Engineering Review — find_a_job_automator

Full audit of the pipeline (code, efficiency, matching quality, reusability), July 2026.
Findings are ordered by priority within each section. Severity scale: **Critical / High / Medium / Low**.

---

## 1. Code audit — bugs and silent failures

### 1.1 CRITICAL — LLM scores are never persisted for existing jobs, yet every job is re-ranked every run
- **Where:** `job_search/cli.py:386-399` (rank → sync), `job_search/pipeline/dedup.py:89-126`
- **What:** `rank_jobs()` is called on *all* filtered records each run (with `pre_score_threshold: 0.0`, every one goes to the LLM). But `sync_job()` only writes `fit_score`/`fit_reason` on the **insert** path. Both the `updated_jd` and `updated_meta` paths discard the freshly-paid-for scores.
- **Effect:** Every still-listed job is re-scored with Sonnet on every run (3×/day), and 100% of those results are thrown away except for brand-new rows. This is simultaneously the biggest cost bug and a correctness bug: a job whose JD changes never gets its score updated.
- **Fix:** Before ranking, query the DB for `job_id → (jd_content_hash, ranker_version)` and only send to the LLM jobs that are (a) new, (b) have a changed `jd_content_hash`, or (c) have a stale `ranker_version`. Persist scores on the `updated_jd` path.

### 1.2 CRITICAL — a transient API failure permanently pins junk scores on new jobs
- **Where:** `job_search/pipeline/rank.py:311-330`
- **What:** LLM batch calls have **no retry/backoff** (the `tenacity` wrapper in `util/http.py` doesn't cover Anthropic calls). On a 429/overloaded error the batch is logged and skipped; the jobs keep their keyword pre-score (~0–2, see 1.4) and confidence 0.3. New jobs are then **inserted with that junk score**, and because of 1.1 they are never re-scored.
- **Effect:** Good matches silently land in the workbook as "P4 Low" forever after one bad API window.
- **Fix:** Add retry with exponential backoff on `RateLimitError`/`APIStatusError`/5xx (the SDK's built-in `max_retries` is the one-line version), and implement the already-designed `--rerank-stale` path (see 1.3) as a safety net.

### 1.3 HIGH — `--rerank-stale` is accepted but does nothing
- **Where:** `job_search/cli.py:195-199` — the flag is parsed and never referenced in the function body. PROJECT.md (§"In DB, JD changed") documents re-rank-if-stale behaviour that was never implemented.
- **Fix:** Implement: select rows where `ranker_version != current` (the version hash already exists at `rank.py:262-264`), rebuild `JobRecord`s, re-rank, persist.

### 1.4 HIGH — keyword pre-score is noise: substring matching + over-normalisation
- **Where:** `job_search/pipeline/rank.py:91-105`
- **What (a):** skills are matched with `skill in text` (substring). With this profile, core skill `"C"` matches **every job in existence**, and `"Git"` matches "di**git**al". Verified empirically: a florist job in Hull scores 0.57 with matched keywords `['git', 'c']`.
- **What (b):** the score is normalised against *all* skills matched (`len(core)*3 + len(adjacent)`, = 105 for this profile), so even a perfect FPGA-graduate job scores ~1.8/10 (verified against a live Optiver Graduate FPGA Engineer listing). If `pre_score_threshold` were ever raised to the code default of 3.0 (`rank.py:261`), **every job including perfect matches would silently skip LLM ranking**.
- **What (c):** exclusion penalties at `rank.py:108-116` use substring matching, while `filter.py:49-62` correctly uses word boundaries — `"hr"` in title_excludes penalises "T**hr**ee Bridges", `"sales"` penalises "Salesforce Engineer".
- **Fix:** match with `\b`-bounded regexes (reuse `_build_title_exclude_pattern`), and normalise against a realistic cap (e.g. `min(raw_score, 30) / 30 * 10` or "5 core-skill hits = 10").

### 1.5 HIGH — remote/distance filter logic is inverted
- **Where:** `job_search/pipeline/filter.py:128-134`
- **What:** the distance check runs only when `not remote_ok`. Consequences:
  - `remote_ok: true` (this profile): **no distance filtering at all** — an on-site job in Aberdeen, or indeed San Francisco, passes even though `search_radius_miles: 60` and home is Sheffield.
  - `remote_ok: false`: remote jobs are *not* excluded (the condition skips them), which is the opposite of what the flag says.
- **Effect:** combined with ATS adapters fetching worldwide jobs (1.6), hundreds of US/EU jobs pass filtering and get Sonnet-ranked each run.
- **Fix:** restructure: if the job is remote → keep iff `remote_ok`; if on-site with coords → apply radius check always; if on-site without coords → keep (benefit of the doubt) or make it configurable.

### 1.6 HIGH — ATS adapters ignore queries and location entirely
- **Where:** `job_search/adapters/greenhouse.py:25-50`, `lever.py:25-50`, `workable_adapter.py:12-30`, `workday.py:47-91` (`searchText: ""`)
- **What:** all four fetch *every* posting from *every* configured company, worldwide (Waymo, Cohere, NVIDIA, Microsoft, Qualcomm…). Nothing filters by country before ranking (see 1.5).
- **Fix:** cheap post-fetch guard: drop records whose location fails a UK allowlist/regex (make the country configurable), before `apply_filters`. For Workday, pass a search term or a location facet per company.

### 1.7 HIGH — Workday API URL derivation breaks for `/en-US/` career URLs
- **Where:** `job_search/adapters/workday.py:22-39`; `config/sources.yaml:73-115`
- **What:** `_derive_api_url` keeps the locale segment, producing `/wday/cxs/{tenant}/en-US/{site}/jobs`. The CXS endpoint is `/wday/cxs/{tenant}/{site}/jobs` (no locale). ~30 of the 40 configured Workday companies (all universities, Rolls-Royce, BAE, IBM, NVIDIA…) use `/en-US/` URLs and will 404 on every run — each failure is only a `logger.warning`, so **the majority of the Workday source list has likely never returned a job** and nobody would know.
- **Also:** `workday.py:97` — `raw.get("bulletFields", [{}])[0]` raises `IndexError` when `bulletFields` is present but empty (kills that record's normalisation).
- **Also:** the CXS *list* endpoint doesn't return `jobDescription`; `workday.py:114` maps a field that is essentially always missing, so Workday jobs are ranked on **title alone** with an empty JD.
- **Fix:** strip a leading `^[a-z]{2}-[A-Z]{2}/` from the site path; guard the `bulletFields` access; fetch the job detail endpoint (`/wday/cxs/{tenant}/{site}/job/{externalPath}`) for descriptions, or at least mark descriptions as absent so the ranker prompt can say so.

### 1.8 HIGH — enabled sources that don't exist / stubs advertised as working
- **Where:** `config/sources.yaml:8-9,19-20` enables `gov_uk_find_a_job` and `hn_hiring`; `job_search/cli.py:311-340` never registers them; both adapters (`adapters/gov_uk.py`, `adapters/hn_hiring.py`) and all eight `adapters/domain/*` adapters just `raise NotImplementedError`. `job-search recover` is also a stub (`cli.py:659-662`).
- **Effect:** config says enabled → silently nothing happens. A healthcare-domain user would reasonably expect `nhs_jobs` to work (the domain pack references it).
- **Fix:** either implement, or remove from `sources.yaml`/domain packs and have `run` warn loudly when config enables an unregistered adapter.

### 1.9 MEDIUM — `secondary_domains` is ignored by the pipeline
- **Where:** `job_search/cli.py:274` calls `load_pack(domain_name)` directly; the fully-implemented and tested merge logic `get_active_domain()` (`util/domain.py:205-223`) is **never called anywhere**.
- **Effect:** this profile's `"secondary_domains": ["science"]` does nothing — PhD/research-specific ranker context from the science pack never reaches the prompt.
- **Fix:** one-line change to `get_active_domain(profile)`.

### 1.10 MEDIUM — LLM batch scores are applied positionally with no identity check
- **Where:** `job_search/pipeline/rank.py:224-238` (`zip(records, scores)`); `config/ranker.yaml` never instructs the model to preserve input order or echo an id.
- **Effect:** if the model reorders (or merges duplicates), scores are silently attached to the wrong jobs. The `len(scores) == len(batch)` check catches count mismatches only.
- **Fix:** include an index `"i"` per job in `jobs_json`, require it in the output schema, and match on it.

### 1.11 MEDIUM — Greenhouse descriptions are HTML-entity-escaped
- **Where:** `job_search/adapters/greenhouse.py:57-61` → `pipeline/jd_clean.py:42-48`
- **What:** the Greenhouse boards API returns `content` HTML-escaped (`&lt;p&gt;…`). BeautifulSoup then extracts *the literal tags as text*, so JDs sent to the LLM are full of `<p>`, `<li>`, `&amp;` noise — wasted tokens and degraded closing-date/salary regexes.
- **Fix:** `html.unescape(content)` before `clean_jd`.

### 1.12 MEDIUM — transient geocode failures are cached as permanent misses
- **Where:** `job_search/util/geocode.py:67-71` — on *any* exception (network blip, 429 from Nominatim) it writes `"null"` to a cache that "never expires".
- **Effect:** locations permanently lose coordinates → distance filtering (once fixed, see 1.5) silently stops applying to those jobs.
- **Fix:** only cache negative results for HTTP-200-empty responses; don't cache exceptions (or add a TTL for negatives). Also: this module bypasses `util/http.py` despite that module's "ALL HTTP calls" invariant.

### 1.13 MEDIUM — HTTP retry never retries the failures that matter
- **Where:** `job_search/util/http.py:33-39`
- **What:** retries only `ConnectionError`/`Timeout`. `raise_for_status()` raises `HTTPError`, so 429s and 5xxs from Adzuna/Reed/ATS endpoints are **never retried** — the adapter's per-query `except` swallows them and that query's results are silently lost for the run.
- **Fix:** retry on `HTTPError` when `status_code in (429, 500, 502, 503, 504)`, honouring `Retry-After`.

### 1.14 MEDIUM — `_parse_date` never tries its own format list
- **Where:** `job_search/pipeline/normalise.py:33-42` — the loop iterates four formats but the body always parses `raw[:10]` with `"%Y-%m-%d"`. `"%d/%m/%Y"` dates (and Lever's epoch-millis `createdAt`, see `lever.py:81`) parse to `None`.
- **Effect:** `posted_date` silently lost → the `max_days_since_posted` filter never applies to those sources, and the digest's "first seen today" logic is the only freshness signal left.
- **Fix:** actually use `fmt` in `strptime`; handle epoch millis for Lever.

### 1.15 MEDIUM — cost soft cap exists only in documentation
- **Where:** `config/settings.yaml:35` (`quota_soft_cap_gbp: 5.00`); README FAQ ("logs a warning if the daily soft cap is exceeded (default: £2.00)"); PROJECT.md §quota.
- **What:** nothing reads `quota_soft_cap_gbp`. `today_total_gbp()` exists (`util/quota.py:156`) and is only used for *display* in the dashboard/email. There is no warning, no stop.
- **Fix:** check in `api_call_wrapper()` (it's the designed choke point): warn at the cap, hard-stop LLM ranking at e.g. 2× the cap, leaving keyword scores.
- **Related dead config:** `heartbeat:` (send_heartbeat exists, never called), `backups.keep_days` (no pruning code — backups accumulate forever, see 2.5).

### 1.16 LOW — assorted
- `cli.py:419` — `duration_s` is always written as `0`; the runs sheet's duration column is meaningless.
- `cli.py:453` — email digest only sends in `active` mode *and* `ranked` non-empty; a run that only closed stale jobs sends nothing (probably fine, worth documenting).
- `util/salary.py:129-137` — "Band 9" maps to `_AFC_BANDS[9]` which the comment says is 8b; the comment map says 9 → 11. Inconsistent AfC handling.
- `jd_clean.py:28` — the "we offer…" boilerplate regex deletes up to 500 chars mid-JD (DOTALL), which can eat real requirements that follow a benefits sentence.
- `cli.py` health command (`cli.py:598`) omits workday/workable/jobspy, and the base `healthcheck()` does a real full fetch (`base.py:74-80`) — expensive for ATS adapters.
- `datetime.utcnow()` (deprecated in 3.12) used in `cli.py`, `quota.py`, `workbook_import.py`.
- `filter.py:124` — cooldown check is one SQL query per record (N+1); trivial to hoist into a set.

---

## 2. Efficiency review

### 2.1 CRITICAL — redundant LLM spend (same as finding 1.1)
Re-ranking everything, every run, and discarding the results is ~90%+ of API cost. With ~40 ATS companies (worldwide listings), Adzuna at 100 queries × 50 results, and jobspy at 100 × 10 × 2 sites, a full run can send several thousand JDs to Sonnet 3×/day. Skipping unchanged `jd_content_hash` rows makes daily marginal cost ≈ new jobs only. This is the design PROJECT.md already specifies; it just isn't implemented.

### 2.2 HIGH — Sonnet where Haiku is specified
`config/settings.yaml:61-76` uses `claude-sonnet-4-6` for both `rank` and `queries`. PROJECT.md:733-736 (the design) says Haiku for both. Ranking 3k-char JDs against a rubric is exactly the Haiku-class task the two-pass design assumed; Sonnet multiplies cost ~4× for little gain here. Keep Sonnet for `parse_cv` (runs once).

### 2.3 HIGH — Reed detail fetch: one HTTP round-trip + 1s polite delay per job
`reed.py:76-83` fetches the detail endpoint for every result inside the search loop; with `util/http.py:49`'s unconditional `time.sleep(1.0)` that's ~1.2s × (queries × results) — hours of wall clock in Actions, which matches the observed 120-minute timeouts (commit history: `fa2e91c`, `73651bb`). Fixes, in order of value: only fetch details for jobs that survive `apply_filters`; make the polite delay per-host (it currently sleeps globally, even between different hosts — the docstring at `http.py:21` already claims per-host); parallelise per-host with a small thread pool.

### 2.4 HIGH — query explosion multiplies board API calls
`max_queries: 100` (`settings.yaml:76`) × Adzuna pagination (50/query) alone approaches Adzuna's ~250-call/day free tier by itself, three times a day. The deterministic generator also produces near-duplicates ("FPGA Engineer", "junior FPGA Engineer", "graduate FPGA Engineer", "FPGA Sheffield"…) that return overlapping result sets which are then all normalised, geocoded, filtered, and ranked. 30–40 well-chosen queries measurably covers the same space; per-source `max_queries` would help (boards need many; ATS need none).

### 2.5 MEDIUM — unbounded growth: backups, git data branch, workbook
- `backups.keep_days: 7` is never enforced; `data/backups/` gains a dated `.db` + `.xlsx` every run (dedup suffixes `.1`, `.2` for same-day runs), and the whole `data/` dir is committed to the `job-search-data` branch **every run, 3×/day** (`daily_run.yml:97-139`). Git keeps every historical binary blob; the branch's history grows without bound. Fix: prune backups by `keep_days`, exclude `data/backups/` from the branch commit, and periodically squash the data branch (or push with `--force` to keep single-commit history).
- `workbook_export.py:786-806` exports the entire jobs table with per-cell styling; at a few thousand rows this is fine, at 50k rows openpyxl will take minutes and produce a file Excel struggles with. Export only non-closed rows (or last N days) to the workbook; the DB remains the full archive.
- `today_total_gbp()` (`quota.py:156-177`) re-reads the whole quota.jsonl per call — fine now, slow at 100k lines; the `api_calls` table already exists, query it instead.

### 2.6 MEDIUM — Actions minutes
Three schedules/day × (currently) up to 120 min = a 2,000-min/month free tier can be exhausted in under a week when sources hang. After 2.1–2.4 a run should take <15 min; also consider dropping to 1–2 schedules/day — job boards don't refresh fast enough to justify three Sonnet passes daily.

### 2.7 LOW — prompt size
The system prompt embeds the full profile JSON (~6 KB — this profile's 130+ target_roles/skills). Prompt caching mitigates within a run, but trimming the profile sent to the ranker (drop `filters`, `location.lat/lon`, education grades — things the rubric doesn't use, or that filtering already handled) cuts every cache-miss. `parse_cv` at 15k chars input with `max_tokens 4096` is fine.

---

## 3. Test coverage

- **The suite doesn't even collect**: `tests/test_filter.py:142` has an `IndentationError` — `pytest` exits with a collection error. Any CI would be red, but **there is no CI workflow that runs tests at all** (only `daily_run`, `configure_profile`, `pages`). → Add a `test.yml` on push/PR: `pytest` + `ruff`.
- **Stale skips hide implemented code**: `tests/test_salary.py` (all 8 tests) and `tests/test_dedup.py` sync tests are `@pytest.mark.skip(reason="not yet implemented (Phase 2)")` — but `parse_salary` and `sync_job` *are* implemented. The salary tests are fully written and would run today; the dedup sync tests are empty stubs. `tests/test_adapters_adzuna.py` normalise test likewise skipped.
- **One genuine failure**: `test_workbook_export.py::test_workbook_export_adds_readable_tracking_columns` asserts the old column layout — the export gained `Action`/`Experience`/`Days Left` columns and the test was never updated.
- **Untested critical paths**: `apply_filters` distance/remote branch (would have caught 1.5), `sync_job` update paths (would have caught 1.1), `keyword_prescore` (would have caught 1.4), `_call_llm_batch` JSON handling/order (1.10), `normalise._parse_date` (1.14), Workday URL derivation (1.7), `import_user_edits` round-trip, quota cap behaviour (1.15).

---

## 4. Ranker prompt review (`config/ranker.yaml`)

- **No output-order guarantee** — the schema demands an array but never says "in the same order as the input, one object per job, echo the job index". Combined with positional `zip()` (1.10) this is the highest-risk ambiguity. Add an `"i"` field.
- **Engineering bias baked into the generic prompt** — the few-shot example (`ranker.yaml:57`) is "Strong FPGA match but requires SC clearance", and the rubric's exemplar excludes are "Senior", "SC clearance". For any non-engineering fork these examples anchor the model toward tech-shaped reasoning. Move domain-specific examples into the domain packs' `ranker_context` and keep `ranker.yaml` neutral.
- **`"c"` (confidence) has no rubric** — no definition of what 0.3 vs 0.8 means, so values are vibes and not comparable across batches. Define it (e.g. "how completely the JD states its requirements") or drop the field — nothing downstream uses it except display.
- **Redundant/conflicting penalty instructions** — the rubric says to penalise heavily for `title_excludes` terms and blocked companies, but `filter.py` has already hard-dropped those jobs before the LLM sees them. Harmless today, but it means the rubric and the filter can drift apart silently; note in the rubric that hard exclusions happen upstream.
- **No empty-JD instruction** — Workday jobs arrive with empty descriptions (1.7). The rubric never says what to do with missing evidence; models tend to score title-plausible jobs 6–7. Add: "If the JD is empty or trivially short, cap the score at 5 and set confidence ≤ 0.3."
- **Double-counting salary/location** — "modest credit" for salary above floor and location in radius rewards attributes that filtering already guarantees, compressing the useful score range. Minor; consider removing.
- **Version hash gap** — `_prompt_content_hash` (`rank.py:59-70`) covers `system`/`rubric`/`domain` but not `user_prompt_template`, so editing the user template doesn't bump `ranker_version`.

---

## 5. New job matching (profile: graduate electronics/embedded/FPGA, Sheffield, remote OK)

Live scraping is blocked from this review sandbox (job-board hosts 403 via proxy), and no `ANTHROPIC_API_KEY` is available, so this was done with: (a) web search for current UK listings matching `target_roles.core`, and (b) the pipeline's own pass-1 scorer + title filter run on those real listings. Roles that should rank at the top for this profile, all live as of July 2026:

| Role | Employer / where | Why it fits the CV |
|---|---|---|
| Graduate FPGA Engineer | Optiver, London ([listing](https://optiver.com/working-at-optiver/career-opportunities/8057449002/)) | Verilog/SystemVerilog + DSP + digital design — direct hit on dissertation (Vivado/Verilog AI accelerator) |
| Graduate Engineer — Machine Learning Hardware Design | Arm, Cambridge ([listing](https://careers.arm.com/job/cambridge/graduate-engineer-machine-learning-hardware-design/33099/95943960480)) | ML accelerator RTL design; the CV's FPGA AI-accelerator project is the exact profile; Arm also hires this stream in **Sheffield/Manchester** |
| Graduate SOC Validation Engineer | Arm, Cambridge ([listing](https://careers.arm.com/job/cambridge/graduate-soc-validation-engineer/33099/89565735648)) | FPGA prototyping + C/Python — matches hardware-software interface strength |
| Graduate Electronics Engineer | Leonardo UK, Edinburgh/Newcastle/Bristol ([listing](https://careers.uk.leonardo.com/gb/en/job/R0022688/Graduate-Electronics-Engineer), [FPGA stream](https://careers.uk.leonardo.com/gb/en/fpga-career-opportunities)) | Mixed-signal + FPGA graduate programme, £34k — matches profile aerospace/defence core roles |
| Graduate Embedded Software Engineer | Innovative Technology, Oldham (via [Milkround](https://www.milkround.com/jobs/embedded-software-engineer)) | Embedded C/microcontrollers within ~35 mi of Sheffield |
| Junior Embedded Software Engineer | Sheffield manufacturing sector (via [Totaljobs](https://www.totaljobs.com/jobs/junior-embedded-software-engineer/in-sheffield), [Reed](https://www.reed.co.uk/jobs/embedded-engineer-jobs-in-sheffield)) | STM32/RTOS — home city |
| Funded PhD: FPGA acceleration of physics workloads | University of Glasgow ([projects](https://www.gla.ac.uk/schools/computing/postgraduateresearch/prospectivestudents/phd-projects/)) | Matches `PhD Studentship FPGA` core role; 3.5-yr funded |
| EPSRC OpenFPGA studentship (Oct 2026) | Newcastle (Rahman/Shafik/Yakovlev) (via [scholarshipdb](https://scholarshipdb.net/fpga-phd-scholarships-in-uk-l?r_q=vhdl)) | Matches `PhD Studentship FPGA`/`Digital Systems` |
| ~10 further FPGA PhDs | [FindAPhD fpga search](https://www.findaphd.com/phds/?Keywords=fpga), [embedded systems (80 listed)](https://www.findaphd.com/phds/?Keywords=embedded+system) | The `findaphd` adapter is a stub (1.8) — this whole channel is currently missed |

**Sanity-check of the pipeline's own scoring against these:** the title filter behaves correctly (word-boundary excludes dropped "Senior FPGA Engineer" and "Account Manager — Electronics" controls, kept all good matches). The keyword pre-score, however, is unusable as a signal (finding 1.4): the best real match (Optiver) pre-scores 1.81/10 while a florist control scores 0.57 from false-positive `c`/`git` substring hits. Final ranking quality therefore rests entirely on the LLM pass — which makes findings 1.2 (no retry → junk scores persisted) and 1.7 (Workday JDs empty) the main real-world misranking risks.

**Profile tuning notes found while cross-checking:**
- `description_excludes` contains `"must have right to work"` — many *good* UK graduate listings include right-to-work boilerplate; this penalises them in the LLM rubric. Remove or narrow to visa-sponsorship-refused phrasing.
- `title_excludes` contains `"lead"` — fine (word-boundary), but note it also blocks "Tech Lead Graduate Scheme"-style titles; acceptable.
- FindAPhD/jobs.ac.uk are the highest-density sources for ~40% of this profile's core roles (PhD/EngD/RA) and neither is implemented — the university Workday boards were presumably added to compensate, but most of them 404 (finding 1.7). Fixing 1.7 or implementing `findaphd` would materially improve recall for this profile.

---

## 6. Personalisation / reusability

The config-first architecture (profile.json + sources.yaml + domain packs + Issue Form) is genuinely close to self-serve. Gaps, in priority order:

1. **The Issue Form flow doesn't geocode** (`profile/issue_profile.py:122-142`): `location.lat/lon` stay `null`, so distance filtering (once 1.5 is fixed) silently never applies for Issue-Form users. `parse_cv` has the same gap (its schema at `parse_cv.py:42` tells the model `lat: null`). Fix: call `util/geocode.geocode(city)` when writing the profile.
2. **Junior-candidate assumptions are hardcoded for everyone**:
   - `parse_cv.py:56-59` — the CV-parse schema *defaults* `title_excludes` to `["Senior","Staff","Principal","Lead","Head of","Director"]` and `requires_years_above: 3`. A senior engineer forking this gets a profile that excludes their own level. Make the model infer seniority from the CV, or leave empty and let the Issue Form set it.
   - `profile/queries.py:21,73-79` — `_JUNIOR_MODIFIERS` ("junior", "graduate", …) are appended to every core role in fallback queries, unconditionally. Should derive from profile seniority.
   - `queries.py:99` — skill queries are templated as `f"{skill} engineer"` — wrong for nurses, lawyers, chefs (the domain packs exist precisely to avoid this). Use a domain-pack query suffix.
3. **The author's sources ship as the default** — `config/sources.yaml` is ~90 engineering/AI/defence/university boards, and `config/profile.json` in the repo root is the author's actual CV data. A fork inherits both. Ship `sources.example.yaml` + `profile.example.json`, gitignore the real ones (like `.env`), and have the Issue-Form workflow write them. (This also stops personal data landing in every fork's history.)
4. **UK is hardcoded below the config layer** despite the README's "Can I use this outside the UK?" claim: `geocode.py:57-61` (`q=f"{location}, UK"`, `countrycodes: gb`), `adzuna.py:22` (`/jobs/gb/`), `reed.py` is UK-only, `filter._REMOTE_TOKENS`, salary parsing is GBP-only. A `country:` key in settings.yaml threaded through these would make the FAQ true.
5. **Issue Form gaps**: no `secondary_domains` field (and the field does nothing anyway, 1.9); no `max_days_since_posted`; free-text Domain input (`job_search_profile.yml:18-25`) should be a `dropdown` (typos → `load_pack` FileNotFoundError → silent fallback to empty domain context at `cli.py:275-277`); Reed's hardcoded `fullTime: True` (`reed.py:57`) excludes part-time seekers with no config escape.
6. **README drift**: says runs daily 07:00 (workflow runs 3×/day); says soft cap "default £2.00" (settings say 5.00, code enforces nothing); advertises LinkedIn scraping (removed from jobspy sites); Step 5 says artifacts (results actually land on the `job-search-data` branch — the download table above it is right); `jobspy:` block duplicated between settings.yaml:87 and sources.yaml:11 with different `results_wanted_per_query` (25 vs 10 — sources.yaml wins, confusing to edit).

---

## 7. Quick wins vs bigger refactors

**Quick wins (hours, huge payoff):**
1. Skip LLM ranking for unchanged `jd_content_hash` rows + persist scores on update (1.1) — cuts API cost ~90% and fixes stale scores.
2. Switch `rank`/`queries` models to Haiku in settings.yaml (2.2).
3. Add SDK retries (`max_retries`) to Anthropic calls (1.2).
4. Fix the Workday locale-segment bug — one regex (1.7) — and `html.unescape` for Greenhouse (1.11).
5. Fix `tests/test_filter.py:142` indentation, un-skip the salary tests, fix the workbook-export test, add a CI test workflow (§3).
6. Word-boundary + saner normalisation in `keyword_prescore` (1.4).
7. Use `get_active_domain()` in `cli.run` (1.9).
8. Enforce `quota_soft_cap_gbp` in `api_call_wrapper` (1.15); prune backups per `keep_days`.
9. Geocode the city in the Issue-Form/parse-cv path (6.1).
10. Retry 429/5xx in `util/http.py` (1.13).

**Bigger refactors (days):**
1. **Incremental ranking pipeline** — a proper "what needs (re)scoring" stage between dedup and rank, plus an implemented `--rerank-stale`; make sync the source of truth for score persistence on all paths.
2. **Location model** — fix the remote/distance semantics (1.5), add a country layer so ATS worldwide listings are cheap-filtered pre-LLM (1.6), and de-hardcode UK for international forks (6.4).
3. **Adapter honesty pass** — implement or delete the eight stub adapters (findaphd first for this profile), register-or-warn for enabled-but-unknown sources, per-host politeness + parallel fetching, Reed detail fetch after filtering (2.3).
4. **Template-repo packaging** — example configs, gitignored personal files, Issue-Form dropdowns, README sync (6.3/6.5/6.6); makes the fork flow genuinely zero-edit.
