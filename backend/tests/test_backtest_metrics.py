import pytest

from scripts.backtest_metrics import (
    calculate_slippage_profile,
    empirical_slippage_cost_r,
    expanding_walk_forward_windows,
    performance_metrics,
    selection_key,
)


def _net_trade(timestamp: int, net_r: float) -> dict:
    return {
        "symbol": f"T{timestamp}USDT",
        "timestamp": timestamp,
        "exit_timestamp": timestamp + 10,
        "realized_r": net_r + 0.1,
        "net_realized_r": net_r,
        "execution_costs": {
            "complete": True,
            "basis": "realized",
            "fee_r": 0.05,
            "funding_r": 0.0,
            "slippage_r": 0.05,
            "provenance": {
                "fee": "https://developers.binance.com/commission",
                "funding": "https://fapi.binance.com/fapi/v1/fundingRate",
                "slippage": "https://data.binance.vision/aggTrades.zip",
            },
        },
    }


def test_net_performance_rejects_missing_or_unreconciled_real_costs():
    missing = performance_metrics(
        [{"symbol": "AUSDT", "timestamp": 1, "realized_r": 1.0}],
        return_field="net_realized_r",
    )
    unreconciled = _net_trade(1, 1.0)
    unreconciled["net_realized_r"] = 0.9

    assert missing["available"] is False
    assert "missing net_realized_r" in missing["reasons"]
    assert performance_metrics([unreconciled], return_field="net_realized_r")["available"] is False


def test_net_performance_reports_mdd_profit_factor_expectancy_and_sample():
    metrics = performance_metrics(
        [_net_trade(index, value) for index, value in enumerate((1.0, -0.5, -0.25, 2.0), start=1)],
        return_field="net_realized_r",
        risk_fraction=0.10,
    )

    assert metrics["available"] is True
    assert metrics["sample_size"] == 4
    assert metrics["expectancy_r"] == 0.5625
    assert metrics["profit_factor"] == 4.0
    assert metrics["max_drawdown_r"] == 0.75
    assert metrics["max_drawdown_pct"] == 7.375
    assert metrics["mdd_method"] == "closed_trade_compounded_equity"
    assert metrics["cost_basis"] == "realized"


def test_net_performance_keeps_modeled_costs_distinct_from_realized_costs():
    trade = _net_trade(1, 0.5)
    trade["execution_costs"]["basis"] = "modeled"

    assert performance_metrics([trade])["cost_basis"] == "modeled"


def test_profit_factor_is_undefined_without_observed_losses():
    metrics = performance_metrics([_net_trade(1, 0.5), _net_trade(2, 1.0)])

    assert metrics["profit_factor"] is None
    assert metrics["profit_factor_status"] == "undefined_no_losses"


def test_selection_key_uses_oos_then_mdd_pf_expectancy_sample_and_simplicity():
    def packet(*, positive=2, folds=3, mdd=10.0, pf=2.0, ev=0.2, sample=50, complexity=1):
        return {
            "oos_folds": folds,
            "positive_oos_folds": positive,
            "performance": {
                "available": True,
                "max_drawdown_pct": mdd,
                "profit_factor": pf,
                "expectancy_r": ev,
                "sample_size": sample,
            },
            "complexity": complexity,
        }

    base = packet()
    assert selection_key(packet(positive=3, mdd=99.0)) < selection_key(base)
    assert selection_key(packet(mdd=9.0, pf=1.0)) < selection_key(base)
    assert selection_key(packet(pf=2.1, ev=-1.0)) < selection_key(base)
    assert selection_key(packet(ev=0.3, sample=1)) < selection_key(base)
    assert selection_key(packet(sample=51, complexity=99)) < selection_key(base)
    assert selection_key(packet(complexity=0)) < selection_key(base)


def test_expanding_walk_forward_windows_purge_the_full_outcome_horizon():
    folds = expanding_walk_forward_windows(
        start_ms=0,
        end_ms=1_000,
        outcome_horizon_ms=50,
        folds=3,
        initial_train_fraction=0.4,
    )

    assert [(item["test_start_ms"], item["test_end_ms"]) for item in folds] == [
        (400, 600),
        (600, 800),
        (800, 1_000),
    ]
    assert all(item["selection_end_ms"] + 50 == item["test_start_ms"] for item in folds)
    assert all(item["test_signal_end_ms"] + 50 == item["test_end_ms"] for item in folds)


