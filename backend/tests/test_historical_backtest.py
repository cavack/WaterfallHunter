from scripts.historical_backtest import (
    EIGHT_HOURS,
    HistoricalFunding,
    _valid_candles,
    chronological_splits,
    derivatives_context,
    expectancy_r,
    is_bearish_trend,
    long_unwind_passes,
    historical_short_funding_r,
    modeled_round_trip_fee_r,
    outcome,
    outcome_details,
    promotion_eligibility,
    realized_r,
    post_hype_stages,
    channel_stages,
    purged_time_splits,
    signal_evidence,
    strategy_stages,
    score_v2_derivatives_context,
    historical_score_v2_components,
    historical_quote_volume,
)


def test_historical_funding_paginates_and_persists_complete_coverage(monkeypatch, tmp_path):
    start_ms = 100 * EIGHT_HOURS
    requested_start = start_ms - 90 * EIGHT_HOURS
    rows = [
        {
            "symbol": "TESTUSDT",
            "fundingTime": requested_start + index,
            "fundingRate": "0.0001",
            "markPrice": "1.25",
        }
        for index in range(1_001)
    ]
    calls = []

    def fake_request(path, **params):
        calls.append(params)
        available = [row for row in rows if params["startTime"] <= row["fundingTime"] <= params["endTime"]]
        return available[: params["limit"]]

    monkeypatch.setattr("scripts.historical_backtest.request", fake_request)
    end_ms = start_ms + 2_000
    history = HistoricalFunding(tmp_path, start_ms, end_ms)

    result = history._rows("TESTUSDT")

    assert len(result) == 1_001
    assert len(calls) == 2
    assert calls[1]["startTime"] == rows[999]["fundingTime"] + 1
    assert result[-1]["mark_price"] == 1.25
    cache = next(tmp_path.glob("TESTUSDT_*.json"))
    payload = __import__("json").loads(cache.read_text())
    assert payload["coverage_end_ms"] == end_ms
    assert len(payload["rows"]) == 1_001


def test_historical_funding_does_not_trust_a_legacy_truncated_cache(monkeypatch, tmp_path):
    import json

    start_ms = 100 * EIGHT_HOURS
    requested_start = start_ms - 90 * EIGHT_HOURS
    end_ms = start_ms + 2_000
    cache = tmp_path / f"TESTUSDT_{requested_start}_{end_ms}.json"
    cache.write_text(json.dumps([
        {"symbol": "TESTUSDT", "fundingTime": requested_start + index,
         "fundingRate": "0.0001", "markPrice": "1.0"}
        for index in range(1_000)
    ]))
    calls = []

    def fake_request(path, **params):
        calls.append(params)
        return [{"symbol": "TESTUSDT", "fundingTime": requested_start + 1_500,
                 "fundingRate": "0.0002", "markPrice": "1.1"}]

    monkeypatch.setattr("scripts.historical_backtest.request", fake_request)
    result = HistoricalFunding(tmp_path, start_ms, end_ms)._rows("TESTUSDT")

    assert calls and calls[0]["startTime"] == requested_start + 1_000
    assert result[-1]["timestamp"] == requested_start + 1_500


def test_historical_funding_window_excludes_entry_and_post_exit_charges(monkeypatch, tmp_path):
    start_ms = 100 * EIGHT_HOURS
    end_ms = start_ms + 4 * EIGHT_HOURS
    rows = [
        {"symbol": "TESTUSDT", "fundingTime": timestamp, "fundingRate": "0.0001", "markPrice": "100"}
        for timestamp in (start_ms, start_ms + EIGHT_HOURS, start_ms + 2 * EIGHT_HOURS)
    ]

    monkeypatch.setattr("scripts.historical_backtest.request", lambda path, **params: rows)
    selected = HistoricalFunding(tmp_path, start_ms, end_ms).between(
        "TESTUSDT", start_ms, start_ms + EIGHT_HOURS,
    )

    assert [item["timestamp"] for item in selected] == [start_ms + EIGHT_HOURS]


def test_position_setup_does_not_claim_a_calibrated_probability():
    from waterfallhunter.core.position_calculator import PositionCalculator

    result = PositionCalculator(
        slippage_pct=0.05,
    ).calculate_short_position(
        100.0,
        mark_price=100.0,
        market_info={
            "precision": {"price": 0.01, "amount": 0.001},
            "limits": {"cost": {"min": 1.0}},
        },
    )

    assert result["status"] == "READY"
    assert all("probability" not in key.lower() for key in result)


def test_position_setup_rejects_unmeasured_slippage_instead_of_using_a_magic_default():
    from waterfallhunter.core.position_calculator import PositionCalculator

    result = PositionCalculator().calculate_short_position(100.0, mark_price=100.0)

    assert result["status"] == "REJECTED: Missing measured slippage"


