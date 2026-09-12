#!/usr/bin/env python3
"""Build or verify Golden Regression Corpus JSON artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from waterfallhunter.core.canonical_json import canonical_json_bytes
from waterfallhunter.core.golden_corpus import build_corpus, verify_corpus


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--type", required=True)
    build.add_argument("--cases", type=Path, required=True)
    build.add_argument("--git-sha")
    build.add_argument("--runtime-fingerprint-id")
    build.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("corpus", type=Path)
    args = parser.parse_args()

    if args.command == "build":
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
        corpus = build_corpus(
            corpus_type=args.type,
            cases=cases,
            git_sha=args.git_sha,
            runtime_fingerprint_id=args.runtime_fingerprint_id,
        )
        args.output.write_bytes(canonical_json_bytes(corpus) + b"\n")
        return 0

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    valid = verify_corpus(corpus)
    print(f"corpus verification {'passed' if valid else 'failed'}")
    return int(not valid)


if __name__ == "__main__":
    raise SystemExit(main())
