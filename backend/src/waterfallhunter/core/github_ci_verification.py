"""Resolve fail-closed GitHub Actions CI evidence from the authoritative API."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waterfallhunter.core.signal_metadata import canonical_sha256


SHA256_PATTERN = r"^[0-9a-f]{64}$"
IMAGE_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"

_REQUIRED_CI_JOBS = (
    "backend",
    "frontend",
    "dependency-audit",
    "container-validation",
    "repository-hygiene",
)

_REQUIRED_STEPS = {
    "backend": (
        "Run pytest -q backend/tests",
        "Run PYTHONPATH=backend/src:. python scripts/verify_runtime_parity.py",
    ),
    "frontend": (
        "Run npm run typecheck",
        "Run npm run build",
    ),
    "dependency-audit": (
        "Audit Python dependencies",
        "Run npm audit --omit=dev --audit-level=high",
    ),
    "container-validation": (
        "Build revision-labelled production artifacts",
        "Test the exact backend artifact family",
        "Record exact tested backend image digest",
        "Verify OCI revision labels",
    ),
    "repository-hygiene": (
        "Reject tracked runtime and secret files",
        "Scan tracked text for common credential patterns",
    ),
}

_IMAGE_MARKER = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}T[0-9:.]+Z )?"
    r"WFH_TESTED_BACKEND_IMAGE_DIGEST=(sha256:[0-9a-f]{64})\r?$",
    re.MULTILINE,
)
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_GH_CANDIDATES = (Path("/usr/bin/gh"), Path("/usr/local/bin/gh"))


class TrustedCIVerificationError(RuntimeError):
    """Raised when authoritative GitHub CI evidence cannot be proven."""


class TrustedCIVerification(BaseModel):
    """Evidence derived from one exact successful GitHub Actions run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["github_actions_ci_verification_v1"] = (
        "github_actions_ci_verification_v1"
    )
    repository: str = Field(min_length=3)
    workflow_path: Literal[".github/workflows/ci.yml"]
    run_id: int = Field(ge=1, strict=True)
    run_attempt: int = Field(ge=1, strict=True)
    source_revision: str = Field(min_length=40, max_length=40)
    tested_image_digest: str = Field(pattern=IMAGE_DIGEST_PATTERN)
    required_job_ids: dict[str, int]
    critical_steps_sha256: str = Field(pattern=SHA256_PATTERN)
    verification_report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _sealed_report(self) -> "TrustedCIVerification":
        if not _GIT_SHA.fullmatch(self.source_revision):
            raise ValueError("trusted CI revision must be lowercase hexadecimal")
        if set(self.required_job_ids) != set(_REQUIRED_CI_JOBS):
            raise ValueError("trusted CI required-job set mismatch")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1
            for value in self.required_job_ids.values()
        ):
            raise ValueError("trusted CI job IDs must be positive integers")
        material = self.model_dump(mode="python")
        expected = material.pop("verification_report_sha256")
        if expected != canonical_sha256(material):
            raise ValueError("trusted CI verification report hash mismatch")
        return self


def _gh_executable() -> str:
    for candidate in _GH_CANDIDATES:
        try:
            stat_result = candidate.stat()
        except OSError:
            continue
        if (
            candidate.is_file()
            and not candidate.is_symlink()
            and stat_result.st_uid == 0
            and stat_result.st_mode & 0o022 == 0
        ):
            return str(candidate)
    raise TrustedCIVerificationError("GITHUB_CLI_UNAVAILABLE_OR_UNTRUSTED")


