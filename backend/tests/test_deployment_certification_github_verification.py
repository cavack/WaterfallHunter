from __future__ import annotations

import pytest

import waterfallhunter.core.github_ci_verification as ci


REVISION = "a" * 40
DIGEST = "sha256:" + "b" * 64
RUN_ID = 123

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


def _jobs(*, failed_job: str | None = None) -> dict:
    jobs = []
    for index, (name, required_steps) in enumerate(_REQUIRED_STEPS.items()):
        conclusion = "failure" if name == failed_job else "success"
        steps = [
            {
                "name": step_name,
                "number": step_index + 1,
                "status": "completed",
                "conclusion": conclusion,
            }
            for step_index, step_name in enumerate(required_steps)
        ]
        jobs.append(
            {
                "id": index + 10,
                "run_id": RUN_ID,
                "name": name,
                "status": "completed",
                "conclusion": conclusion,
                "steps": steps,
            }
        )
    return {"jobs": jobs}


def _run() -> dict:
    return {
        "id": RUN_ID,
        "name": "CI",
        "head_sha": REVISION,
        "head_branch": "main",
        "path": ".github/workflows/ci.yml",
        "event": "pull_request",
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
    }


def _install_api_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    jobs: dict | None = None,
    log: str | None = None,
    run: dict | None = None,
) -> None:
    def fake_json(endpoint: str) -> dict:
        if endpoint.endswith("/jobs?per_page=100"):
            return _jobs() if jobs is None else jobs
        return _run() if run is None else run

    monkeypatch.setattr(ci, "_gh_json", fake_json)
    monkeypatch.setattr(
        ci,
        "_gh_text",
        lambda _endpoint: (
            f"2026-08-24T00:00:00.0000000Z "
            f"WFH_TESTED_BACKEND_IMAGE_DIGEST={DIGEST}\n"
            if log is None
            else log
        ),
    )


def test_trusted_ci_is_derived_from_exact_github_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_api_fakes(monkeypatch)

    trusted = ci.resolve_github_ci_verification(
        repository="cavack/WFH-ORG",
        run_id=RUN_ID,
        expected_revision=REVISION,
    )

    assert trusted.source_revision == REVISION
    assert trusted.tested_image_digest == DIGEST
    assert set(trusted.required_job_ids) == set(_REQUIRED_STEPS)
    assert len(trusted.critical_steps_sha256) == 64
    assert len(trusted.verification_report_sha256) == 64


def test_trusted_ci_accepts_successful_manual_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    run["event"] = "workflow_dispatch"
    _install_api_fakes(monkeypatch, run=run)

    trusted = ci.resolve_github_ci_verification(
        repository="cavack/WFH-ORG",
        run_id=RUN_ID,
        expected_revision=REVISION,
    )

    assert trusted.source_revision == REVISION


def test_trusted_ci_rejects_manual_run_from_non_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    run["event"] = "workflow_dispatch"
    run["head_branch"] = "fix/untrusted"
    _install_api_fakes(monkeypatch, run=run)

    with pytest.raises(
        ci.TrustedCIVerificationError,
        match="GITHUB_CI_RUN_NOT_TRUSTED",
    ):
        ci.resolve_github_ci_verification(
            repository="cavack/WFH-ORG",
            run_id=RUN_ID,
            expected_revision=REVISION,
        )


def test_trusted_ci_rejects_missing_image_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_api_fakes(monkeypatch, log="no marker\n")

    with pytest.raises(
        ci.TrustedCIVerificationError,
        match="GITHUB_TESTED_IMAGE_DIGEST_UNPROVEN",
    ):
        ci.resolve_github_ci_verification(
            repository="cavack/WFH-ORG",
            run_id=RUN_ID,
            expected_revision=REVISION,
        )


def test_trusted_ci_rejects_failed_required_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_api_fakes(
        monkeypatch,
        jobs=_jobs(failed_job="backend"),
    )

    with pytest.raises(
        ci.TrustedCIVerificationError,
        match="GITHUB_REQUIRED_CI_JOB_FAILED",
    ):
        ci.resolve_github_ci_verification(
            repository="cavack/WFH-ORG",
            run_id=RUN_ID,
            expected_revision=REVISION,
        )


def test_trusted_ci_rejects_wrong_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    run["head_sha"] = "0" * 40
    _install_api_fakes(monkeypatch, run=run)

    with pytest.raises(
        ci.TrustedCIVerificationError,
        match="GITHUB_CI_RUN_NOT_TRUSTED",
    ):
        ci.resolve_github_ci_verification(
            repository="cavack/WFH-ORG",
            run_id=RUN_ID,
            expected_revision=REVISION,
        )


def test_trusted_ci_rejects_duplicate_required_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = _jobs()
    jobs["jobs"].append(dict(jobs["jobs"][0]))
    _install_api_fakes(monkeypatch, jobs=jobs)

    with pytest.raises(
        ci.TrustedCIVerificationError,
        match="GITHUB_REQUIRED_CI_JOB_MISSING_OR_AMBIGUOUS",
    ):
        ci.resolve_github_ci_verification(
            repository="cavack/WFH-ORG",
            run_id=RUN_ID,
            expected_revision=REVISION,
        )



def test_current_main_revision_is_resolved_authoritatively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ci,
        "_gh_json",
        lambda endpoint: {
            "name": "main",
            "protected": True,
            "commit": {"sha": REVISION},
        }
        if endpoint == "repos/cavack/WFH-ORG/branches/main"
        else {},
    )

    assert ci.resolve_github_current_main_revision("cavack/WFH-ORG") == REVISION


def test_current_main_revision_rejects_invalid_branch_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ci,
        "_gh_json",
        lambda _endpoint: {"name": "main", "commit": {"sha": "not-a-sha"}},
    )

    with pytest.raises(
        ci.TrustedCIVerificationError,
        match="GITHUB_CURRENT_MAIN_REVISION_UNTRUSTED",
    ):
        ci.resolve_github_current_main_revision("cavack/WFH-ORG")



def test_current_main_revision_rejects_unprotected_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ci,
        "_gh_json",
        lambda _endpoint: {
            "name": "main",
            "protected": False,
            "commit": {"sha": REVISION},
        },
    )

    with pytest.raises(
        ci.TrustedCIVerificationError,
        match="GITHUB_CURRENT_MAIN_REVISION_UNTRUSTED",
    ):
        ci.resolve_github_current_main_revision("cavack/WFH-ORG")
