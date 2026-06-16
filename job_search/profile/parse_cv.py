"""PDF CV → profile.json via Anthropic API.

CRITICAL: The prompt MUST include:
  "use only facts present in the provided CV; do not invent or infer experience"
This prevents application materials being grounded in assumed context.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from job_search import PROJECT_ROOT
from job_search.util.quota import api_call_wrapper

logger = logging.getLogger(__name__)

_PROFILE_PATH = PROJECT_ROOT / "config" / "profile.json"

_SYSTEM_PROMPT_TEMPLATE = """\
You are a CV parser that extracts structured data from a candidate's CV.

CRITICAL RULE: Use ONLY facts explicitly present in the provided CV text.
Do NOT invent, infer, or assume any experience, qualification, skill, or detail
that is not directly stated in the CV. If a field cannot be determined from the CV,
use null or an empty list.

{domain_context}

Respond with a single valid JSON object matching the schema below.
Do not include markdown fences, preamble, or explanation — JSON only.

Schema:
{{
  "name": "string",
  "domain": "string (the domain name provided)",
  "secondary_domains": [],
  "location": {{"city": "string", "lat": null, "lon": null}},
  "search_radius_miles": 60,
  "remote_ok": true,
  "education": {{
    "highest_qualification": "string",
    "institution": "string",
    "completion_year": null,
    "grade": "string or null",
    "registrations": []
  }},
  "experience_years": null,
  "experience_summary": "string",
  "core_skills": ["list of explicit skills from CV"],
  "adjacent_skills": ["list of adjacent/supporting skills from CV"],
  "negative_signals": {{
    "title_excludes": ["Senior", "Staff", "Principal", "Lead", "Head of", "Director"],
    "requires_years_above": 3,
    "description_excludes": [],
    "company_blocklist": []
  }},
  "target_roles": {{
    "core": ["roles from CV experience and stated objectives"],
    "adjacent": [],
    "stretch": []
  }},
  "filters": {{
    "salary_floor_gbp": 0,
    "salary_unit": "annual",
    "max_days_since_posted": 30,
    "exclude_companies": [],
    "rejected_company_cooldown_days": 90
  }}
}}
"""


def _read_pdf(pdf_path: Path) -> str:
    """Extract text from a PDF file."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except ImportError:
        pass

    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        pass

    raise ImportError(
        "Install pdfplumber or pypdf to parse CVs: pip install pdfplumber"
    )


def parse_cv(pdf_path: Path, domain: str = "general") -> dict:
    """Read a PDF CV and call the Anthropic API to produce a profile dict.

    The domain's cv_parser_context is prepended to the system prompt.
    The prompt unconditionally includes the no-inference instruction.
    Writes the result to config/profile.json and returns it.
    """
    import anthropic
    import yaml

    # Load settings for model config
    settings_path = PROJECT_ROOT / "config" / "settings.yaml"
    with settings_path.open() as f:
        settings = yaml.safe_load(f)

    model_cfg = settings.get("models", {}).get("parse_cv", {})
    model = model_cfg.get("model", "claude-sonnet-4-6")
    max_tokens = model_cfg.get("max_tokens_response", 4096)

    # Load domain pack for cv_parser_context
    domain_context = ""
    try:
        from job_search.util.domain import load_pack
        pack = load_pack(domain)
        domain_context = pack.cv_parser_context or ""
        if domain_context:
            domain_context = f"DOMAIN-SPECIFIC EXTRACTION GUIDANCE ({domain}):\n{domain_context}\n"
    except Exception as exc:
        logger.warning("parse_cv: could not load domain pack %r: %s", domain, exc)

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(domain_context=domain_context)

    # Extract CV text
    logger.info("Extracting text from %s", pdf_path)
    cv_text = _read_pdf(pdf_path)
    if not cv_text.strip():
        raise ValueError(f"Could not extract text from {pdf_path}. Is it a scanned PDF?")

    user_message = (
        f"Domain: {domain}\n\n"
        f"CV text:\n{cv_text[:15000]}"  # cap at ~15k chars to stay within context
    )

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    with api_call_wrapper("parse_cv") as rec:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        rec["model"] = model
        rec["input_tokens"] = response.usage.input_tokens
        rec["cached_input_tokens"] = getattr(response.usage, "cache_read_input_tokens", 0)
        rec["output_tokens"] = response.usage.output_tokens

    raw_json = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw_json.startswith("```"):
        raw_json = raw_json.split("```")[1]
        if raw_json.startswith("json"):
            raw_json = raw_json[4:]
    raw_json = raw_json.strip()

    try:
        profile = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"API returned invalid JSON: {exc}\nRaw: {raw_json[:500]}") from exc

    # Ensure domain is set
    profile["domain"] = domain

    # Seed example skills/roles from domain pack if profile has empty lists
    try:
        from job_search.util.domain import load_pack
        pack = load_pack(domain)
        if not profile.get("core_skills") and pack.example_skills:
            profile["core_skills"] = pack.example_skills.get("core", [])
        if not profile.get("adjacent_skills") and pack.example_skills:
            profile["adjacent_skills"] = pack.example_skills.get("adjacent", [])
        if not profile.get("target_roles", {}).get("core") and pack.example_target_roles:
            profile.setdefault("target_roles", {})["core"] = pack.example_target_roles.get("core", [])
    except Exception:
        pass

    # Write to config/profile.json
    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _PROFILE_PATH.open("w") as f:
        json.dump(profile, f, indent=2)

    logger.info("profile.json written to %s", _PROFILE_PATH)
    return profile
