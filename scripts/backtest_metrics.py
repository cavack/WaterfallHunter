from __future__ import annotations

import hashlib
import json
import math
from urllib.parse import urlparse


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _https_source(value) -> bool:
    values = value if isinstance(value, list) else [value]
    if not values:
        return False
    return all(
        isinstance(item, str)
        and urlparse(item).scheme == "https"
        and bool(urlparse(item).netloc)
        for item in values
    )


def _costs_reconcile(trade: dict) -> bool:
    costs = trade.get("execution_costs")
    if not isinstance(costs, dict) or costs.get("complete") is not True:
        return False
    fields = ("fee_r", "funding_r", "slippage_r")
    if any(not _finite_number(costs.get(field)) for field in fields):
        return False
    if costs["fee_r"] < 0 or costs["slippage_r"] < 0:
        return False
    provenance = costs.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) != {"fee", "funding", "slippage"}:
        return False
    if not all(_https_source(provenance[name]) for name in provenance):
        return False
    gross, net = trade.get("realized_r"), trade.get("net_realized_r")
    if not _finite_number(gross) or not _finite_number(net):
        return False
    expected = gross - costs["fee_r"] - costs["slippage_r"] + costs["funding_r"]
    return math.isclose(float(net), expected, rel_tol=0.0, abs_tol=1e-9)


def performance_metrics(trades, *, return_field: str = "net_realized_r", risk_fraction: float = 0.01) -> dict:
    if not _finite_number(risk_fraction) or not 0 < risk_fraction <= 1:
        raise ValueError("risk_fraction must be within (0, 1]")
    if not isinstance(trades, list):
        raise TypeError("trades must be a list")
    reasons = set()
    ordered = []
    cost_bases = set()
    for trade in trades:
        if not isinstance(trade, dict):
            reasons.add("invalid trade packet")
            continue
        value = trade.get(return_field)
        if not _finite_number(value):
            reasons.add(f"missing {return_field}")
            continue
        if return_field == "net_realized_r" and not _costs_reconcile(trade):
            reasons.add("incomplete or unreconciled execution costs")
            continue
        if return_field == "net_realized_r":
            # Legacy complete-cost packets predate the explicit basis field and
            # were defined by contract as realized. New modeled packets must say so.
            basis = trade["execution_costs"].get("basis", "realized")
            if basis not in {"realized", "modeled"}:
                reasons.add("missing execution cost basis")
                continue
            cost_bases.add(basis)
        timestamp = trade.get("timestamp")
        exit_timestamp = trade.get("exit_timestamp", timestamp)
        if not isinstance(timestamp, int) or not isinstance(exit_timestamp, int) or exit_timestamp < timestamp:
            reasons.add("invalid trade timestamps")
            continue
        ordered.append((exit_timestamp, timestamp, str(trade.get("symbol", "")), float(value)))
    if len(cost_bases) > 1:
        reasons.add("mixed execution cost bases")
    if reasons or len(ordered) != len(trades) or not ordered:
        if not trades:
            reasons.add("no trades")
        return {
            "available": False,
            "return_field": return_field,
            "sample_size": 0,
            "rejected_trades": len(trades) - len(ordered),
            "reasons": sorted(reasons),
        }

    ordered.sort()
    values = [item[-1] for item in ordered]
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = -sum(value for value in values if value < 0)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    equity = peak_equity = 1.0
    max_drawdown_fraction = 0.0
    cumulative_r = peak_r = max_drawdown_r = 0.0
    for value in values:
        equity *= max(0.0, 1.0 + value * risk_fraction)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown_fraction = max(max_drawdown_fraction, (peak_equity - equity) / peak_equity)
        cumulative_r += value
        peak_r = max(peak_r, cumulative_r)
        max_drawdown_r = max(max_drawdown_r, peak_r - cumulative_r)
    result = {
        "available": True,
        "return_field": return_field,
        "sample_size": len(values),
        "expectancy_r": round(sum(values) / len(values), 6),
        "gross_profit_r": round(gross_profit, 6),
        "gross_loss_r": round(gross_loss, 6),
        "profit_factor": round(profit_factor, 6) if profit_factor is not None else None,
        "profit_factor_status": "available" if profit_factor is not None else "undefined_no_losses",
        "max_drawdown_r": round(max_drawdown_r, 6),
        "max_drawdown_pct": round(max_drawdown_fraction * 100.0, 6),
        "ending_equity_multiple": round(equity, 8),
        "risk_fraction_per_trade": risk_fraction,
        "mdd_method": "closed_trade_compounded_equity",
        "ordering": "exit_timestamp_then_entry_timestamp",
    }
    if return_field == "net_realized_r":
        result["cost_basis"] = next(iter(cost_bases))
    return result


