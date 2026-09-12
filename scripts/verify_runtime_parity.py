#!/usr/bin/env python3
"""Fail CI when direct-test and image runtime declarations drift."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _require(pattern: str, text: str, label: str) -> None:
    if not re.search(pattern, text):
        raise SystemExit(f"runtime parity mismatch: {label}")


def _require_literal(value: str, text: str, label: str) -> None:
    if value not in text:
        raise SystemExit(f"runtime parity mismatch: {label}")


def main() -> int:
    versions = json.loads(
        (ROOT / ".github" / "runtime-versions.json").read_text(encoding="utf-8")
    )
    backend = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    watchdog = (ROOT / "watchdog" / "Dockerfile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    _require(
        rf"ARG PYTHON_VERSION={re.escape(versions['backend_python'])}\b",
        backend,
        "backend Docker Python",
    )
    _require_literal(
        f"ARG PYTHON_IMAGE={versions['backend_image']}",
        backend,
        "backend base-image digest",
    )
    _require(
        rf"ARG NODE_VERSION={re.escape(versions['frontend_node'])}\b",
        frontend,
        "frontend Docker Node",
    )
    _require_literal(
        f"ARG NODE_IMAGE={versions['frontend_image']}",
        frontend,
        "frontend base-image digest",
    )
    _require(
        rf"ARG PYTHON_VERSION={re.escape(versions['watchdog_python'])}\b",
        watchdog,
        "watchdog Docker Python",
    )
    _require_literal(
        f"ARG PYTHON_IMAGE={versions['watchdog_image']}",
        watchdog,
        "watchdog base-image digest",
    )
    _require(
        rf'PYTHON_RUNTIME: "{re.escape(versions["backend_python"])}"',
        workflow,
        "CI Python",
    )
    _require(
        rf'NODE_RUNTIME: "{re.escape(versions["frontend_node"])}"',
        workflow,
        "CI Node",
    )
    print("runtime declarations are aligned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
