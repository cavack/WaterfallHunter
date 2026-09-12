#!/usr/bin/env python3
"""Verify WFH-ORG migration manifest invariants."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


FINAL_DISPOSITIONS = {"KEEP", "FIX_FIRST", "SUPERSEDED", "DROP", "QUARANTINE"}
ALLOWED_DISPOSITIONS = FINAL_DISPOSITIONS | {"UNREVIEWED"}
REQUIRED_FILE_FIELDS = {
    "source_repo",
    "source_sha",
    "source_path",
    "source_blob_sha",
    "mode",
    "disposition",
    "destination_path",
    "rationale",
    "verification",
}


def verify(
    data: dict,
    *,
    allow_unreviewed: bool,
    target_index: dict[str, tuple[str, str]] | None = None,
) -> list[str]:
    errors: list[str] = []
    files = data.get("files", [])

    for item in files:
        for field in sorted(REQUIRED_FILE_FIELDS - item.keys()):
            errors.append(
                f"missing required field {field} for "
                f"{item.get('source_path', '<unknown>')}"
            )
        disposition = item.get("disposition")
        if disposition not in ALLOWED_DISPOSITIONS:
            errors.append(f"unknown disposition: {disposition}")

    if not allow_unreviewed:
        pending = [
            item.get("source_path", "<unknown>")
            for item in files
            if item.get("disposition") == "UNREVIEWED"
        ]
        if pending:
            errors.append(f"UNREVIEWED entries remain: {len(pending)}")
        for item in files:
            if item.get("disposition") not in {"KEEP", "FIX_FIRST"}:
                continue
            source_path = item.get("source_path", "<unknown>")
            if not item.get("destination_path"):
                errors.append(f"destination_path required for imported file: {source_path}")
            if not item.get("verification"):
                errors.append(f"verification required for imported file: {source_path}")

    declared_sources = {
        (str(item.get("repo")), str(item.get("sha"))): int(item.get("file_count", -1))
        for item in data.get("sources", [])
    }
    observed_counts: dict[tuple[str, str], int] = {}
    for item in files:
        key = (str(item.get("source_repo")), str(item.get("source_sha")))
        observed_counts[key] = observed_counts.get(key, 0) + 1
        if key not in declared_sources:
            errors.append(f"undeclared source: {key[0]}@{key[1]}")
    for key, expected_count in declared_sources.items():
        observed_count = observed_counts.get(key, 0)
        if observed_count != expected_count:
            errors.append(
                f"file count mismatch for {key[0]}@{key[1]}: "
                f"expected {expected_count}, observed {observed_count}"
            )

    sources: set[tuple[str, str, str]] = set()
    destinations: set[str] = set()
    for item in files:
        source_id = (
            str(item.get("source_repo")),
            str(item.get("source_sha")),
            str(item.get("source_path")),
        )
        if source_id in sources:
            errors.append("duplicate source: " + ":".join(source_id))
        sources.add(source_id)

        destination = item.get("destination_path")
        if not destination:
            continue
        if destination in destinations:
            errors.append(f"duplicate destination: {destination}")
        destinations.add(destination)

    schema_version = int(data.get("schema_version", 1))
    target_files = data.get("target_files", []) if schema_version >= 2 else []
    if schema_version >= 2:
        source_refs = {
            f"{item.get('source_repo')}@{item.get('source_sha')}:{item.get('source_path')}"
            for item in files
        }
        seen_target_paths: set[str] = set()
        for item in target_files:
            path = str(item.get("path", ""))
            if not path:
                errors.append("target file path required")
                continue
            if path in seen_target_paths:
                errors.append(f"duplicate target file: {path}")
            seen_target_paths.add(path)
            origin = item.get("origin")
            refs = item.get("source_refs") or []
            if path != "migration/source-manifest.json" and not item.get("blob_sha"):
                errors.append(f"blob_sha required for target file: {path}")
            if origin in {"legacy_unchanged", "legacy_fixed"} and not refs:
                errors.append(f"source_refs required for legacy target: {path}")
            for ref in refs:
                if ref not in source_refs:
                    errors.append(f"unknown target source_ref for {path}: {ref}")
            if not allow_unreviewed and not item.get("verification"):
                errors.append(f"verification required for target file: {path}")

    if target_index is not None:
        if schema_version >= 2:
            expected_paths = {str(item.get("path")) for item in target_files if item.get("path")}
        else:
            expected_paths = {
                str(item["destination_path"])
                for item in files
                if item.get("disposition") in {"KEEP", "FIX_FIRST"}
                and item.get("destination_path")
            }
        target_paths = set(target_index)
        for path in sorted(target_paths - expected_paths):
            errors.append(f"unmanifested target path: {path}")
        for path in sorted(expected_paths - target_paths):
            errors.append(f"manifest destination missing from target: {path}")
        if schema_version >= 2:
            for item in target_files:
                path = item.get("path")
                if not path or path not in target_index:
                    continue
                actual_mode, actual_blob = target_index[path]
                expected_mode = str(item.get("mode", ""))
                raw_expected_blob = item.get("blob_sha")
                expected_blob = str(raw_expected_blob) if raw_expected_blob is not None else None
                if expected_mode and actual_mode != expected_mode:
                    errors.append(
                        f"target mode mismatch: {path}: expected {expected_mode}, observed {actual_mode}"
                    )
                if expected_blob and actual_blob != expected_blob:
                    errors.append(
                        f"target blob mismatch: {path}: expected {expected_blob}, observed {actual_blob}"
                    )
    return errors


def _tracked_index(target_root: Path) -> dict[str, tuple[str, str]]:
    result = subprocess.run(
        ["git", "-C", str(target_root), "ls-files", "-s", "-z"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or "git ls-files failed")
    tracked: dict[str, tuple[str, str]] = {}
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        meta, path_raw = raw.split(b"\t", 1)
        mode_raw, blob_raw, stage_raw = meta.split(b" ", 2)
        if stage_raw != b"0":
            raise RuntimeError("unmerged target path: " + path_raw.decode("utf-8", errors="replace"))
        tracked[path_raw.decode("utf-8", errors="strict")] = (
            mode_raw.decode("ascii"),
            blob_raw.decode("ascii"),
        )
    return tracked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--allow-unreviewed", action="store_true")
    parser.add_argument("--target-root", type=Path)
    args = parser.parse_args()

    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    try:
        target_index = _tracked_index(args.target_root) if args.target_root else None
    except RuntimeError as exc:
        print(f"target tree inspection failed: {exc}", file=sys.stderr)
        return 1
    errors = verify(
        data,
        allow_unreviewed=args.allow_unreviewed,
        target_index=target_index,
    )
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