def _gh_json(endpoint: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [_gh_executable(), "api", endpoint],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise TrustedCIVerificationError(
            "GITHUB_API_VERIFICATION_FAILED"
        ) from error
    if not isinstance(payload, dict):
        raise TrustedCIVerificationError("GITHUB_API_RESPONSE_INVALID")
    return payload


def _gh_text(endpoint: str) -> str:
    try:
        completed = subprocess.run(
            [_gh_executable(), "api", endpoint],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise TrustedCIVerificationError(
            "GITHUB_JOB_LOG_UNAVAILABLE"
        ) from error
    return completed.stdout


def _required_job_map(
    jobs: list[Any],
    *,
    run_id: int,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name in _REQUIRED_CI_JOBS:
        matches = [
            job
            for job in jobs
            if isinstance(job, dict) and job.get("name") == name
        ]
        if len(matches) != 1:
            raise TrustedCIVerificationError(
                "GITHUB_REQUIRED_CI_JOB_MISSING_OR_AMBIGUOUS"
            )
        job = matches[0]
        if (
            job.get("run_id") not in (None, run_id)
            or job.get("status") != "completed"
            or job.get("conclusion") != "success"
        ):
            raise TrustedCIVerificationError("GITHUB_REQUIRED_CI_JOB_FAILED")
        job_id = job.get("id")
        if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id < 1:
            raise TrustedCIVerificationError("GITHUB_CI_JOB_ID_INVALID")
        result[name] = job
    return result


def _critical_step_material(
    jobs: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    material: list[dict[str, Any]] = []
    for job_name in _REQUIRED_CI_JOBS:
        job = jobs[job_name]
        steps = job.get("steps")
        if not isinstance(steps, list):
            raise TrustedCIVerificationError("GITHUB_CI_STEPS_INVALID")
        by_name = {
            str(step.get("name")): step
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("name"), str)
        }
        for step_name in _REQUIRED_STEPS[job_name]:
            step = by_name.get(step_name)
            if (
                not isinstance(step, dict)
                or step.get("status") != "completed"
                or step.get("conclusion") != "success"
            ):
                raise TrustedCIVerificationError(
                    "GITHUB_REQUIRED_CI_STEP_FAILED"
                )
            material.append(
                {
                    "job": job_name,
                    "job_id": int(job["id"]),
                    "step": step_name,
                    "step_number": int(step.get("number", 0)),
                    "conclusion": "success",
                }
            )
    return material



def resolve_github_current_main_revision(repository: str) -> str:
    """Resolve the exact current protected ``main`` revision from GitHub."""
    if not _REPOSITORY.fullmatch(repository):
        raise TrustedCIVerificationError("GITHUB_REPOSITORY_INVALID")
    payload = _gh_json(f"repos/{repository}/branches/main")
    commit = payload.get("commit")
    revision = commit.get("sha") if isinstance(commit, dict) else None
    if (
        payload.get("name") != "main"
        or payload.get("protected") is not True
        or not isinstance(revision, str)
        or _GIT_SHA.fullmatch(revision) is None
    ):
        raise TrustedCIVerificationError("GITHUB_CURRENT_MAIN_REVISION_UNTRUSTED")
    return revision

def resolve_github_ci_verification(
    *,
    repository: str,
    run_id: int,
    expected_revision: str,
) -> TrustedCIVerification:
    """Resolve exact successful CI evidence from GitHub, or fail closed."""
    if not _REPOSITORY.fullmatch(repository):
        raise TrustedCIVerificationError("GITHUB_REPOSITORY_INVALID")
    if (
        not isinstance(run_id, int)
        or isinstance(run_id, bool)
        or run_id < 1
        or not _GIT_SHA.fullmatch(expected_revision)
    ):
        raise TrustedCIVerificationError("GITHUB_CI_IDENTITY_INVALID")

    run = _gh_json(f"repos/{repository}/actions/runs/{run_id}")
    if (
        run.get("id") != run_id
        or run.get("name") != "CI"
        or run.get("head_sha") != expected_revision
        or run.get("path") != ".github/workflows/ci.yml"
        or run.get("event") not in {"pull_request", "push", "workflow_dispatch"}
        or (
            run.get("event") == "workflow_dispatch"
            and run.get("head_branch") != "main"
        )
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or not isinstance(run.get("run_attempt"), int)
        or isinstance(run.get("run_attempt"), bool)
        or int(run["run_attempt"]) < 1
    ):
        raise TrustedCIVerificationError("GITHUB_CI_RUN_NOT_TRUSTED")

    jobs_payload = _gh_json(
        f"repos/{repository}/actions/runs/{run_id}/jobs?per_page=100"
    )
    jobs = jobs_payload.get("jobs")
    if not isinstance(jobs, list):
        raise TrustedCIVerificationError("GITHUB_CI_JOBS_INVALID")
    required_jobs = _required_job_map(jobs, run_id=run_id)
    critical_steps = _critical_step_material(required_jobs)

    container_job_id = int(required_jobs["container-validation"]["id"])
    log = _gh_text(
        f"repos/{repository}/actions/jobs/{container_job_id}/logs"
    )
    markers = _IMAGE_MARKER.findall(log)
    if len(markers) != 1:
        raise TrustedCIVerificationError(
            "GITHUB_TESTED_IMAGE_DIGEST_UNPROVEN"
        )

    body = {
        "contract_version": "github_actions_ci_verification_v1",
        "repository": repository,
        "workflow_path": str(run["path"]),
        "run_id": run_id,
        "run_attempt": int(run["run_attempt"]),
        "source_revision": str(run["head_sha"]),
        "tested_image_digest": markers[0],
        "required_job_ids": {
            name: int(required_jobs[name]["id"])
            for name in _REQUIRED_CI_JOBS
        },
        "critical_steps_sha256": canonical_sha256(critical_steps),
    }
    return TrustedCIVerification.model_validate(
        {
            **body,
            "verification_report_sha256": canonical_sha256(body),
        }
    )
