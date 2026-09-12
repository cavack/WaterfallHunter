"""Golden Regression Corpus construction and deterministic replay gates."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any, Callable, Iterable

from waterfallhunter.core.canonical_json import canonical_json_bytes
from waterfallhunter.core.runtime_fingerprint import (
    LEGACY_RUNTIME_UNVERIFIED_REVISION,
    VERIFIED_GIT_REVISION,
)


CORPUS_CONTRACT_VERSION = "golden_regression_corpus_v1"
LEGACY_RUNTIME_CORPUS = "LEGACY_RUNTIME_CORPUS"
CANONICAL_MAIN_REPLAY_CORPUS = "CANONICAL_MAIN_REPLAY_CORPUS"
MODEL_CHANGE_REPLAY_CORPUS = "MODEL_CHANGE_REPLAY_CORPUS"
MODEL_CONTRACT_REVISION = "MODEL_CONTRACT_REVISION"
DEFAULT_VOLATILE_FIELDS = frozenset(
    {"generated_at", "wall_clock_duration_ms", "worker_pid", "host_name"}
)
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def semantic_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def without_volatile_fields(value: Any, fields: Iterable[str]) -> Any:
    excluded = frozenset(fields)
    if isinstance(value, dict):
        return {
            key: without_volatile_fields(item, excluded)
            for key, item in sorted(value.items())
            if key not in excluded
        }
    if isinstance(value, list):
        return [without_volatile_fields(item, excluded) for item in value]
    return value


def build_case(case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "")
    if not case_id:
        raise ValueError("case_id is required")
    if "input" not in case or "expected_output" not in case:
        raise ValueError(f"case {case_id} requires input and expected_output")
    expected = without_volatile_fields(
        deepcopy(case["expected_output"]), DEFAULT_VOLATILE_FIELDS
    )
    built = {
        "case_id": case_id,
        "input": deepcopy(case["input"]),
        "expected_output": expected,
        "input_hash": semantic_hash(case["input"]),
        "expected_semantic_hash": semantic_hash(expected),
        "reason_code_hash": semantic_hash(expected.get("reason_codes", []))
        if isinstance(expected, dict)
        else None,
        "lifecycle_trace_hash": semantic_hash(expected.get("lifecycle_trace", []))
        if isinstance(expected, dict)
        else None,
        "execution_plan_hash": semantic_hash(expected.get("execution_plan"))
        if isinstance(expected, dict) and "execution_plan" in expected
        else None,
        "ordered_output_hash": semantic_hash(expected.get("ordered_output", []))
        if isinstance(expected, dict)
        else None,
    }
    for field in ("evidence_class", "model_impact"):
        if field in case:
            built[field] = str(case[field])
    return built


def build_corpus(
    *,
    corpus_type: str,
    cases: list[dict[str, Any]],
    git_sha: str | None = None,
    runtime_fingerprint_id: str | None = None,
    model_contract_id: str | None = None,
) -> dict[str, Any]:
    if corpus_type == LEGACY_RUNTIME_CORPUS:
        if not (
            runtime_fingerprint_id
            and _SHA256.fullmatch(runtime_fingerprint_id)
            and git_sha is None
            and model_contract_id is None
        ):
            raise ValueError(
                "legacy corpus requires exactly a 64-character runtime fingerprint"
            )
        revision_status = LEGACY_RUNTIME_UNVERIFIED_REVISION
    elif corpus_type == CANONICAL_MAIN_REPLAY_CORPUS:
        if not (
            git_sha
            and _GIT_SHA.fullmatch(git_sha)
            and runtime_fingerprint_id is None
            and model_contract_id is None
        ):
            raise ValueError(
                "canonical corpus requires exactly a 40-character Git SHA"
            )
        revision_status = VERIFIED_GIT_REVISION
    elif corpus_type == MODEL_CHANGE_REPLAY_CORPUS:
        if not (
            model_contract_id
            and re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", model_contract_id)
            and git_sha is None
            and runtime_fingerprint_id is None
        ):
            raise ValueError(
                "model-change corpus requires exactly one stable model_contract_id"
            )
        revision_status = MODEL_CONTRACT_REVISION
    else:
        raise ValueError("unknown corpus type")

    built_cases = [build_case(case) for case in sorted(cases, key=lambda x: str(x.get("case_id")))]
    case_ids = [case["case_id"] for case in built_cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("case_id values must be unique")
    payload = {
        "contract_version": CORPUS_CONTRACT_VERSION,
        "corpus_type": corpus_type,
        "revision_status": revision_status,
        "git_sha": git_sha,
        "runtime_fingerprint_id": runtime_fingerprint_id,
        "model_contract_id": model_contract_id,
        "volatile_fields": sorted(DEFAULT_VOLATILE_FIELDS),
        "cases": built_cases,
    }
    return {**payload, "corpus_sha256": semantic_hash(payload)}


def verify_corpus(corpus: dict[str, Any]) -> bool:
    supplied_hash = corpus.get("corpus_sha256")
    payload = {key: value for key, value in corpus.items() if key != "corpus_sha256"}
    if semantic_hash(payload) != supplied_hash:
        return False
    for case in corpus.get("cases", []):
        try:
            rebuilt = build_case(case)
        except (TypeError, ValueError):
            return False
        for key in (
            "input_hash",
            "expected_semantic_hash",
            "reason_code_hash",
            "lifecycle_trace_hash",
            "execution_plan_hash",
            "ordered_output_hash",
        ):
            if case.get(key) != rebuilt.get(key):
                return False
    return True


def replay_determinism_gate(
    replay: Callable[[Any], Any],
    case_input: Any,
    *,
    repeats: int = 3,
) -> dict[str, Any]:
    if repeats < 2:
        raise ValueError("determinism gate requires at least two replays")
    outputs = [
        without_volatile_fields(replay(deepcopy(case_input)), DEFAULT_VOLATILE_FIELDS)
        for _ in range(repeats)
    ]
    hashes = [semantic_hash(output) for output in outputs]
    return {
        "deterministic": len(set(hashes)) == 1,
        "repeats": repeats,
        "semantic_hashes": hashes,
    }