def test_same_bar_tp_and_stop_is_a_loss():
    rows = [[0, 100.0, 103.0, 95.0, 100.0, 1.0]]

    assert outcome(rows, 0, stop_pct=2.0, target_pct=4.0) == "loss"
    details = outcome_details(rows, 0, stop_pct=2.0, target_pct=4.0)
    assert details["exit_timestamp"] == 300_000
    assert details["funding_cutoff_timestamp"] == 0
    assert details["exit_reason"] == "same_candle_stop_and_target_conservative_loss"


def test_outcome_respects_the_declared_holding_horizon():
    rows = [
        [0, 100.0, 100.0, 100.0, 100.0, 1.0],
        [300_000, 100.0, 100.0, 94.0, 95.0, 1.0],
    ]

    assert outcome(rows, 0, stop_pct=2.0, target_pct=4.0, horizon_bars=1) == "timeout"
    assert outcome(rows, 0, stop_pct=2.0, target_pct=4.0, horizon_bars=2) == "win"


def test_timeout_is_marked_to_market_for_realized_expectancy():
    rows = [
        [0, 100.0, 100.0, 100.0, 100.0, 1.0],
        [300_000, 100.0, 100.0, 95.0, 95.0, 1.0],
    ]

    assert realized_r(rows, 0, stop_pct=10.0, target_pct=5.0, horizon_bars=2) == 0.5
    assert outcome_details(rows, 0, stop_pct=10.0, target_pct=5.0, horizon_bars=2) == {
        "outcome": "win",
        "realized_r": 0.5,
        "exit_price": 95.0,
        "exit_timestamp": 600_000,
        "funding_cutoff_timestamp": 300_000,
        "exit_reason": "target",
    }


def test_modeled_round_trip_fee_accounts_for_entry_and_exit_notional():
    profile = {
        "schema_version": "binance_usdm_fee_model_v1",
        "venue": "binance",
        "product": "USDT perpetual",
        "liquidity": "taker",
        "taker_commission_rate": 0.0004,
        "basis": "modeled_official_api_example_not_account_specific",
        "source_url": "https://developers.binance.com/commission",
    }

    assert modeled_round_trip_fee_r(
        profile, stop_pct=2.0, entry_price=100.0, exit_price=96.0,
    ) == 0.0392


def test_historical_short_funding_uses_mark_price_and_short_cashflow_sign():
    events = [
        {"timestamp": 1, "funding_rate": 0.0001, "mark_price": 110.0},
        {"timestamp": 2, "funding_rate": -0.0002, "mark_price": 90.0},
    ]

    assert historical_short_funding_r(events, entry_price=100.0, stop_pct=2.0) == -0.0035


def test_expectancy_uses_only_settled_outcomes():
    assert expectancy_r(["win", "loss", "timeout"], reward_r=2.0) == 0.5


def test_promotion_requires_a_sufficient_positive_holdout_and_density():
    net = {"available": True, "cost_basis": "realized", "expectancy_r": 0.05,
           "max_drawdown_pct": 10.0, "profit_factor": 1.6}
    accepted = promotion_eligibility(
        {"settled": 30, "win_rate": 0.70, "realized_expectancy_r": 0.01, "net_performance": net}, signals_per_day=2.0,
        validation_summary={"settled": 50, "realized_expectancy_r": 0.01, "net_performance": net}, reward_r=1.0,
        strategy_equivalent=True,
    )
    rejected = promotion_eligibility(
        {"settled": 29, "win_rate": 0.80, "realized_expectancy_r": 0.10}, signals_per_day=3.0
    )

    assert accepted["eligible"] is True
    assert rejected["eligible"] is False


def test_promotion_rejects_modeled_net_costs_even_when_metrics_pass():
    net = {"available": True, "cost_basis": "modeled", "expectancy_r": 0.05,
           "max_drawdown_pct": 10.0, "profit_factor": 1.6}
    result = promotion_eligibility(
        {"settled": 30, "win_rate": 0.70, "realized_expectancy_r": 0.01, "net_performance": net},
        signals_per_day=2.0,
        validation_summary={"settled": 50, "realized_expectancy_r": 0.01, "net_performance": net},
        reward_r=1.0,
        strategy_equivalent=True,
    )

    assert result["eligible"] is False
    assert "holdout execution costs are modeled rather than realized" in result["reasons"]


def test_promotion_rejects_gross_only_performance_even_when_headline_metrics_look_good():
    result = promotion_eligibility(
        {"settled": 100, "win_rate": 0.80, "realized_expectancy_r": 0.5},
        signals_per_day=3.0,
        validation_summary={"settled": 100, "realized_expectancy_r": 0.5},
        reward_r=2.0,
        strategy_equivalent=True,
    )

    assert result["eligible"] is False
    assert "holdout performance is not net of complete real execution costs" in result["reasons"]


