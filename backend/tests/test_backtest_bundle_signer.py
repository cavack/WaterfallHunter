from __future__ import annotations

from pathlib import Path

import pytest

from scripts.sign_backtest_bundle import _resolve_workspace_path, sign_bundle
from waterfallhunter.routes_backtest_lab import (
    BacktestLabRequest,
    backtest_attestation_sha256,
)


def test_signer_normalizes_and_attests_complete_bundle() -> None:
    key = "test-only-backtest-artifact-key-32-bytes"
    signed = sign_bundle(
        {
            "dataset_manifest_hash": "a" * 64,
            "initial_equity": 1_000,
            "events": [],
            "signal_rows": [],
        },
        artifact_hmac_key=key,
    )
    request = BacktestLabRequest.model_validate(signed)

    assert signed["artifact_key_id"] == "wfh-backtest-hmac-v1"
    assert signed["artifact_hmac_sha256"] == backtest_attestation_sha256(
        request,
        artifact_hmac_key=key,
    )


def test_signer_rejects_paths_outside_operator_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    assert _resolve_workspace_path(Path("bundle.json"), label="input").parent == workspace
    with pytest.raises(ValueError, match="must remain inside"):
        _resolve_workspace_path(tmp_path / "outside.json", label="input")