def selection_key(candidate: dict) -> tuple:
    performance = candidate.get("performance") if isinstance(candidate, dict) else None
    if not isinstance(performance, dict) or performance.get("available") is not True:
        return (1, 0, 0.0, float("inf"), 0.0, 0.0, 0, float("inf"))
    folds = candidate.get("oos_folds")
    positive = candidate.get("positive_oos_folds")
    if not isinstance(folds, int) or folds <= 0 or not isinstance(positive, int) or not 0 <= positive <= folds:
        return (1, 0, 0.0, float("inf"), 0.0, 0.0, 0, float("inf"))
    profit_factor = performance.get("profit_factor")
    return (
        0,
        -positive,
        -(positive / folds),
        float(performance["max_drawdown_pct"]),
        -float(profit_factor) if _finite_number(profit_factor) else 0.0,
        -float(performance["expectancy_r"]),
        -int(performance["sample_size"]),
        float(candidate.get("complexity", 0)),
    )


def expanding_walk_forward_windows(*, start_ms: int, end_ms: int, outcome_horizon_ms: int,
                                   folds: int = 3, initial_train_fraction: float = 0.5) -> list[dict]:
    if not isinstance(start_ms, int) or not isinstance(end_ms, int) or start_ms >= end_ms:
        raise ValueError("invalid walk-forward window")
    if not isinstance(outcome_horizon_ms, int) or outcome_horizon_ms < 0:
        raise ValueError("invalid outcome horizon")
    if not isinstance(folds, int) or folds < 2:
        raise ValueError("walk-forward requires at least two folds")
    if not _finite_number(initial_train_fraction) or not 0 < initial_train_fraction < 1:
        raise ValueError("invalid initial train fraction")
    span = end_ms - start_ms
    first_test = start_ms + int(span * initial_train_fraction)
    remaining = end_ms - first_test
    width = remaining // folds
    if width <= outcome_horizon_ms:
        raise ValueError("walk-forward folds are shorter than the outcome horizon")
    result = []
    for index in range(folds):
        test_start = first_test + index * width
        test_end = end_ms if index == folds - 1 else first_test + (index + 1) * width
        result.append({
            "fold": index + 1,
            "train_start_ms": start_ms,
            "selection_end_ms": test_start - outcome_horizon_ms,
            "test_start_ms": test_start,
            "test_signal_end_ms": test_end - outcome_horizon_ms,
            "test_end_ms": test_end,
            "purge_ms": outcome_horizon_ms,
        })
    return result