def test_promotion_rejects_non_equivalent_or_sub_one_r_research():
    rejected = promotion_eligibility(
        {"settled": 30, "win_rate": 0.75, "realized_expectancy_r": 0.10}, signals_per_day=2.0,
        validation_summary={"settled": 50, "realized_expectancy_r": 0.10}, reward_r=0.5,
        strategy_equivalent=False,
    )
    assert rejected["eligible"] is False
    assert "cost-adjusted reward is below 1R" in rejected["reasons"]


def test_historical_candle_validation_rejects_invalid_ohlc():
    rows = [[index * 300_000 + 1, 10.0, 10.2, 9.8, 10.0, 1.0] for index in range(100)]
    rows[25][2] = 9.0
    assert _valid_candles(rows, 0, rows[-1][0] + 300_000) is None


def test_historical_quote_volume_uses_real_completed_quote_volume_only():
    rows = [[index * 300_000, 1.0, 1.1, 0.9, 1.0, 10.0, 20.0] for index in range(289)]

    assert historical_quote_volume(rows, 288) == 5_760.0
    assert historical_quote_volume([row[:6] for row in rows], 288) is None


def test_chronological_splits_do_not_overlap():
    trades = [{"timestamp": timestamp, "outcome": "win"} for timestamp in range(12)]

    splits = chronological_splits(trades)

    assert [len(splits[name]) for name in ("train", "validation", "holdout")] == [6, 4, 2]
    assert splits["train"][-1]["timestamp"] < splits["validation"][0]["timestamp"]
    assert splits["validation"][-1]["timestamp"] < splits["holdout"][0]["timestamp"]


def test_waterfall_v2_requires_separate_regime_setup_and_trigger():
    checks = {
        "4h": {"flags": {"lower_high": True, "bearish_close": True, "rsi_rollover": True}},
        "1h": {"flags": {"two_bearish": True, "lower_high": True, "reclaim_or_repump": True}},
        "15m": {"flags": {"two_bearish": True, "lower_high": True, "volume_acceleration": True, "bearish_close": True}},
        "5m": {"flags": {"lower_high": True, "bearish_close": True}},
    }

    assert strategy_stages(checks, "waterfall_v2") == {"regime": True, "setup": True, "trigger": True}
    checks["15m"]["flags"]["volume_acceleration"] = False
    assert strategy_stages(checks, "waterfall_v2")["trigger"] is False


def test_purged_time_splits_exclude_the_outcome_horizon_at_boundaries():
    trades = [{"timestamp": timestamp, "outcome": "win"} for timestamp in range(0, 100, 5)]

    splits = purged_time_splits(trades, start_ms=0, end_ms=100, outcome_horizon_ms=10)

    assert max(trade["timestamp"] for trade in splits["train"]) < 40
    assert min(trade["timestamp"] for trade in splits["validation"]) >= 50
    assert max(trade["timestamp"] for trade in splits["validation"]) < 75
    assert min(trade["timestamp"] for trade in splits["holdout"]) >= 85


def test_bearish_trend_requires_price_below_aligned_emas():
    descending = [100.0 - index for index in range(60)]
    rising = list(reversed(descending))

    assert is_bearish_trend(descending) is True
    assert is_bearish_trend(rising) is False


def test_post_hype_strategy_requires_hype_damage_failed_reclaim_and_trigger():
    checks = {
        "4h": {"hype_context": True, "support_broken": True, "failed_pullback": True,
               "flags": {"lower_high": True}},
        "15m": {"flags": {"two_bearish": True, "lower_high": True, "volume_acceleration": True, "bearish_close": True}},
        "5m": {"flags": {"lower_high": True, "bearish_close": True}},
    }

    assert post_hype_stages(checks) == {"hype": True, "damage": True, "setup": True, "trigger": True}
    checks["4h"]["hype_context"] = False
    assert post_hype_stages(checks)["hype"] is False


def test_channel_strategy_distinguishes_breakdown_from_failed_pullback():
    checks = {
        "4h": {"hype_context": True, "support_broken": True, "failed_pullback": False,
               "flags": {"lower_high": True, "bearish_close": True, "volume_acceleration": True}},
        "1h": {"flags": {"two_bearish": True, "lower_high": True, "bearish_close": True}},
        "15m": {"flags": {"lower_high": True, "bearish_close": True}},
        "5m": {"flags": {"lower_high": True, "bearish_close": True}},
    }

    stages = channel_stages(checks)

    assert stages["setup_type"] == "BREAKDOWN"
    assert all(stages[name] for name in ("hype", "damage", "setup", "trigger"))
    checks["4h"]["failed_pullback"] = True
    assert channel_stages(checks)["setup_type"] == "FAILED_PULLBACK"


