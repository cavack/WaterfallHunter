#!/usr/bin/env python3
"""Fail local setup early when the interpreter differs from the canonical runtime."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    declared = json.loads(
        (ROOT / ".github" / "runtime-versions.json").read_text(encoding="utf-8")
    )["backend_python"]
    observed = ".".join(str(part) for part in sys.version_info[:2])
    if observed != declared:
        raise SystemExit(
            f"Python {declared} is the canonical backend runtime "
            f"(.github/runtime-versions.json); {sys.executable} is {observed}. "
            f"Re-run with PYTHON=python{declared}."
        )
    print(f"python_runtime=PASS version={observed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
