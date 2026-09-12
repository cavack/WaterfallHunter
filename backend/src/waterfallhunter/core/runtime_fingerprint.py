"""Runtime fingerprint contracts that never impersonate a Git revision."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from waterfallhunter.core.canonical_json import canonical_json_bytes


LEGACY_RUNTIME_UNVERIFIED_REVISION = "LEGACY_RUNTIME_UNVERIFIED_REVISION"
VERIFIED_GIT_REVISION = "VERIFIED_GIT_REVISION"
RUNTIME_FINGERPRINT_VERSION = "runtime_fingerprint_v1"

NON_SECRET_CONFIG_ALLOWLIST = frozenset(
    {
        "LIVE_TRADING_ENABLED",
        "EXPERIMENTAL_PRETRIGGER_ENABLED",
        "EXPERIMENTAL_PRETRIGGER_THRESHOLD",
        "LBANK_EXECUTION_SHADOW_ENABLED",
        "LBANK_EXECUTION_SHADOW_BATCH_SIZE",
        "LBANK_EXECUTION_SHADOW_INTERVAL_SECONDS",
        "LBANK_EXECUTION_SHADOW_SUCCESS_RECHECK_SECONDS",
        "LBANK_EXECUTION_SHADOW_FAILURE_RECHECK_SECONDS",
    }
)

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_manifest(
    root: str | Path,
    *,
    exclude_parts: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Build a deterministic content manifest without following symlinks."""

    base = Path(root).resolve()
    if not base.is_dir():
        raise ValueError(f"manifest root is not a directory: {base}")
    excluded = frozenset(exclude_parts)
    entries: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(base)
        if excluded.intersection(relative.parts):
            continue
        content = path.read_bytes()
        entries.append(
            {
                "path": relative.as_posix(),
                "size": len(content),
                "sha256": sha256_bytes(content),
            }
        )
    return entries


def manifest_sha256(entries: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json_bytes(entries))


def non_secret_config(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: values[key]
        for key in sorted(NON_SECRET_CONFIG_ALLOWLIST)
        if key in values
    }


def build_runtime_fingerprint(
    *,
    revision_status: str,
    captured_at: int,
    source_manifests: Mapping[str, list[dict[str, Any]]],
    config: Mapping[str, Any],
    runtime: Mapping[str, Any],
    images: Mapping[str, Any] | None = None,
    git_sha: str | None = None,
    source_dirty: bool = False,
) -> dict[str, Any]:
    if revision_status not in {
        LEGACY_RUNTIME_UNVERIFIED_REVISION,
        VERIFIED_GIT_REVISION,
    }:
        raise ValueError("unknown revision status")
    if revision_status == LEGACY_RUNTIME_UNVERIFIED_REVISION and git_sha is not None:
        raise ValueError("a legacy runtime fingerprint cannot claim a Git SHA")
    if revision_status == VERIFIED_GIT_REVISION and not (
        git_sha and _GIT_SHA.fullmatch(git_sha)
    ):
        raise ValueError("a verified revision requires an exact 40-character Git SHA")
    if revision_status == VERIFIED_GIT_REVISION and source_dirty:
        raise ValueError("a dirty source tree cannot claim a verified Git revision")
    if captured_at <= 0:
        raise ValueError("captured_at must be a positive Unix timestamp")
    if not source_manifests or any(not entries for entries in source_manifests.values()):
        raise ValueError("every source manifest must contain at least one file")

    manifests = {
        name: {
            "files": entries,
            "tree_sha256": manifest_sha256(entries),
        }
        for name, entries in sorted(source_manifests.items())
    }
    safe_config = non_secret_config(config)
    payload: dict[str, Any] = {
        "contract_version": RUNTIME_FINGERPRINT_VERSION,
        "revision_status": revision_status,
        "captured_at": int(captured_at),
        "git_sha": git_sha,
        "source_dirty": bool(source_dirty),
        "source_manifests": manifests,
        "effective_nonsecret_config": safe_config,
        "effective_nonsecret_config_sha256": sha256_bytes(
            canonical_json_bytes(safe_config)
        ),
        "runtime": dict(sorted(runtime.items())),
        "images": dict(sorted((images or {}).items())),
    }
    fingerprint_id = sha256_bytes(canonical_json_bytes(payload))
    return {"runtime_fingerprint_id": fingerprint_id, **payload}