def test_signal_evidence_contains_only_boolean_observed_flags():
    checks = {
        timeframe: {"flags": {"lower_high": timeframe != "5m", "bearish_close": True}}
        for timeframe in ("5m", "15m", "1h", "4h")
    }

    evidence = signal_evidence(checks)

    assert evidence["5m"]["lower_high"] is False
    assert evidence["4h"]["lower_high"] is True
    assert all(isinstance(value, bool) for flags in evidence.values() for value in flags.values())


def test_long_unwind_requires_real_oi_decline_sell_dominance_and_long_crowding():
    context = derivatives_context(
        {"open_interest_usdt": 95.0, "taker_long_short_volume_ratio": 0.8, "top_trader_long_short_ratio": 1.2},
        {"open_interest_usdt": 100.0},
    )

    assert context == {
        "oi_change_1h_pct": -5.0,
        "taker_long_short_volume_ratio": 0.8,
        "top_trader_long_short_ratio": 1.2,
    }
    assert long_unwind_passes(context) is True
    assert long_unwind_passes(derivatives_context(
        {"open_interest_usdt": 101.0, "taker_long_short_volume_ratio": 0.8, "top_trader_long_short_ratio": 1.2},
        {"open_interest_usdt": 100.0},
    )) is False


def test_score_v2_derivatives_rejects_missing_real_taker_ratio_instead_of_substituting_zero():
    context = score_v2_derivatives_context(
        funding_rate=0.0001,
        funding_history=[0.00005, 0.0001],
        oi_current=100.0,
        oi_one_hour_ago=101.0,
        taker_ratio=None,
        top_ratio=1.2,
    )

    assert context is None


def test_score_v2_derivatives_rejects_observations_after_or_too_old_for_entry():
    entry = 1_700_000_000_000
    context = score_v2_derivatives_context(
        funding_rate=0.0001,
        funding_history=[0.00005, 0.0001],
        oi_current=100.0,
        oi_one_hour_ago=101.0,
        taker_ratio=0.8,
        top_ratio=1.2,
        entry_timestamp=entry,
        timestamps={
            "funding": entry - 8 * 3_600_000 - 1,
            "oi_current": entry - 300_000,
            "oi_one_hour_ago": entry - 3_600_000,
            "taker": entry + 1,
            "top_trader": entry - 300_000,
        },
        source_urls={
            "funding": "https://fapi.binance.com/fapi/v1/fundingRate?symbol=1000PEPEUSDT",
            "metrics": "https://data.binance.vision/data/futures/um/daily/metrics/1000PEPEUSDT/metrics.zip",
        },
    )

    assert context is None


def test_score_v2_derivatives_rejects_accepted_packet_without_https_provenance():
    context = score_v2_derivatives_context(
        funding_rate=0.0001,
        funding_history=[0.00005, 0.0001],
        oi_current=100.0,
        oi_one_hour_ago=101.0,
        taker_ratio=0.8,
        top_ratio=1.2,
        source_urls={"funding": "", "metrics": "https://data.binance.vision/metrics.zip"},
    )

    assert context is None


def test_historical_score_v2_reports_known_components_and_never_invents_execution_or_cross_exchange():
    from waterfallhunter.core.score_v2 import ScoreV2
    checks = {
        "4h": {"hype_context": True, "support_broken": True, "failed_pullback": True,
                "flags": {"lower_high": True, "bearish_close": True, "volume_acceleration": True}},
        "1h": {"flags": {"two_bearish": True, "lower_high": True, "reclaim_or_repump": True,
                           "rsi_rollover": True, "bearish_close": True, "volume_acceleration": True}},
        "15m": {"flags": {"two_bearish": True, "lower_high": True, "reclaim_or_repump": True,
                            "rsi_rollover": True, "bearish_close": True, "volume_acceleration": True}},
        "5m": {"flags": {"two_bearish": True, "lower_high": True, "reclaim_or_repump": True,
                           "rsi_rollover": True, "bearish_close": True, "volume_acceleration": True}},
    }
    derivatives = {
        "funding_rate": 0.0001, "funding_percentile": 0.9, "oi_change_1h_pct": -1.0,
        "taker_buy_sell_ratio": 0.8, "top_trader_long_short_ratio": 1.2,
    }

    score = historical_score_v2_components(checks, derivatives, below_vwap=True)
    derivative_points = ScoreV2()._derivatives(derivatives, {"1h": {"bearish_close": True}})

    assert score["available_score"] == round(60.0 + derivative_points, 2)
    assert score["available_maximum"] == 75.0
    assert score["components"]["structural_post_pump"] == {"available": True, "points": 35.0, "maximum": 35.0}
    assert score["components"]["execution_microstructure"]["available"] is False
    assert score["components"]["cross_exchange_confirmation"]["available"] is False
