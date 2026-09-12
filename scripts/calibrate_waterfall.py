#!/usr/bin/env python3
"""Research-only Waterfall trigger calibration from an immutable candidate pool."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from scripts.backtest_metrics import expanding_walk_forward_windows, performance_metrics, selection_key
from scripts.historical_backtest import purged_time_splits


CONFIGURATIONS = (
    {"name": "channel_v1_baseline", "requirements": (), "complexity": 0},
    {"name": "trigger_15m_two_bearish", "requirements": (("15m", "two_bearish"),), "complexity": 1},
    {"name": "trigger_15m_volume", "requirements": (("15m", "volume_acceleration"),), "complexity": 1},
    {"name": "trigger_5m_two_bearish", "requirements": (("5m", "two_bearish"),), "complexity": 1},
    {"name": "trigger_both_two_bearish", "requirements": (("15m", "two_bearish"), ("5m", "two_bearish")), "complexity": 2},
    {"name": "trigger_15m_two_bearish_volume", "requirements": (("15m", "two_bearish"), ("15m", "volume_acceleration")), "complexity": 2},
)


def _passes(trade: dict, requirements) -> bool:
    evidence = trade.get("evidence")
    if not isinstance(evidence, dict):
        return False
    return all(
        isinstance(evidence.get(timeframe), dict) and evidence[timeframe].get(field) is True
        for timeframe, field in requirements
    )


def trades_by_configuration(report: dict, configurations=CONFIGURATIONS) -> dict[str, list]:
    trades = report.get("trades") if isinstance(report, dict) else None
    if not isinstance(trades, list):
        raise ValueError("report lacks historical trades")
    cooldown_ms = max(float(report.get("cooldown_hours") or 0.0), 0.0) * 3_600_000
    result = {}
    for configuration in configurations:
        selected = [trade for trade in trades if _passes(trade, configuration["requirements"])]
        replayed = []
        cooldown_until = {}
        for trade in sorted(selected, key=lambda item: (item["timestamp"], str(item.get("symbol", "")))):
            symbol = str(trade.get("symbol", ""))
            if trade["timestamp"] < cooldown_until.get(symbol, 0):
                continue
            replayed.append(trade)
            cooldown_until[symbol] = trade["timestamp"] + cooldown_ms
        result[configuration["name"]] = replayed
    return result


def _performance(trades: list) -> dict:
    return performance_metrics(trades, return_field="net_realized_r")


def select_development_validation(by_configuration: dict[str, list], *, configurations, start_ms: int,
                                  end_ms: int, horizon_ms: int, minimum_trades: int) -> dict:
    ranked = []
    rejected = {}
    for configuration in configurations:
        name = configuration["name"]
        split = purged_time_splits(by_configuration[name], start_ms, end_ms, horizon_ms)
        metrics = _performance(split["validation"])
        if metrics.get("available") is not True or metrics.get("sample_size", 0) < minimum_trades:
            rejected[name] = "insufficient complete validation sample"
            continue
        if metrics.get("expectancy_r", 0.0) <= 0 or metrics.get("profit_factor") is None:
            rejected[name] = "validation net performance is not positive and defined"
            continue
        objective = {
            "oos_folds": 1,
            "positive_oos_folds": 1,
            "performance": metrics,
            "complexity": configuration["complexity"],
        }
        ranked.append((selection_key(objective), name, metrics))
    if not ranked:
        return {"selected_name": None, "rejected": rejected}
    _, name, metrics = sorted(ranked)[0]
    return {"selected_name": name, "performance": metrics, "rejected": rejected}


def development_walk_forward(by_configuration: dict[str, list], *, configurations, start_ms: int,
                             end_ms: int, horizon_ms: int, folds: int = 3,
                             minimum_fold_trades: int = 8) -> dict:
    windows = expanding_walk_forward_windows(
        start_ms=start_ms, end_ms=end_ms, outcome_horizon_ms=horizon_ms, folds=folds,
    )
    required_positive = math.ceil(folds * 2 / 3)
    packets = {}
    eligible = []
    for configuration in configurations:
        name = configuration["name"]
        fold_packets = []
        oos = []
        for window in windows:
            trades = [
                trade for trade in by_configuration[name]
                if window["test_start_ms"] <= trade["timestamp"] < window["test_signal_end_ms"]
            ]
            metrics = _performance(trades)
            sufficient = metrics.get("available") is True and metrics.get("sample_size", 0) >= minimum_fold_trades
            fold_packets.append({**window, "sufficient": sufficient, "performance": metrics})
            if sufficient:
                oos.extend(trades)
        aggregate = _performance(oos)
        sufficient_folds = sum(packet["sufficient"] for packet in fold_packets)
        positive_folds = sum(
            packet["sufficient"] and packet["performance"].get("expectancy_r", 0.0) > 0
            for packet in fold_packets
        )
        valid = (
            sufficient_folds == folds and positive_folds >= required_positive
            and aggregate.get("available") is True and aggregate.get("profit_factor") is not None
        )
        packet = {
            "oos_folds": folds,
            "sufficient_oos_folds": sufficient_folds,
            "positive_oos_folds": positive_folds,
            "minimum_positive_oos_folds": required_positive,
            "performance": aggregate,
            "folds": fold_packets,
            "complexity": configuration["complexity"],
            "valid": valid,
        }
        packets[name] = packet
        if valid:
            eligible.append((selection_key(packet), name))
    return {
        "method": "anchored_expanding_walk_forward_development_only_with_horizon_purge",
        "selected_name": sorted(eligible)[0][1] if eligible else None,
        "configurations": packets,
    }


def calibrate(report: dict, *, configurations=CONFIGURATIONS, minimum_validation_trades: int = 12,
              minimum_fold_trades: int = 8) -> dict:
    window = report.get("window") if isinstance(report, dict) else None
    if not isinstance(window, dict) or not isinstance(window.get("start_ms"), int) or not isinstance(window.get("end_ms"), int):
        raise ValueError("report lacks an immutable window")
    if window["start_ms"] >= window["end_ms"] or report.get("candidate_pool_complete") is not True:
        raise ValueError("report is not a complete immutable candidate pool")
    if report.get("net_ev_contract", {}).get("promotion_permitted") is not False:
        raise ValueError("report lacks the research-only net-EV guardrail")
    report_bytes = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    start_ms, end_ms = window["start_ms"], window["end_ms"]
    hold_hours = report.get("max_hold_hours")
    if not isinstance(hold_hours, int) or hold_hours < 1:
        raise ValueError("report lacks an outcome horizon")
    horizon_ms = hold_hours * 3_600_000
    development_end = start_ms + (end_ms - start_ms) * 5 // 6
    by_configuration = trades_by_configuration(report, configurations)
    inner = select_development_validation(
        by_configuration, configurations=configurations, start_ms=start_ms,
        end_ms=development_end, horizon_ms=horizon_ms, minimum_trades=minimum_validation_trades,
    )
    walk_forward = development_walk_forward(
        by_configuration, configurations=configurations, start_ms=start_ms,
        end_ms=development_end, horizon_ms=horizon_ms, minimum_fold_trades=minimum_fold_trades,
    )
    agreed = inner["selected_name"] is not None and inner["selected_name"] == walk_forward["selected_name"]
    selected_name = inner["selected_name"] if agreed else None
    holdout = {}
    if selected_name is not None:
        holdout_trades = [trade for trade in by_configuration[selected_name] if trade["timestamp"] >= development_end]
        holdout = _performance(holdout_trades)
    all_configurations = {}
    for configuration in configurations:
        name = configuration["name"]
        development = [trade for trade in by_configuration[name] if trade["timestamp"] < development_end]
        all_configurations[name] = {
            "requirements": [list(item) for item in configuration["requirements"]],
            "complexity": configuration["complexity"],
            "full_sample_size": len(by_configuration[name]),
            "development_performance": _performance(development),
            "walk_forward": walk_forward["configurations"][name],
        }
    return {
        "schema_version": "waterfall_calibration_v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_window": window,
        "source_canonical_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "selection_contract": {
            "configuration_space_frozen_before_holdout": True,
            "selection_data": "development inner-validation plus anchored walk-forward OOS",
            "holdout_used_for_selection": False,
            "agreement_required": True,
        },
        "all_configurations": all_configurations,
        "inner_validation": inner,
        "walk_forward": walk_forward,
        "selected": {
            "name": selected_name,
            "agreement": agreed,
            "research_only": True,
            "production_applied": False,
            "holdout_used_for_selection": False,
        },
        "holdout": holdout,
        "promotion_eligibility": {
            "eligible": False,
            "reasons": [
                "calibration is research-only and not feature-equivalent to production",
                "execution fee is modeled rather than the account's realized fee",
                "no production threshold or lifecycle state may be changed by this artifact",
            ],
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest-report", required=True)
    parser.add_argument("--output", default="research/backtests/waterfall_calibration")
    args = parser.parse_args()
    source = Path(args.backtest_report)
    report = json.loads(source.read_text())
    result = calibrate(report)
    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"waterfall_calibration_{report['window']['end_ms']}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(path)
    print(json.dumps({"selected": result["selected"], "holdout": result["holdout"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
