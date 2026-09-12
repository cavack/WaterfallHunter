#!/usr/bin/env python3
"""Evaluate an artifact provenance input document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from waterfallhunter.core.canonical_json import canonical_json_bytes
from waterfallhunter.core.deployment_provenance import evaluate_deployment_provenance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate_deployment_provenance(
        json.loads(args.input.read_text(encoding="utf-8"))
    )
    encoded = canonical_json_bytes(result) + b"\n"
    if args.output:
        args.output.write_bytes(encoded)
    else:
        print(encoded.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
