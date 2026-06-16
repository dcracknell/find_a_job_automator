"""Tests for GitHub Actions workflow configuration."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def _load_workflow(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def test_daily_run_workflow_is_valid_yaml() -> None:
    workflow = _load_workflow(".github/workflows/daily_run.yml")
    assert workflow["name"] == "Daily Job Search"
    assert "schedule" in workflow["on"]
    assert "workflow_dispatch" in workflow["on"]
    assert "run-pipeline" in workflow["jobs"]

    steps = workflow["jobs"]["run-pipeline"]["steps"]
    step_names = {step["name"] for step in steps}

    assert "Restore persistent job data" in step_names
    assert "Run pipeline" in step_names
    assert "Save persistent job data" in step_names
    assert "Upload run outputs" in step_names


def test_configure_profile_workflow_is_valid_yaml() -> None:
    workflow = _load_workflow(".github/workflows/configure_profile.yml")
    assert workflow["name"] == "Configure Job Search Profile"
    assert "issues" in workflow["on"]
    assert "workflow_dispatch" in workflow["on"]
    assert "configure-profile" in workflow["jobs"]

    steps = workflow["jobs"]["configure-profile"]["steps"]
    step_names = {step["name"] for step in steps}

    assert "Fetch issue body" in step_names
    assert "Build profile from issue" in step_names
    assert "Commit updated profile" in step_names
    assert "Comment on issue" in step_names


def test_job_search_profile_issue_form_is_valid_yaml() -> None:
    issue_form = yaml.safe_load(
        Path(".github/ISSUE_TEMPLATE/job_search_profile.yml").read_text(encoding="utf-8")
    )

    assert issue_form["name"] == "Job search profile setup"
    assert "profile-setup" in issue_form["labels"]

    field_ids = {
        field.get("id")
        for field in issue_form["body"]
        if field.get("type") in {"input", "textarea", "dropdown"}
    }
    assert "cv_text" in field_ids
    assert "cv_pdf_attachment" in field_ids
    assert "core_target_roles" in field_ids
    assert "core_skills" in field_ids


def test_workflow_bash_blocks_are_valid() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")

    workflows = [
        _load_workflow(str(path))
        for path in sorted(Path(".github/workflows").glob("*.yml"))
    ]

    for workflow in workflows:
        for job in workflow["jobs"].values():
            for step in job["steps"]:
                script = step.get("run")
                if not script:
                    continue
                result = subprocess.run(
                    [bash, "-n", "-c", script],
                    capture_output=True,
                    check=False,
                    text=True,
                )
                assert result.returncode == 0, (
                    f"{workflow['name']} / {step['name']} failed bash syntax:\n"
                    f"{result.stderr}"
                )


def test_setup_ui_contains_required_controls() -> None:
    html = Path("docs/index.html").read_text(encoding="utf-8")

    assert 'rel="stylesheet"' not in html
    assert 'src="app.js"' not in html
    assert 'id="cv-text"' in html
    assert 'id="core-roles"' in html
    assert 'id="settings-summary"' in html
    assert 'id="open-issue"' in html
    assert "Job titles you want most" in html
    assert "Similar job titles you'd also consider" in html
    assert "Dream job titles, harder to get" in html
    assert "function buildIssueBody" in html
    assert "function generateQueries" in html