def calculate_slippage_profile(candidates: dict, *, now: float, executable_notional: float,
                               venue: str | None = None, max_age_seconds: float = 10.0,
                               minimum_samples: int = 20,
                               minimum_quote_volume_usdt: float = 0.0) -> dict:
    if not isinstance(candidates, dict) or not _finite_number(now) or not _finite_number(executable_notional):
        raise ValueError("invalid slippage profile input")
    if (executable_notional <= 0 or max_age_seconds <= 0 or minimum_samples < 1
            or not _finite_number(minimum_quote_volume_usdt) or minimum_quote_volume_usdt < 0):
        raise ValueError("invalid slippage profile limits")
    samples = []
    rejected_one_sided = 0
    for symbol, candidate in candidates.items():
        if not isinstance(candidate, dict) or candidate.get("data_status") != "live":
            continue
        metrics = candidate.get("metrics")
        micro = metrics.get("microstructure") if isinstance(metrics, dict) else None
        source = metrics.get("data_sources") if isinstance(metrics, dict) else None
        exchange = source.get("ticker_orderbook_candles_trades") if isinstance(source, dict) else None
        if venue is not None and exchange != venue:
            continue
        quote_volume = candidate.get("quote_volume")
        if not _finite_number(quote_volume) or quote_volume < minimum_quote_volume_usdt:
            continue
        selected_quote_volume = metrics.get("selected_quote_volume_usdt") if isinstance(metrics, dict) else None
        if not _finite_number(selected_quote_volume) or selected_quote_volume < minimum_quote_volume_usdt:
            continue
        observed_at = micro.get("observed_at") if isinstance(micro, dict) else None
        if not _finite_number(observed_at) or observed_at > now or now - observed_at > max_age_seconds:
            continue
        micro_notional = micro.get("executable_notional") if isinstance(micro, dict) else None
        if not _finite_number(micro_notional) or not math.isclose(
            float(micro_notional), executable_notional, rel_tol=0.0, abs_tol=1e-9
        ):
            continue
        if micro.get("executable") is not True:
            continue
        best_bid = micro.get("best_bid")
        sell_vwap = micro.get("sell_vwap")
        best_ask = micro.get("best_ask")
        buy_vwap = micro.get("buy_vwap")
        if (not all(_finite_number(value) and value > 0 for value in (best_bid, sell_vwap, best_ask, buy_vwap))
                or sell_vwap > best_bid or buy_vwap < best_ask):
            rejected_one_sided += 1
            continue
        entry = (float(best_bid) - float(sell_vwap)) / float(best_bid) * 100.0
        exit_value = (float(buy_vwap) - float(best_ask)) / float(best_ask) * 100.0
        samples.append({
            "symbol": str(symbol),
            "venue": exchange,
            "observed_at": float(observed_at),
            "reference_quote_volume_usdt": float(quote_volume),
            "selected_quote_volume_usdt": float(selected_quote_volume),
            "executable_notional_usdt": float(executable_notional),
            "executable": True,
            "best_bid": float(best_bid),
            "sell_vwap": float(sell_vwap),
            "best_ask": float(best_ask),
            "buy_vwap": float(buy_vwap),
            "entry_slippage_pct": float(entry),
            "exit_slippage_pct": float(exit_value),
            "round_trip_slippage_pct": float(entry) + float(exit_value),
        })
    samples.sort(key=lambda item: item["symbol"])
    if len(samples) < minimum_samples:
        return {
            "available": False,
            "sample_size": len(samples),
            "minimum_samples": minimum_samples,
            "rejected_one_sided": rejected_one_sided,
            "reason": "insufficient fresh same-notional slippage samples",
        }
    entries = [sample["entry_slippage_pct"] for sample in samples]
    exits = [sample["exit_slippage_pct"] for sample in samples]
    entry_mean = sum(entries) / len(entries)
    exit_mean = sum(exits) / len(exits)
    sample_bytes = json.dumps(samples, sort_keys=True, separators=(",", ":")).encode()
    return {
        "available": True,
        "sample_size": len(samples),
        "minimum_samples": minimum_samples,
        "rejected_one_sided": rejected_one_sided,
        "venue": venue,
        "minimum_quote_volume_usdt": float(minimum_quote_volume_usdt),
        "executable_notional_usdt": executable_notional,
        "mean_entry_slippage_pct": round(entry_mean, 8),
        "mean_exit_slippage_pct": round(exit_mean, 8),
        "mean_round_trip_slippage_pct": round(entry_mean + exit_mean, 8),
        "calculated_at": float(now),
        "maximum_age_seconds": float(max_age_seconds),
        "samples": samples,
        "samples_sha256": hashlib.sha256(sample_bytes).hexdigest(),
        "reason": None,
        "method": "arithmetic_mean_fresh_live_same_notional_orderbook_vwap",
    }