def test_slippage_profile_averages_only_fresh_live_same_notional_observations():
    def candidate(observed_at, entry, exit_value, notional=50.0, exchange="binance", status="live",
                  quote_volume=10_000_000.0, microstructure_observed_at=None, executable=True):
        return {
            "data_status": status,
            "observed_at": observed_at,
            "quote_volume": quote_volume,
            "metrics": {
                "selected_quote_volume_usdt": quote_volume,
                "data_sources": {"ticker_orderbook_candles_trades": exchange},
                "microstructure": {
                    "observed_at": observed_at if microstructure_observed_at is None else microstructure_observed_at,
                    "executable": executable,
                    "executable_notional": notional,
                    "best_bid": 100.0,
                    "sell_vwap": 100.0 * (1.0 - entry / 100.0),
                    "best_ask": 100.0,
                    "buy_vwap": 100.0 * (1.0 + exit_value / 100.0) if exit_value is not None else None,
                    "entry_slippage_pct": entry,
                    "exit_slippage_pct": exit_value,
                },
            },
        }

    candidates = {
        "A": candidate(995.0, 0.01, 0.02),
        "B": candidate(996.0, 0.03, 0.04),
        "STALE": candidate(900.0, 9.0, 9.0),
        "WRONG_SIZE": candidate(999.0, 9.0, 9.0, notional=500.0),
        "WRONG_VENUE": candidate(999.0, 9.0, 9.0, exchange="okx"),
        "LOW_VOLUME": candidate(999.0, 9.0, 9.0, quote_volume=4_999_999.0),
        "LOW_SELECTED_VOLUME": candidate(999.0, 9.0, 9.0),
        "STALE_MICRO": candidate(999.0, 9.0, 9.0, microstructure_observed_at=900.0),
        "NOT_EXECUTABLE": candidate(999.0, 9.0, 9.0, executable=False),
        "MISSING_NOTIONAL": {
            "data_status": "live", "observed_at": 999.0, "quote_volume": 10_000_000.0,
            "metrics": {
                "data_sources": {"ticker_orderbook_candles_trades": "binance"},
                    "microstructure": {"observed_at": 999.0, "executable": True,
                                   "executable_notional": None,
                                   "best_bid": 100.0, "sell_vwap": 91.0,
                                   "best_ask": 100.0, "buy_vwap": 109.0,
                                   "entry_slippage_pct": 9.0, "exit_slippage_pct": 9.0},
            },
        },
    }
    candidates["LOW_SELECTED_VOLUME"]["metrics"]["selected_quote_volume_usdt"] = 4_999_999.0
    candidates["A"]["metrics"]["microstructure"]["entry_slippage_pct"] = 9.0
    candidates["A"]["metrics"]["microstructure"]["exit_slippage_pct"] = 9.0
    profile = calculate_slippage_profile(candidates, now=1_000.0, executable_notional=50.0,
       venue="binance", minimum_samples=2,
       minimum_quote_volume_usdt=5_000_000.0)

    assert profile["available"] is True
    assert profile["sample_size"] == 2
    assert profile["mean_entry_slippage_pct"] == 0.02
    assert profile["mean_exit_slippage_pct"] == 0.03
    assert profile["mean_round_trip_slippage_pct"] == 0.05
    assert profile["minimum_quote_volume_usdt"] == 5_000_000.0
    assert [sample["symbol"] for sample in profile["samples"]] == ["A", "B"]
    assert isinstance(profile["samples_sha256"], str) and len(profile["samples_sha256"]) == 64


def test_slippage_profile_rejects_one_sided_observations_instead_of_zero_filling():
    profile = calculate_slippage_profile({
        "A": {
            "data_status": "live",
            "observed_at": 999.0,
            "quote_volume": 10_000_000.0,
            "metrics": {
                "selected_quote_volume_usdt": 10_000_000.0,
                "data_sources": {"ticker_orderbook_candles_trades": "binance"},
                "microstructure": {
                    "observed_at": 999.0,
                    "executable": True,
                    "executable_notional": 50.0,
                    "best_bid": 100.0,
                    "sell_vwap": 99.99,
                    "best_ask": 100.0,
                    "buy_vwap": None,
                    "entry_slippage_pct": 0.01,
                    "exit_slippage_pct": None,
                },
            },
        },
    }, now=1_000.0, executable_notional=50.0, venue="binance", minimum_samples=1,
       minimum_quote_volume_usdt=5_000_000.0)

    assert profile["available"] is False
    assert profile["sample_size"] == 0
    assert profile["rejected_one_sided"] == 1
    assert profile["reason"] == "insufficient fresh same-notional slippage samples"


