#!/usr/bin/env python3
"""Validation-only reporting for the fixed Score V2 evidence contract."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
import math

from scripts.historical_backtest import (
    HISTORICAL_SCORE_V2_AVAILABLE_MAXIMUM,
    promotion_eligibility,
    purged_time_splits,
    summarize,
)
from scripts.backtest_metrics import (
    expanding_walk_forward_windows,
    performance_metrics,
    selection_key,
)

APPROVED_WEIGHTS = {
    "structural_post_pump": 35.0,
    "entry_timing": 20.0,
    "execution_microstructure": 20.0,
    "derivatives_confirmation": 15.0,
    "cross_exchange_confirmation": 5.0,
    "same_contract_price_location": 5.0,
}
CONFIGURATIONS = (
    {"name": "score_v2_available_45", "weights": APPROVED_WEIGHTS, "historical_available_threshold": 45.0},
    {"name": "score_v2_available_55", "weights": APPROVED_WEIGHTS, "historical_available_threshold": 55.0},
    {"name": "score_v2_available_65", "weights": APPROVED_WEIGHTS, "historical_available_threshold": 65.0},
)


def _valid_weights(weights: object) -> bool:
    if not isinstance(weights, dict) or set(weights) != set(APPROVED_WEIGHTS):
        return False
    try:
        values = {name: float(value) for name, value in weights.items()}
    except (TypeError, ValueError):
        return False
    return sum(values.values()) == 100.0 and values == APPROVED_WEIGHTS


def _threshold(candidate: dict) -> float | None:
    value = candidate.get("historical_available_threshold", 0.0)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    value = float(value)
    return value if 0.0 <= value <= HISTORICAL_SCORE_V2_AVAILABLE_MAXIMUM else None


def _valid_candidate(candidate: object) -> bool:
    return isinstance(candidate, dict) and _valid_weights(candidate.get("weights")) and _threshold(candidate) is not None


def _degradation(train: dict, validation: dict) -> float:
    train_value, validation_value = train.get("realized_expectancy_r"), validation.get("realized_expectancy_r")
    if train_value is None or validation_value is None:
        return float("inf")
    return max(float(train_value) - float(validation_value), 0.0)


def select_weights(*, train: dict[str, list], validation: dict[str, list], holdout: dict[str, list],
                   candidates, reward_r: float, minimum_validation_trades: int = 30) -> dict:
    """Select exclusively from training/validation; holdout is deliberately ignored here."""
    del holdout
    rejected, ranked = {}, []
    for candidate in candidates:
        name = candidate.get("name") if isinstance(candidate, dict) else None
        weights = candidate.get("weights") if isinstance(candidate, dict) else None
        if not isinstance(name, str) or not _valid_candidate(candidate):
            if isinstance(name, str):
                rejected[name] = "weights must sum to 100 and match approved component maxima"
            continue
        train_summary = summarize(train.get(name, []), reward_r)
        validation_summary = summarize(validation.get(name, []), reward_r)
        net = validation_summary.get("net_performance") or {}
        if net.get("available") is not True:
            rejected[name] = "validation costs are incomplete or unreconciled"
            continue
        if net.get("sample_size", 0) < minimum_validation_trades:
            rejected[name] = "insufficient validation trades"
            continue
        if net.get("expectancy_r") is None or net["expectancy_r"] <= 0:
            rejected[name] = "validation net expectancy is not positive"
            continue
        if net.get("profit_factor") is None:
            rejected[name] = "validation profit factor is undefined without observed losses"
            continue
        objective = {
            "oos_folds": 1,
            "positive_oos_folds": 1,
            "performance": net,
            "complexity": int(candidate.get("complexity", 0)),
        }
        ranked.append((
            selection_key(objective), name, dict(weights), _threshold(candidate),
            train_summary, validation_summary,
        ))
    if not ranked:
        return {
            "name": None, "weights": None, "selection_source": "validation",
            "holdout_used_for_selection": False, "rejected_configurations": rejected,
        }
    _, name, weights, threshold, train_summary, validation_summary = sorted(ranked)[0]
    degradation = _degradation(train_summary, validation_summary)
    return {
        "name": name, "weights": dict(weights), "selection_source": "validation",
        "holdout_used_for_selection": False, "rejected_configurations": rejected,
        "historical_available_threshold": threshold,
        "train": train_summary, "validation": validation_summary,
        "validation_to_train_degradation": None if degradation == float("inf") else round(degradation, 4),
        "selection_objective": [
            "positive_oos_folds", "max_drawdown_pct", "profit_factor",
            "net_expectancy_r", "sample_size", "simplicity",
        ],
    }


def historical_configuration_binding(calibration: dict) -> dict:
    selected = calibration.get("selected") if isinstance(calibration, dict) else None
    holdout = calibration.get("holdout") if isinstance(calibration, dict) else None
    if not isinstance(selected, dict) or not _valid_candidate(selected):
        raise ValueError("calibration does not contain a valid selected historical Score V2 configuration")
    if selected.get("selection_source") != "walk_forward_development_oos" or selected.get("holdout_used_for_selection") is not False:
        raise ValueError("historical configuration was not selected by development walk-forward only")
    return {
        "identifier": selected["name"],
        "weights": dict(selected["weights"]),
        "historical_available_threshold": _threshold(selected),
        "selection_source": selected["selection_source"],
        "holdout_used_for_selection": False,
        "validation_summary": dict(selected.get("validation") or {}),
        "holdout_summary": dict(holdout or {}),
    }


def _trades_by_configuration(report: dict, configurations):
    trades = report.get("trades") if isinstance(report.get("trades"), list) else []
    selected = {}
    for item in configurations:
        threshold = _threshold(item)
        if threshold is None:
            continue
        eligible = [
            trade for trade in trades
            if isinstance(trade, dict)
            and isinstance(trade.get("historical_score_v2"), dict)
            and isinstance(trade["historical_score_v2"].get("available_score"), (int, float))
            and trade["historical_score_v2"]["available_score"] >= threshold
        ]
        cooldown_ms = max(float(report.get("cooldown_hours") or 0.0), 0.0) * 3_600_000
        cooldown_until = {}
        replayed = []
        for trade in sorted(eligible, key=lambda value: (value["timestamp"], str(value.get("symbol", "")))):
            symbol = str(trade.get("symbol", ""))
            if trade["timestamp"] < cooldown_until.get(symbol, 0):
                continue
            replayed.append(trade)
            cooldown_until[symbol] = trade["timestamp"] + cooldown_ms
        selected[item["name"]] = replayed
    return selected


def walk_forward_assessment(by_configuration: dict[str, list], *, configurations,
                            start_ms: int, end_ms: int, outcome_horizon_ms: int,
                            folds: int = 3, initial_train_fraction: float = 0.5,
                            minimum_fold_trades: int = 10) -> dict:
    windows = expanding_walk_forward_windows(
        start_ms=start_ms,
        end_ms=end_ms,
        outcome_horizon_ms=outcome_horizon_ms,
        folds=folds,
        initial_train_fraction=initial_train_fraction,
    )
    results = {}
    eligible = []
    required_positive = math.ceil(folds * 2 / 3)
    for configuration in configurations:
        name = configuration["name"]
        trades = by_configuration.get(name, [])
        fold_packets = []
        oos_trades = []
        for window in windows:
            selected = [
                trade for trade in trades
                if window["test_start_ms"] <= trade["timestamp"] < window["test_signal_end_ms"]
            ]
            metrics = performance_metrics(selected, return_field="net_realized_r")
            sufficient = metrics.get("available") is True and metrics.get("sample_size", 0) >= minimum_fold_trades
            fold_packets.append({**window, "sufficient": sufficient, "performance": metrics})
            if sufficient:
                oos_trades.extend(selected)
        aggregate = performance_metrics(oos_trades, return_field="net_realized_r")
        positive = sum(
            packet["sufficient"] and packet["performance"].get("expectancy_r", 0.0) > 0
            for packet in fold_packets
        )
        sufficient_folds = sum(packet["sufficient"] for packet in fold_packets)
        valid = (
            sufficient_folds == folds
            and positive >= required_positive
            and aggregate.get("available") is True
            and aggregate.get("profit_factor") is not None
        )
        packet = {
            "oos_folds": folds,
            "sufficient_oos_folds": sufficient_folds,
            "positive_oos_folds": positive,
            "minimum_positive_oos_folds": required_positive,
            "performance": aggregate if valid else {
                **aggregate,
                "available": False,
                "reasons": sorted(set((aggregate.get("reasons") or []) + ["walk-forward stability gate failed"])),
            },
            "folds": fold_packets,
            "complexity": int(configuration.get("complexity", 0)),
        }
        results[name] = packet
        if valid:
            eligible.append((selection_key(packet), name))
    selected_name = sorted(eligible)[0][1] if eligible else None
    return {
        "method": "anchored_expanding_walk_forward_with_horizon_purge",
        "selection_order": [
            "positive_oos_folds", "max_drawdown_pct", "profit_factor",
            "net_expectancy_r", "sample_size", "simplicity",
        ],
        "selected_name": selected_name,
        "configurations": results,
    }


def calibrate(report: dict, configurations=CONFIGURATIONS, outcome_horizon_ms: int | None = None,
              minimum_validation_trades: int = 30, walk_forward_folds: int = 3,
              minimum_walk_forward_fold_trades: int = 10) -> dict:
    window = report.get("window") or {}
    start_ms, end_ms = window.get("start_ms"), window.get("end_ms")
    if not isinstance(start_ms, int) or not isinstance(end_ms, int) or start_ms >= end_ms:
        raise ValueError("backtest report lacks a valid immutable window")
    if report.get("candidate_pool_complete") is not True:
        raise ValueError("backtest report lacks a complete pre-threshold candidate pool")
    if outcome_horizon_ms is None:
        hold_hours = report.get("max_hold_hours")
        if not isinstance(hold_hours, int) or hold_hours < 1:
            raise ValueError("backtest report lacks a valid outcome horizon")
        outcome_horizon_ms = hold_hours * 3_600_000
    reward_r = float(report.get("reward_r") or 0.0)
    by_config = _trades_by_configuration(report, configurations)
    splits = {name: purged_time_splits(trades, start_ms, end_ms, outcome_horizon_ms) for name, trades in by_config.items()}
    selected = select_weights(
        train={name: split["train"] for name, split in splits.items()},
        validation={name: split["validation"] for name, split in splits.items()},
        holdout={name: split["holdout"] for name, split in splits.items()},
        candidates=configurations,
        reward_r=reward_r,
        minimum_validation_trades=minimum_validation_trades,
    )
    development_end = start_ms + (end_ms - start_ms) * 5 // 6
    walk_forward = walk_forward_assessment(
        by_config,
        configurations=configurations,
        start_ms=start_ms,
        end_ms=development_end,
        outcome_horizon_ms=outcome_horizon_ms,
        folds=walk_forward_folds,
        minimum_fold_trades=minimum_walk_forward_fold_trades,
    )
    if selected.get("name") != walk_forward.get("selected_name"):
        selected = {
            "name": None,
            "weights": None,
            "selection_source": "walk_forward_development_oos",
            "holdout_used_for_selection": False,
            "rejected_configurations": {
                **selected.get("rejected_configurations", {}),
                "selection": "inner validation and walk-forward selection did not agree",
            },
        }
    elif selected.get("name") is not None:
        selected["selection_source"] = "walk_forward_development_oos"
        selected["walk_forward"] = walk_forward["configurations"][selected["name"]]
    all_configurations = {}
    for config in configurations:
        name = config["name"]
        split = splits[name]
        all_configurations[name] = {
            "weights": dict(config["weights"]),
            "historical_available_threshold": _threshold(config),
            "available_score_maximum": HISTORICAL_SCORE_V2_AVAILABLE_MAXIMUM,
            "train": summarize(split["train"], reward_r),
            "validation": summarize(split["validation"], reward_r),
            "holdout": summarize(split["holdout"], reward_r),
        }
    selected_holdout = all_configurations.get(selected["name"], {}).get("holdout", {})
    days = max((end_ms - start_ms) / 86_400_000, 1.0)
    selected_signals = len(by_config.get(selected["name"], []))
    promotion = promotion_eligibility(
        selected_holdout, selected_signals / days,
        validation_summary=selected.get("validation", {}), reward_r=reward_r,
        strategy_equivalent=False,
    )
    return {
        "score_version": "score_v2", "generated_at": datetime.now(UTC).isoformat(),
        "source_windows": [window], "source_provenance": report.get("source_provenance", {}),
        "rejected_symbols": report.get("rejected_symbols", []),
        "strategy_equivalent": False,
        "execution_features": "unavailable_no_historical_l2_or_trades",
        "historical_score_contract": {
            "score_version": "score_v2_historical_available_v1",
            "available_component_maximum": HISTORICAL_SCORE_V2_AVAILABLE_MAXIMUM,
            "unavailable_components": ["execution_microstructure", "cross_exchange_confirmation"],
        },
        "all_configurations": all_configurations, "walk_forward": walk_forward, "selected": selected,
        "holdout": selected_holdout, "promotion_eligibility": promotion,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest-report", required=True)
    parser.add_argument("--output", default="research/backtests/score_v2")
    args = parser.parse_args()
    report = json.loads(Path(args.backtest_report).read_text())
    result = calibrate(report)
    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=True)
    end_ms = report["window"]["end_ms"]
    path = destination / f"score_v2_calibration_{end_ms}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(path)
    print(json.dumps({"selected": result["selected"]["name"], "promotion": result["promotion_eligibility"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