def empirical_slippage_cost_r(profile: dict, *, stop_pct: float, executable_notional: float,
                              venue: str, minimum_quote_volume_usdt: float) -> float:
    if not isinstance(profile, dict) or profile.get("available") is not True:
        raise ValueError("slippage profile is unavailable")
    if profile.get("method") != "arithmetic_mean_fresh_live_same_notional_orderbook_vwap":
        raise ValueError("unsupported slippage profile method")
    if not _https_source(profile.get("source_url")):
        raise ValueError("slippage profile lacks HTTPS provenance")
    if not _finite_number(stop_pct) or stop_pct <= 0:
        raise ValueError("invalid stop percentage")
    if not _finite_number(executable_notional) or executable_notional <= 0:
        raise ValueError("invalid executable notional")
    if not isinstance(venue, str) or not venue:
        raise ValueError("invalid slippage venue")
    if profile.get("venue") != venue:
        raise ValueError("slippage profile venue does not match the backtest venue")
    if not _finite_number(minimum_quote_volume_usdt) or minimum_quote_volume_usdt < 0:
        raise ValueError("invalid minimum quote volume")
    profile_minimum_volume = profile.get("minimum_quote_volume_usdt")
    if not _finite_number(profile_minimum_volume) or not math.isclose(
        float(profile_minimum_volume), float(minimum_quote_volume_usdt), rel_tol=0.0, abs_tol=1e-9
    ):
        raise ValueError("slippage profile minimum quote volume does not match the backtest")
    profile_notional = profile.get("executable_notional_usdt")
    if not _finite_number(profile_notional) or not math.isclose(
        float(profile_notional), float(executable_notional), rel_tol=0.0, abs_tol=1e-9
    ):
        raise ValueError("slippage profile notional does not match the backtest")
    if not isinstance(profile.get("sample_size"), int) or not isinstance(profile.get("minimum_samples"), int):
        raise ValueError("slippage profile lacks sample metadata")
    if profile["sample_size"] < profile["minimum_samples"]:
        raise ValueError("slippage profile sample is insufficient")
    samples = profile.get("samples")
    if not isinstance(samples, list) or len(samples) != profile["sample_size"]:
        raise ValueError("slippage profile samples are incomplete")
    sample_bytes = json.dumps(samples, sort_keys=True, separators=(",", ":")).encode()
    if profile.get("samples_sha256") != hashlib.sha256(sample_bytes).hexdigest():
        raise ValueError("slippage profile samples hash mismatch")
    calculated_at = profile.get("calculated_at")
    maximum_age = profile.get("maximum_age_seconds")
    if not _finite_number(calculated_at) or not _finite_number(maximum_age) or maximum_age <= 0:
        raise ValueError("slippage profile samples lack a freshness contract")
    entries, exits = [], []
    for sample in samples:
        if not isinstance(sample, dict) or sample.get("venue") != venue:
            raise ValueError("slippage profile samples contain a venue mismatch")
        observed_at = sample.get("observed_at")
        reference_quote_volume = sample.get("reference_quote_volume_usdt")
        selected_quote_volume = sample.get("selected_quote_volume_usdt")
        notional = sample.get("executable_notional_usdt")
        best_bid = sample.get("best_bid")
        sell_vwap = sample.get("sell_vwap")
        best_ask = sample.get("best_ask")
        buy_vwap = sample.get("buy_vwap")
        entry = sample.get("entry_slippage_pct")
        exit_value = sample.get("exit_slippage_pct")
        round_trip_sample = sample.get("round_trip_slippage_pct")
        if (not _finite_number(observed_at) or observed_at > calculated_at
                or calculated_at - observed_at > maximum_age):
            raise ValueError("slippage profile samples violate freshness")
        if (not _finite_number(reference_quote_volume) or reference_quote_volume < minimum_quote_volume_usdt
                or not _finite_number(selected_quote_volume) or selected_quote_volume < minimum_quote_volume_usdt):
            raise ValueError("slippage profile samples violate minimum quote volume")
        if not _finite_number(notional) or not math.isclose(
            float(notional), float(executable_notional), rel_tol=0.0, abs_tol=1e-9
        ):
            raise ValueError("slippage profile samples contain a notional mismatch")
        if sample.get("executable") is not True:
            raise ValueError("slippage profile samples contain a non-executable observation")
        if (not all(_finite_number(value) and value > 0 for value in (best_bid, sell_vwap, best_ask, buy_vwap))
                or sell_vwap > best_bid or buy_vwap < best_ask):
            raise ValueError("slippage profile samples lack raw two-sided prices")
        raw_entry = (float(best_bid) - float(sell_vwap)) / float(best_bid) * 100.0
        raw_exit = (float(buy_vwap) - float(best_ask)) / float(best_ask) * 100.0
        if (not _finite_number(entry) or entry < 0 or not _finite_number(exit_value) or exit_value < 0
                or not _finite_number(round_trip_sample)
                or not math.isclose(float(entry), raw_entry, rel_tol=0.0, abs_tol=1e-12)
                or not math.isclose(float(exit_value), raw_exit, rel_tol=0.0, abs_tol=1e-12)
                or not math.isclose(float(round_trip_sample), float(entry) + float(exit_value),
                                    rel_tol=0.0, abs_tol=1e-12)):
            raise ValueError("slippage profile samples do not reconcile")
        entries.append(float(entry))
        exits.append(float(exit_value))
    entry_mean = sum(entries) / len(entries)
    exit_mean = sum(exits) / len(exits)
    if (not _finite_number(profile.get("mean_entry_slippage_pct"))
            or not _finite_number(profile.get("mean_exit_slippage_pct"))
            or not math.isclose(profile["mean_entry_slippage_pct"], round(entry_mean, 8), rel_tol=0.0, abs_tol=1e-12)
            or not math.isclose(profile["mean_exit_slippage_pct"], round(exit_mean, 8), rel_tol=0.0, abs_tol=1e-12)):
        raise ValueError("slippage profile sample means do not reconcile")
    round_trip = profile.get("mean_round_trip_slippage_pct")
    if not _finite_number(round_trip) or round_trip < 0:
        raise ValueError("slippage profile lacks a round-trip mean")
    if not math.isclose(float(round_trip), round(entry_mean + exit_mean, 8), rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("slippage profile round-trip mean does not reconcile")
    return round(float(round_trip) / float(stop_pct), 8)
