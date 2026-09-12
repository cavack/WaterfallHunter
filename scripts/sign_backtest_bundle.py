#!/usr/bin/env python3
"""Attest a Backtest Lab bundle with an operator-held server key."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from waterfallhunter.routes_backtest_lab import (
    BacktestLabRequest,
    backtest_attestation_sha256,
)


def _resolve_workspace_path(candidate: Path, *, label: str) -> Path:
    workspace = Path.cwd().resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(workspace):
        raise ValueError(f"{label} path must remain inside {workspace}")
    return resolved


def sign_bundle(payload: dict[str, Any], *, artifact_hmac_key: str) -> dict[str, Any]:
    if len(artifact_hmac_key.encode("utf-8")) < 32:
        raise ValueError("BACKTEST_ARTIFACT_HMAC_KEY must contain at least 32 bytes")
    provisional = {
        **payload,
        "artifact_key_id": "wfh-backtest-hmac-v1",
        "artifact_hmac_sha256": "0" * 64,
    }
    request = BacktestLabRequest.model_validate(provisional)
    return {
        **request.model_dump(mode="json"),
        "artifact_hmac_sha256": backtest_attestation_sha256(
            request,
            artifact_hmac_key=artifact_hmac_key,
        ),
    }


def _write_atomic(destination: Path, payload: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".partial",
        dir=destination.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    input_path = _resolve_workspace_path(args.input, label="input")
    output_path = _resolve_workspace_path(args.output, label="output")
    if input_path == output_path:
        parser.error("input and output paths must be different")
    secret = os.environ.get("BACKTEST_ARTIFACT_HMAC_KEY", "")
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("backtest bundle must be a JSON object")
    signed = sign_bundle(payload, artifact_hmac_key=secret)
    _write_atomic(output_path, signed)
    print(json.dumps({"output": str(output_path), "artifact_key_id": signed["artifact_key_id"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
