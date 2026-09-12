#!/usr/bin/env python3
"""Build the canonical-main deterministic fixture corpus."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import subprocess

from waterfallhunter.core.canonical_json import canonical_json_bytes
from waterfallhunter.core.golden_corpus import (
    CANONICAL_MAIN_REPLAY_CORPUS,
    build_corpus,
)
from waterfallhunter.core.model_regression import replay_model_case


BASELINE_SHA = "652f99446ed523c0a602798dde4457bab7983373"
BASELINE_MODEL_PATHS = (
    "backend/src/waterfallhunter/core/final_ranking.py",
    "backend/src/waterfallhunter/core/multi_exchange_validator.py",
    "backend/src/waterfallhunter/core/risk_manager.py",
    "backend/src/waterfallhunter/core/score_v2.py",
)


def _require_unmodified_baseline_models() -> None:
    result = subprocess.run(
        ["git", "diff", "--quiet", BASELINE_SHA, "--", *BASELINE_MODEL_PATHS],
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            "refusing to bind changed model code to the canonical-main baseline"
        )


def _score_packet() -> dict:
    timing = {
        "valid": True,
        "two_closed_candles": True,
        "lower_high": True,
        "reclaim": True,
        "repump": False,
        "rsi_rollover": True,
        "bearish_close": True,
        "volume_acceleration": True,
    }
    return {
        "candles": {
            "4h": {
                "valid": True,
                "hype_context": True,
                "support_broken": True,
                "lower_high": True,
                "setup": "FAILED_PULLBACK",
                "bearish_close": True,
                "volume_acceleration": True,
            },
            "1h": deepcopy(timing),
            "15m": deepcopy(timing),
            "5m": deepcopy(timing),
        },
        "microstructure": {
            "approved": True,
            "spoofing_detected": False,
            "sell_flow_usdt": 60,
            "buy_flow_usdt": 40,
            "footprint": {"available": True, "aggressive_selling": True},
            "bid_depth_usdt": 1000,
            "ask_depth_usdt": 1000,
            "spread_pct": 0.05,
            "slippage_pct": 0.05,
        },
        "derivatives": {
            "available": True,
            "funding_rate": 0.0005,
            "funding_percentile": 0.95,
            "oi_change_1h_pct": 1,
            "taker_buy_sell_ratio": 0.8,
            "top_trader_long_short_ratio": 2.0,
        },
        "cross_exchange_confirmed": True,
        "price_location": {"below_vwap": True},
    }


def _candidate(
    status: str,
    score: float,
    execution: str,
    analysis_age: float,
    reference_age: float,
    *,
    evaluation_time: float,
) -> dict:
    return {
        "status": status,
        "score": score,
        "analysis_observed_at": evaluation_time - analysis_age,
        "reference_observed_at": evaluation_time - reference_age,
        "execution_suitability": {"status": execution},
        "metrics": {
            "score_version": "score_v2",
            "trade_eligible": True,
            "strategy_stages": {
                "hype": True,
                "damage": True,
                "setup": True,
                "trigger": status == "TRIGGERED",
            },
            "relative_weakness_features": {
                "available": True,
                "timeframes": {
                    "4h": {"relative_return_6bars_pct": -5.0},
                    "1h": {"relative_return_6bars_pct": -4.0},
                    "15m": {"relative_return_6bars_pct": -2.0},
                    "5m": {"relative_return_6bars_pct": -1.0},
                },
            },
        },
    }


def cases() -> list[dict]:
    complete = _score_packet()
    missing = deepcopy(complete)
    missing["derivatives"] = {
        "available": False,
        "reason": "missing valid funding rate",
    }
    active_buying = deepcopy(complete)
    active_buying["derivatives"]["taker_buy_sell_ratio"] = 1.2
    chain = {
        "passed": True,
        "hype": True,
        "damage": True,
        "setup": True,
        "trigger": True,
    }
    inputs = [
        ("strict_complete", {"evaluator": "score_v2", "arguments": complete}),
        ("strict_missing_derivatives", {"evaluator": "score_v2", "arguments": missing}),
        ("strict_active_buying", {"evaluator": "score_v2", "arguments": active_buying}),
        ("watch_missing_derivatives", {"evaluator": "score_v2_watch", "arguments": missing}),
        ("lifecycle_armed_threshold", {"evaluator": "suggested_status", "arguments": {
            "score": 60, "stages": chain, "microstructure_approved": True,
            "cross_exchange_confirmed": True,
        }}),
        ("legacy_btc_leverage", {"evaluator": "legacy_leverage", "arguments": {
            "symbol": "BTC/USDT:USDT",
        }}),
        ("observational_ranking_order", {"evaluator": "final_ranking", "arguments": {
            "evaluation_time": 1_700_000_000.0,
            "limit": 3,
            "candidates": {
                "WATCH": _candidate(
                    "WATCH", 60.0, "MARGINAL", 20.0, 10.0,
                    evaluation_time=1_700_000_000.0,
                ),
                "READY": _candidate(
                    "TRIGGERED", 85.0, "SUITABLE", 5.0, 2.0,
                    evaluation_time=1_700_000_000.0,
                ),
            },
        }}),
    ]
    return [
        {
            "case_id": case_id,
            "evidence_class": "DETERMINISTIC_FIXTURE",
            "model_impact": "BASELINE_BEHAVIOR",
            "input": case_input,
            "expected_output": replay_model_case(case_input),
        }
        for case_id, case_input in inputs
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backend/tests/golden/canonical_main_corpus.json"),
    )
    args = parser.parse_args()
    _require_unmodified_baseline_models()
    corpus = build_corpus(
        corpus_type=CANONICAL_MAIN_REPLAY_CORPUS,
        git_sha=BASELINE_SHA,
        cases=cases(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(corpus) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
