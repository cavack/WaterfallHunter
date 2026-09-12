from pathlib import Path

from scripts.build_canonical_golden_corpus import cases
from scripts.build_wave1d_semantics_corpus import MODEL_CONTRACT_ID
from waterfallhunter.core.canonical_json import canonical_json_bytes
from waterfallhunter.core.golden_corpus import (
    MODEL_CHANGE_REPLAY_CORPUS,
    build_corpus,
)


WAVE1D_CORPUS = (
    Path(__file__).parent / "golden" / "wave1d_semantics_corpus.json"
)


def test_checked_in_wave1d_corpus_matches_current_generator():
    generated = canonical_json_bytes(
        build_corpus(
            corpus_type=MODEL_CHANGE_REPLAY_CORPUS,
            model_contract_id=MODEL_CONTRACT_ID,
            cases=cases(),
        )
    ) + b"\n"
    checked_in = WAVE1D_CORPUS.read_bytes()

    assert checked_in == generated, (
        "checked-in Wave 1D corpus is stale; regenerate with "
        "scripts/build_wave1d_semantics_corpus.py\n"
        + generated.decode("utf-8")
    )
