#!/usr/bin/env python3
"""Build the deterministic corpus for the approved Wave 1D semantic contract."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.build_canonical_golden_corpus import cases
from waterfallhunter.core.canonical_json import canonical_json_bytes
from waterfallhunter.core.golden_corpus import (
    MODEL_CHANGE_REPLAY_CORPUS,
    build_corpus,
)


MODEL_CONTRACT_ID = "wave1d_semantics_v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backend/tests/golden/wave1d_semantics_corpus.json"),
    )
    args = parser.parse_args()
    corpus = build_corpus(
        corpus_type=MODEL_CHANGE_REPLAY_CORPUS,
        model_contract_id=MODEL_CONTRACT_ID,
        cases=cases(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(corpus) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
