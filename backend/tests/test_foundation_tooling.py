import math

import pytest

from waterfallhunter.core.canonical_json import canonical_json_bytes
from waterfallhunter.core.deployment_provenance import (
    DEPLOYMENT_PROVENANCE_PARTIAL,
    DEPLOYMENT_PROVENANCE_VERIFIED,
    evaluate_deployment_provenance,
)
from waterfallhunter.core.golden_corpus import (
    CANONICAL_MAIN_REPLAY_CORPUS,
    LEGACY_RUNTIME_CORPUS,
    build_corpus,
    replay_determinism_gate,
    verify_corpus,
)
from waterfallhunter.core.runtime_fingerprint import (
    LEGACY_RUNTIME_UNVERIFIED_REVISION,
    VERIFIED_GIT_REVISION,
    build_runtime_fingerprint,
    file_manifest,
)


def test_canonical_json_is_deterministic_and_rejects_non_finite_numbers():
    assert canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    assert canonical_json_bytes({"whole_float": 1.0}) == b'{"whole_float":1}'
    with pytest.raises(ValueError, match="non-finite"):
        canonical_json_bytes({"unsafe": math.nan})


def test_runtime_fingerprint_is_content_sensitive_and_never_hashes_secrets(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n")
    manifest = file_manifest(tmp_path)
    values = dict(
        revision_status=LEGACY_RUNTIME_UNVERIFIED_REVISION,
        captured_at=1_700_000_000,
        source_manifests={"backend": manifest},
        config={
            "LIVE_TRADING_ENABLED": False,
            "TELEGRAM_BOT_TOKEN": "must-not-appear",
        },
        runtime={"python": "3.12.3"},
    )
    first = build_runtime_fingerprint(**values)
    second = build_runtime_fingerprint(**values)
    assert first == second
    assert "TELEGRAM_BOT_TOKEN" not in str(first)
    assert "must-not-appear" not in str(first)

    with pytest.raises(ValueError, match="cannot claim"):
        build_runtime_fingerprint(**values, git_sha="a" * 40)

    with pytest.raises(ValueError, match="dirty source tree"):
        build_runtime_fingerprint(
            **{**values, "revision_status": VERIFIED_GIT_REVISION},
            git_sha="a" * 40,
            source_dirty=True,
        )

    verified = build_runtime_fingerprint(
        **{**values, "revision_status": VERIFIED_GIT_REVISION}, git_sha="a" * 40
    )
    assert verified["git_sha"] == "a" * 40

    with pytest.raises(ValueError, match="manifest root"):
        file_manifest(tmp_path / "missing")


def test_dual_corpus_identity_tamper_detection_and_replay_determinism():
    case = {
        "case_id": "strict-001",
        "input": {"symbol": "TESTUSDT"},
        "expected_output": {
            "reason_codes": ["STRICT_GATE_PASS"],
            "lifecycle_trace": ["WATCH", "TRIGGERED"],
            "generated_at": 123,
        },
    }
    legacy = build_corpus(
        corpus_type=LEGACY_RUNTIME_CORPUS,
        runtime_fingerprint_id="f" * 64,
        cases=[case],
    )
    canonical = build_corpus(
        corpus_type=CANONICAL_MAIN_REPLAY_CORPUS,
        git_sha="a" * 40,
        cases=[case],
    )
    assert verify_corpus(legacy) is True
    assert canonical["revision_status"] == VERIFIED_GIT_REVISION
    with pytest.raises(ValueError, match="exactly a 40-character"):
        build_corpus(
            corpus_type=CANONICAL_MAIN_REPLAY_CORPUS,
            git_sha="not-a-revision",
            cases=[case],
        )
    with pytest.raises(ValueError, match="must be unique"):
        build_corpus(
            corpus_type=CANONICAL_MAIN_REPLAY_CORPUS,
            git_sha="a" * 40,
            cases=[case, case],
        )
    legacy["cases"][0]["expected_output"]["reason_codes"] = ["TAMPERED"]
    assert verify_corpus(legacy) is False

    counter = iter([1, 2, 3])
    stable = replay_determinism_gate(
        lambda _: {"value": 7, "generated_at": next(counter)}, {}, repeats=3
    )
    assert stable["deterministic"] is True
    changing = iter([1, 2, 3])
    unstable = replay_determinism_gate(
        lambda _: {"value": next(changing)}, {}, repeats=3
    )
    assert unstable["deterministic"] is False


def test_deployment_provenance_requires_the_tested_and_running_artifact_to_match():
    digest = "sha256:" + "a" * 64
    complete = {
        "git_sha": "b" * 40,
        "dependency_lock_sha256": "c" * 64,
        "dockerfile_sha256": "d" * 64,
        "base_image_digest": digest,
        "built_image_digest": digest,
        "tested_image_digest": digest,
        "deployment_manifest_sha256": "e" * 64,
        "running_image_digest": digest,
        "runtime_fingerprint_sha256": "f" * 64,
    }
    assert evaluate_deployment_provenance(complete)["status"] == DEPLOYMENT_PROVENANCE_VERIFIED
    complete["running_image_digest"] = "sha256:" + "f" * 64
    result = evaluate_deployment_provenance(complete)
    assert result["status"] == DEPLOYMENT_PROVENANCE_PARTIAL
    assert result["mismatched_links"] == ["running_image_digest"]
