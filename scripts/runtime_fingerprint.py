#!/usr/bin/env python3
"""Create a source-only RuntimeFingerprint without reading secrets."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import time
from pathlib import Path

from waterfallhunter.core.canonical_json import canonical_json_bytes
from waterfallhunter.core.runtime_fingerprint import (
    LEGACY_RUNTIME_UNVERIFIED_REVISION,
    VERIFIED_GIT_REVISION,
    build_runtime_fingerprint,
    file_manifest,
)


def _git_sha(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_dirty(root: Path) -> bool:
    return bool(
        subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    revision_status = (
        LEGACY_RUNTIME_UNVERIFIED_REVISION if args.legacy else VERIFIED_GIT_REVISION
    )
    fingerprint = build_runtime_fingerprint(
        revision_status=revision_status,
        captured_at=int(time.time()),
        git_sha=None if args.legacy else _git_sha(root),
        source_dirty=_source_dirty(root),
        source_manifests={
            "backend": file_manifest(
                root / "backend" / "src",
                exclude_parts={"__pycache__"},
            ),
            "frontend": file_manifest(
                root / "frontend",
                exclude_parts={"node_modules", ".next"},
            ),
            "watchdog": file_manifest(root / "watchdog"),
        },
        config={"LIVE_TRADING_ENABLED": False},
        runtime={"python": platform.python_version()},
    )
    encoded = canonical_json_bytes(fingerprint) + b"\n"
    if args.output:
        args.output.write_bytes(encoded)
    else:
        print(encoded.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
