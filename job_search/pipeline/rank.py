"""Two-pass job ranker.

Pass 1 — keyword pre-score (free, no API call):
  - Count core_skills matches (weight ×3) and adjacent_skills (weight ×1)
  - Apply negative_signals penalties
  - Jobs below pre_score_threshold skip Pass 2

Pass 2 — LLM rank (Anthropic API, batched, cached):
  - Model from settings.yaml:models.rank
  - System prompt = ranker.yaml template + active domain pack rankehr_context (cache_control)
  - Profile JSON in cached block
  - Up to 5 JDs batched per call
  - Compact short-key output: {"s": ..., "c": ..., "r": ..., "k": [...]}
  - Every call goes through util/quota.py:api_call_wrapper — never call the API directly

IMPORTANT: JDs MUST be passed through pipeline/jd_clean.py before sending to the model.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from functools import lru_cache
from typing import Any

import yaml

from job_search import PROJECT_ROOT
from job_search.adapters.base import JobRecord
from job_search.util.quota import QuotaExceededError, api_call_wrapper
from job_search.util.secrets import looks_configured_secret

logger = logging.getLogger(__name__)

_RANKER_YAML_PATH = PROJECT_ROOT / "config" / "ranker.yaml"
_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"

_ranker_cfg_cache: dict | None = None
_settings_cache: dict | None = None


def _load_ranker() -> dict:
    global _ranker_cfg_cache
    if _ranker_cfg_cache is None:
        with _RANKER_YAML_PATH.open() as f:
            _ranker_cfg_cache = yaml.safe_load(f)
    return _ranker_cfg_cache


def _load_settings() -> dict:
    global _settings_cache
    if _settings_cache is None:
        with _SETTINGS_PATH.open() as f:
            _settings_cache = yaml.safe_load(f)
    return _settings_cache


def _prompt_content_hash(ranker_cfg: dict, domain_context: str) -> str:
    """Stable hash of the ranker prompt (ignoring whitespace/comments)."""
    stable = json.dumps(
        {
            "version": ranker_cfg.get("version", ""),
            "system": ranker_cfg.get("system_prompt_template", ""),
            "user": ranker_cfg.get("user_prompt_template", ""),
            "rubric": ranker_cfg.get("scoring_rubric", ""),
            "domain": domain_context,
        },
        sort_keys=True,
    )
    return hashlib.sha1(stable.encode()).hexdigest()[:16]


def current_ranker_version(domain_context: str = "") -> str:
    """The version tag written to jobs.ranker_version for the active prompt."""
    ranker_cfg = _load_ranker()
    return f"{ranker_cfg.get('version', 'v1')}-{_prompt_content_hash(ranker_cfg, domain_context)}"


# ---------------------------------------------------------------------------
# Pass 1 — keyword pre-score
# ---------------------------------------------------------------------------


# A job matching this many weighted skill points scores 10/10 on the pre-scan.
# (e.g. four core skills + three adjacent = 4*3 + 3*1 = 15). Normalising against
# the FULL skill list would make a perfect job score ~2/10 on a rich profile.
_PRESCORE_FULL_MARKS = 15.0


@lru_cache(maxsize=32)
def _compile_term_patterns(terms: tuple[str, ...]) -> tuple[tuple[str, re.Pattern], ...]:
    """Compile word-boundary-ish patterns for skills/exclude terms.

    Uses lookarounds instead of \\b so terms ending in symbols ("C++", "C#")
    still anchor correctly, and "C" never matches the letter c inside a word.
    """
    compiled = []
    for term in terms:
        term = term.strip()
        if not term:
            continue
        pattern = re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        compiled.append((term, pattern))
    return tuple(compiled)


def keyword_prescore(record: JobRecord, profile: dict) -> float:
    """Compute a keyword-based pre-score (0-10) for a job record. No API call.

    Skills and exclude terms are matched at word boundaries (so "C" doesn't
    match every word containing a c, and "git" doesn't match "digital").
    """
    text = f"{record.title} {record.description}"

    core_skills = tuple(profile.get("core_skills", []))
    adjacent_skills = tuple(profile.get("adjacent_skills", []))
    negative = profile.get("negative_signals", {})
    title_excludes = tuple(negative.get("title_excludes", []))
    desc_excludes = tuple(negative.get("description_excludes", []))

    score = 0.0
    matched: list[str] = []

    for skill, pattern in _compile_term_patterns(core_skills):
        if pattern.search(text):
            score += 3.0
            matched.append(skill)

    for skill, pattern in _compile_term_patterns(adjacent_skills):
        if pattern.search(text):
            score += 1.0
            if len(matched) < 5:
                matched.append(skill)

    if core_skills or adjacent_skills:
        score = min(10.0, score / _PRESCORE_FULL_MARKS * 10.0)
    else:
        score = 5.0  # no skills defined — neutral score

    # Penalties
    for _, pattern in _compile_term_patterns(title_excludes):
        if pattern.search(record.title):
            score = max(0.0, score - 4.0)
            break

    for _, pattern in _compile_term_patterns(desc_excludes):
        if pattern.search(text):
            score = max(0.0, score - 3.0)
            break

    record.matched_keywords = matched[:5]
    return round(score, 2)


# ---------------------------------------------------------------------------
# Pass 2 — LLM ranking
# ---------------------------------------------------------------------------


def _build_system_prompt(ranker_cfg: dict, profile: dict, domain_context: str) -> str:
    template = ranker_cfg.get("system_prompt_template", "")
    rubric = ranker_cfg.get("scoring_rubric", "")
    profile_json = json.dumps(profile, indent=None, separators=(",", ":"))
    if not template:
        # Fallback: build system prompt from scoring_rubric when template is absent
        parts = ["You are an expert job-fit ranker. Output ONLY valid JSON."]
        if rubric:
            parts.append(rubric.strip())
        if domain_context:
            parts.append(domain_context.strip())
        parts.append("Candidate profile: " + profile_json)
        return "\n\n".join(p for p in parts if p)
    return _render_prompt_template(
        template,
        {
            "profile_json": profile_json,
            "scoring_rubric": rubric,
            "domain_context": domain_context,
        },
    )
def _render_prompt_template(template: str, values: dict[str, object]) -> str:
    """Replace only supported prompt placeholders, leaving JSON braces intact."""
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def _call_llm_batch(
    client: Any,
    model: str,
    max_tokens: int,
    system_prompt: str,
    batch: list[JobRecord],
    ranker_cfg: dict,
) -> list[dict]:
    """Call the LLM to rank a batch of up to 5 jobs. Returns list of score dicts."""
    user_template = ranker_cfg.get(
        "user_prompt_template",
        "Rate the following {n} job(s):\n{jobs_json}",
    )
    jobs_data = [
        {"i": idx, "title": r.title, "company": r.company, "jd": r.description[:3000]}
        for idx, r in enumerate(batch)
    ]
    jobs_json = json.dumps(jobs_data, indent=None, separators=(",", ":"))
    user_message = _render_prompt_template(
        user_template,
        {
            "n": len(batch),
            "jobs_json": jobs_json,
        },
    )

    with api_call_wrapper("rank") as rec:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=(
              [
                  {
                      "type": "text",
                      "text": system_prompt,
                      "cache_control": {"type": "ephemeral"},
                  }
              ]
              if system_prompt
              else []
          ),
            messages=[{"role": "user", "content": user_message}],
        )
        rec["model"] = model
        rec["input_tokens"] = response.usage.input_tokens
        rec["cached_input_tokens"] = getattr(response.usage, "cache_read_input_tokens", 0)
        rec["output_tokens"] = response.usage.output_tokens

    raw_text = response.content[0].text.strip()
    # Strip any markdown fences
    if raw_text.startswith("```"):
        parts = raw_text.split("```")
        raw_text = parts[1] if len(parts) > 1 else raw_text
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            parsed = [parsed]
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError as exc:
        logger.warning("rank: invalid JSON from LLM: %s\nRaw: %r", exc, raw_text[:300])
        return []


def _apply_scores(records: list[JobRecord], scores: list[dict], ranker_version: str) -> None:
    """Apply LLM score dicts (short-key format) back to JobRecord objects.

    Scores carrying an "i" index are matched to the record at that position;
    anything else falls back to positional order.
    """
    ordered: list[dict | None] = [None] * len(records)
    positional: list[dict] = []
    for score_dict in scores:
        if not isinstance(score_dict, dict):
            continue
        idx = score_dict.get("i")
        valid_idx = isinstance(idx, (int, float)) and 0 <= int(idx) < len(records)
        if valid_idx and ordered[int(idx)] is None:
            ordered[int(idx)] = score_dict
        else:
            positional.append(score_dict)
    # Fill any unmatched slots positionally
    it = iter(positional)
    for i, slot in enumerate(ordered):
        if slot is None:
            ordered[i] = next(it, None)

    for rec, score_dict in zip(records, ordered):
        if not isinstance(score_dict, dict):
            continue
        try:
            rec.fit_score = float(score_dict.get("s", rec.fit_score or 0))
            rec.fit_confidence = float(score_dict.get("c", 0.5))
            rec.fit_reason = str(score_dict.get("r", ""))
            kw = score_dict.get("k", [])
            if isinstance(kw, list):
                rec.matched_keywords = [str(k) for k in kw[:5]]
            rec.ranker_version = ranker_version
            rec.freshly_ranked = True
        except (ValueError, TypeError) as exc:
            logger.warning("rank: could not apply score for %s: %s", rec.job_id, exc)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def rank_jobs(
    records: list[JobRecord],
    profile: dict,
    settings: dict,
    domain_context: str = "",
) -> list[JobRecord]:
    """Run both ranking passes on a list of records; update fit_score in place."""
    if not records:
        return records

    ranker_cfg = _load_ranker()
    rank_cfg = settings.get("models", {}).get("rank", {})
    model = rank_cfg.get("model", "claude-haiku-4-5")
    batch_size = rank_cfg.get("batch_size", 5)
    max_tokens = rank_cfg.get("max_tokens_response", 200)
    # Default 0.0: never silently withhold jobs from LLM ranking unless the
    # user explicitly opts in to a keyword pre-filter in ranker.yaml.
    pre_score_threshold = ranker_cfg.get("pre_score_threshold", 0.0)
    ranker_version = (
        f"{ranker_cfg.get('version', 'v1')}-{_prompt_content_hash(ranker_cfg, domain_context)}"
    )

    # Pass 1 — keyword pre-score all records
    for rec in records:
        pre = keyword_prescore(rec, profile)
        rec.fit_score = pre
        rec.fit_confidence = 0.3  # low confidence for pre-score only

    # Split: below threshold → leave at pre-score; above → queue for LLM
    needs_llm = [r for r in records if (r.fit_score or 0) >= pre_score_threshold]
    skipped = len(records) - len(needs_llm)
    if skipped:
        logger.info(
            "rank: %d/%d jobs skipped LLM (pre-score < %.1f)",
            skipped, len(records), pre_score_threshold,
        )
        for rec in records:
            if (rec.fit_score or 0) < pre_score_threshold:
                rec.fit_reason = "filtered by keyword pre-scan"
                rec.fit_confidence = 0.3
                rec.ranker_version = ranker_version
                rec.freshly_ranked = True

    if not needs_llm:
        return records

    # Pass 2 — LLM ranking in batches
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not looks_configured_secret(api_key):
        logger.warning(
            "rank: ANTHROPIC_API_KEY is not configured; keeping keyword pre-scores for %d jobs",
            len(needs_llm),
        )
        for rec in needs_llm:
            rec.fit_reason = "keyword pre-score only; ANTHROPIC_API_KEY not configured"
            rec.fit_confidence = 0.3
            rec.ranker_version = ranker_version
        return records

    try:
        import anthropic
        # max_retries covers 429/overloaded/5xx with the SDK's own backoff —
        # without it, one bad API window permanently pins keyword pre-scores
        # on every job first seen during that window.
        client = anthropic.Anthropic(api_key=api_key, max_retries=4)
    except ImportError:
        logger.error("rank: anthropic package not installed; skipping LLM ranking")
        return records

    system_prompt = _build_system_prompt(ranker_cfg, profile, domain_context)

    for i in range(0, len(needs_llm), batch_size):
        batch = needs_llm[i : i + batch_size]
        try:
            scores = _call_llm_batch(client, model, max_tokens, system_prompt, batch, ranker_cfg)
            if len(scores) == len(batch):
                _apply_scores(batch, scores, ranker_version)
            else:
                # Retry individually
                logger.warning(
                    "rank: batch size mismatch (%d scores for %d jobs), retrying individually",
                    len(scores), len(batch),
                )
                for single_rec in batch:
                    solo_scores = _call_llm_batch(
                        client, model, max_tokens, system_prompt, [single_rec], ranker_cfg
                    )
                    if solo_scores:
                        _apply_scores([single_rec], solo_scores, ranker_version)
        except QuotaExceededError as exc:
            logger.error("rank: %s — %d job(s) keep keyword pre-scores", exc, len(needs_llm) - i)
            for rec in needs_llm[i:]:
                if not rec.ranker_version:
                    rec.fit_reason = "keyword pre-score only; daily API quota reached"
                    rec.fit_confidence = 0.3
            break
        except Exception as exc:
            logger.error("rank: LLM batch failed: %s", exc)

    return records
