from scripts.calibrate_score_v2 import (
    APPROVED_WEIGHTS,
    calibrate,
    historical_configuration_binding,
    select_weights,
    walk_forward_assessment,
    _trades_by_configuration,
)


def _trade(timestamp, net_r, *, score=80.0, outcome=None):
    return {
        "timestamp": timestamp,
        "exit_timestamp": timestamp + 1,
        "outcome": outcome or ("win" if net_r > 0 else "loss"),
        "realized_r": net_r + 0.1,
        "net_realized_r": net_r,
        "historical_score_v2": {"available_score": score},
        "execution_costs": {
            "complete": True,
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


def test_calibration_ranks_only_validation_and_never_uses_holdout_to_choose_weights():
    candidates = [
        {"name": "validation_positive", "weights": APPROVED_WEIGHTS},
        {"name": "validation_negative", "weights": APPROVED_WEIGHTS},
    ]
    selected = select_weights(
        train={
            "validation_positive": [_trade(1, 1.0), _trade(2, -0.2)],
            "validation_negative": [_trade(1, 1.0), _trade(2, -0.2)],
        },
        validation={
            "validation_positive": [_trade(3, 1.0), _trade(4, -0.2)],
            "validation_negative": [_trade(3, 0.1), _trade(4, -1.0)],
        },
        holdout={
            "validation_positive": [_trade(5, -1.0), _trade(6, -0.2)],
            "validation_negative": [_trade(5, 1.0), _trade(6, -0.2)],
        },
        candidates=candidates,
        reward_r=1.0,
        minimum_validation_trades=2,
    )

    assert selected["name"] == "validation_positive"
    assert selected["selection_source"] == "validation"
    assert selected["holdout_used_for_selection"] is False


def test_calibration_rejects_weight_vectors_that_do_not_preserve_the_approved_score_contract():
    selected = select_weights(
        train={"invalid": []},
        validation={"invalid": []},
        holdout={"invalid": []},
        candidates=[{"name": "invalid", "weights": {**APPROVED_WEIGHTS, "entry_timing": 19.0}}],
        reward_r=1.0,
        minimum_validation_trades=1,
    )

    assert selected["name"] is None
    assert selected["rejected_configurations"]["invalid"] == "weights must sum to 100 and match approved component maxima"


def test_calibration_uses_validation_to_select_a_threshold_that_changes_the_eligible_trade_set():
    report = {
        "window": {"start_ms": 0, "end_ms": 1_200}, "reward_r": 1.0,
        "candidate_pool_complete": True, "cooldown_hours": 0, "max_hold_hours": 1,
        "trades": [
            _trade(100, 1.0, score=55.0),
            _trade(520, 1.0, score=55.0),
            _trade(530, -0.5, score=70.0),
            _trade(750, 1.0, score=55.0),
            _trade(760, -0.2, score=70.0),
            _trade(1_050, -1.0, score=70.0),
        ],
        "source_provenance": {}, "rejected_symbols": [],
    }
    candidates = (
        {"name": "threshold_50", "weights": APPROVED_WEIGHTS, "historical_available_threshold": 50.0},
        {"name": "threshold_60", "weights": APPROVED_WEIGHTS, "historical_available_threshold": 60.0},
    )

    result = calibrate(
        report, configurations=candidates, outcome_horizon_ms=0,
        minimum_validation_trades=2, walk_forward_folds=2,
        minimum_walk_forward_fold_trades=2,
    )

    assert result["selected"]["name"] == "threshold_50"
    assert result["all_configurations"]["threshold_50"]["validation"]["signals"] == 2
    assert result["all_configurations"]["threshold_60"]["validation"]["signals"] == 1
    assert result["selected"]["holdout_used_for_selection"] is False


def test_walk_forward_assessment_prefers_positive_oos_folds_before_drawdown():
    candidates = (
        {"name": "stable", "weights": APPROVED_WEIGHTS, "historical_available_threshold": 45.0},
        {"name": "low_drawdown_but_negative", "weights": APPROVED_WEIGHTS, "historical_available_threshold": 55.0},
    )
    stable = [_trade(timestamp, value) for timestamp, value in (
        (410, 0.4), (490, -0.1), (610, 0.3), (690, -0.1), (810, 0.2), (890, -0.1)
    )]
    negative = [_trade(timestamp, -0.01) for timestamp in (410, 490, 610, 690, 810, 890)]

    result = walk_forward_assessment(
        {"stable": stable, "low_drawdown_but_negative": negative},
        configurations=candidates,
        start_ms=0,
        end_ms=1_000,
        outcome_horizon_ms=0,
        folds=3,
        initial_train_fraction=0.4,
        minimum_fold_trades=2,
    )

    assert result["selected_name"] == "stable"
    assert result["configurations"]["stable"]["positive_oos_folds"] == 3
    assert result["selection_order"] == [
        "positive_oos_folds", "max_drawdown_pct", "profit_factor", "net_expectancy_r", "sample_size", "simplicity"
    ]


def test_configuration_replay_applies_threshold_before_symbol_cooldown():
    report = {
        "cooldown_hours": 1,
        "trades": [
            {**_trade(1_000, 0.1, score=40.0), "symbol": "TESTUSDT"},
            {**_trade(2_000, 0.1, score=55.0), "symbol": "TESTUSDT"},
        ],
    }
    configurations = (
        {"name": "threshold_45", "weights": APPROVED_WEIGHTS, "historical_available_threshold": 45.0},
    )

    replay = _trades_by_configuration(report, configurations)

    assert [trade["timestamp"] for trade in replay["threshold_45"]] == [2_000]


def test_calibration_rejects_a_post_cooldown_report_that_is_not_a_complete_candidate_pool():
    report = {"window": {"start_ms": 0, "end_ms": 1_000}, "reward_r": 1.0, "trades": []}

    try:
        calibrate(report)
    except ValueError as exc:
        assert str(exc) == "backtest report lacks a complete pre-threshold candidate pool"
    else:
        raise AssertionError("incomplete candidate pool was accepted")


def test_historical_reports_bind_the_selected_validation_configuration_without_reselecting_from_holdout():
    calibration = {
        "selected": {
            "name": "threshold_55", "weights": APPROVED_WEIGHTS, "historical_available_threshold": 55.0,
            "selection_source": "walk_forward_development_oos", "holdout_used_for_selection": False,
            "validation": {"signals": 10},
        },
        "holdout": {"signals": 4},
    }

    binding = historical_configuration_binding(calibration)

    assert binding == {
        "identifier": "threshold_55", "weights": APPROVED_WEIGHTS,
        "historical_available_threshold": 55.0, "selection_source": "walk_forward_development_oos",
        "holdout_used_for_selection": False, "validation_summary": {"signals": 10},
        "holdout_summary": {"signals": 4},
    }
