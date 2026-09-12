import json
from pathlib import Path

from waterfallhunter.core.golden_corpus import (
    replay_determinism_gate,
    verify_corpus,
)
from waterfallhunter.core.model_regression import replay_model_case


CORPUS = (
    Path(__file__).parent / "golden" / "canonical_main_corpus.json"
)
WAVE1D_CORPUS = (
    Path(__file__).parent / "golden" / "wave1d_semantics_corpus.json"
)


def test_canonical_main_corpus_is_valid_and_bound_to_the_design_baseline():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert verify_corpus(corpus) is True
    assert corpus["git_sha"] == "652f99446ed523c0a602798dde4457bab7983373"
    assert len(corpus["cases"]) >= 7
    assert {case["evidence_class"] for case in corpus["cases"]} == {
        "DETERMINISTIC_FIXTURE"
    }


def test_wave1d_semantics_corpus_is_valid_and_explicitly_design_bound():
    corpus = json.loads(WAVE1D_CORPUS.read_text(encoding="utf-8"))
    assert verify_corpus(corpus) is True
    assert corpus["model_contract_id"] == "wave1d_semantics_v1"
    assert corpus["git_sha"] is None
    assert corpus["runtime_fingerprint_id"] is None


def test_every_wave1d_case_matches_three_deterministic_replays():
    corpus = json.loads(WAVE1D_CORPUS.read_text(encoding="utf-8"))
    for case in corpus["cases"]:
        gate = replay_determinism_gate(
            replay_model_case,
            case["input"],
            repeats=3,
        )
        assert gate["deterministic"] is True, case["case_id"]
        assert replay_model_case(case["input"]) == case["expected_output"], case["case_id"]