def test_empirical_slippage_cost_uses_the_real_profile_round_trip_mean():
    profile = calculate_slippage_profile({
        "A": {
            "data_status": "live", "observed_at": 999.0, "quote_volume": 10_000_000.0,
            "metrics": {
                "selected_quote_volume_usdt": 10_000_000.0,
                "data_sources": {"ticker_orderbook_candles_trades": "binance"},
                "microstructure": {"observed_at": 999.0, "executable": True, "executable_notional": 50.0,
                                   "best_bid": 100.0, "sell_vwap": 99.999, "best_ask": 100.0,
                                   "buy_vwap": 100.00451562,
                                   "entry_slippage_pct": 0.001, "exit_slippage_pct": 0.00451562},
            },
        },
    }, now=1_000.0, executable_notional=50.0, venue="binance", minimum_samples=1,
       minimum_quote_volume_usdt=5_000_000.0)
    profile["source_url"] = "https://waterfall.booksreadlive.online/dashboard/api/candidates"

    assert empirical_slippage_cost_r(
        profile,
        stop_pct=3.0,
        executable_notional=50.0,
        venue="binance",
        minimum_quote_volume_usdt=5_000_000.0,
    ) == 0.00183854


def test_empirical_slippage_cost_rejects_a_cross_venue_average():
    profile = calculate_slippage_profile({
        "A": {
            "data_status": "live", "observed_at": 999.0, "quote_volume": 10_000_000.0,
            "metrics": {
                "selected_quote_volume_usdt": 10_000_000.0,
                "data_sources": {"ticker_orderbook_candles_trades": "binance"},
                "microstructure": {"observed_at": 999.0, "executable": True, "executable_notional": 50.0,
                                   "best_bid": 100.0, "sell_vwap": 99.999, "best_ask": 100.0,
                                   "buy_vwap": 100.00451562,
                                   "entry_slippage_pct": 0.001, "exit_slippage_pct": 0.00451562},
            },
        },
    }, now=1_000.0, executable_notional=50.0, venue=None, minimum_samples=1,
       minimum_quote_volume_usdt=5_000_000.0)
    profile["source_url"] = "https://waterfall.booksreadlive.online/dashboard/api/candidates"

    with pytest.raises(ValueError, match="venue"):
        empirical_slippage_cost_r(
            profile,
            stop_pct=3.0,
            executable_notional=50.0,
            venue="binance",
            minimum_quote_volume_usdt=5_000_000.0,
        )


def test_empirical_slippage_cost_rejects_tampered_profile_samples():
    profile = calculate_slippage_profile({
        "A": {
            "data_status": "live", "observed_at": 999.0, "quote_volume": 10_000_000.0,
            "metrics": {
                "selected_quote_volume_usdt": 10_000_000.0,
                "data_sources": {"ticker_orderbook_candles_trades": "binance"},
                "microstructure": {"observed_at": 999.0, "executable": True, "executable_notional": 50.0,
                                   "best_bid": 100.0, "sell_vwap": 99.999, "best_ask": 100.0,
                                   "buy_vwap": 100.004,
                                   "entry_slippage_pct": 0.001, "exit_slippage_pct": 0.004},
            },
        },
    }, now=1_000.0, executable_notional=50.0, venue="binance", minimum_samples=1,
       minimum_quote_volume_usdt=5_000_000.0)
    profile["source_url"] = "https://waterfall.booksreadlive.online/dashboard/api/candidates"
    profile["samples"][0]["entry_slippage_pct"] = 99.0

    with pytest.raises(ValueError, match="samples"):
        empirical_slippage_cost_r(
            profile,
            stop_pct=3.0,
            executable_notional=50.0,
            venue="binance",
            minimum_quote_volume_usdt=5_000_000.0,
        )
