from __future__ import annotations

import pytest
from pydantic import ValidationError

from waterfallhunter.core.execution_planning import (
    ExecutionCostPolicy,
    MaintenanceMarginTier,
    PortfolioCapacity,
    PriceBand,
    RawShortLevels,
    RiskPolicy,
    SafeLeverageBounds,
    ValidatedMarketConstraints,
    build_short_paper_execution_plan,
)
from waterfallhunter.core.portfolio_replay import (
    PortfolioEvent,
    build_signal_level_research_report,
    replay_paper_portfolio,
)
from waterfallhunter.core.signal_metadata import canonical_sha256


MANIFEST_HASH = "d" * 64


def _plan(signal_id: str) -> dict:
    constraints = ValidatedMarketConstraints.create(
        canonical_symbol="TEST-USDT-PERP",
        venue_symbol="TEST/USDT:USDT",
        tick_size=0.1,
        quantity_step=0.001,
        contract_size=1.0,
        min_quantity=0.001,
        min_notional=5.0,
        maximum_leverage=20.0,
        price_band=PriceBand(minimum=1.0, maximum=1_000.0),
        maintenance_margin_tiers=(
            MaintenanceMarginTier(
                notional_floor=0.0,
                maintenance_margin_rate=0.005,
            ),
        ),
        liquidation_fee_rate=0.002,
        funding_semantics="timestamped_8h",
        constraints_observed_at=50,
        expires_at=200,
    )
    costs = ExecutionCostPolicy.create(
        taker_fee_rate=0.0006,
        maker_fee_rate=0.0002,
        entry_slippage_p95_rate=0.0005,
        exit_slippage_p95_rate=0.0007,
        expected_funding_rate=0.0001,
        liquidation_buffer_rate=0.02,
    )
    return build_short_paper_execution_plan(
        signal_id=signal_id,
        cluster_id="MEME_HIGH_BETA",
        evaluation_time=90,
        constraints=constraints,
        expected_constraints_hash=constraints.constraints_hash,
        cost_policy=costs,
        risk_policy=RiskPolicy.v1(),
        portfolio=PortfolioCapacity(cash_equity=200.0, unrealized_pnl=0.0),
        leverage_bounds=SafeLeverageBounds.create(
            exchange_max=20.0,
            maintenance_liquidation=10.0,
            volatility=6.0,
            liquidity_slippage=5.0,
            evidence_uncertainty=4.0,
            portfolio_open_risk=3.8,
        ),
        requested_risk_rate=0.0075,
        raw_levels=RawShortLevels(
            entry=100.09,
            tp1=98.01,
            tp2=96.01,
            stop=101.51,
        ),
    )


def _open(
    event_id: str,
    position_id: str,
    signal_id: str,
    occurred_at: int = 100,
) -> PortfolioEvent:
    return PortfolioEvent(
        event_id=event_id,
        occurred_at=occurred_at,
        event_type="OPEN",
        position_id=position_id,
        signal_id=signal_id,
        cluster_id="MEME_HIGH_BETA",
        execution_plan=_plan(signal_id),
    )


def test_replay_has_total_deterministic_order_and_does_not_reuse_unrealized_gain() -> None:
    events = [
        PortfolioEvent(
            event_id="close",
            occurred_at=120,
            event_type="CLOSE",
            position_id="position-1",
            price=96.0,
            exit_cost=0.1,
        ),
        _open("open-2", "position-2", "signal-2", occurred_at=110),
        PortfolioEvent(
            event_id="mark",
            occurred_at=105,
            event_type="MARK",
            position_id="position-1",
            price=96.0,
            exit_cost=0.0,
        ),
        PortfolioEvent(
            event_id="funding",
            occurred_at=120,
            event_type="FUNDING",
            position_id="position-1",
            amount=-0.2,
        ),
        _open("open-1", "position-1", "signal-1"),
    ]

    first = replay_paper_portfolio(
        events,
        initial_equity=200.0,
        risk_policy=RiskPolicy.v1(),
        dataset_manifest_hash=MANIFEST_HASH,
    )
    second = replay_paper_portfolio(
        list(reversed(events)),
        initial_equity=200.0,
        risk_policy=RiskPolicy.v1(),
        dataset_manifest_hash=MANIFEST_HASH,
    )

    assert first == second
    assert first["replay_sha256"] == second["replay_sha256"]
    assert first["event_order"] == ["open-1", "mark", "open-2", "funding", "close"]
    assert first["skipped_signals"] == [
        {
            "signal_id": "signal-2",
            "position_id": "position-2",
            "reason": "PORTFOLIO_CLUSTER_RISK_CAP_EXCEEDED",
        }
    ]
    assert first["final_cash_equity"] > 200.0
    assert first["report_type"] == "PORTFOLIO_REALIZABLE"


def test_mark_at_liquidation_closes_only_the_isolated_position() -> None:
    plan = _plan("signal-liquidated")
    events = [
        PortfolioEvent(
            event_id="open",
            occurred_at=100,
            event_type="OPEN",
            position_id="position-liquidated",
            signal_id="signal-liquidated",
            cluster_id="MEME_HIGH_BETA",
            execution_plan=plan,
        ),
        PortfolioEvent(
            event_id="liquidating-mark",
            occurred_at=110,
            event_type="MARK",
            position_id="position-liquidated",
            price=plan["liquidation_price"] + 1.0,
            exit_cost=0.2,
        ),
    ]

    replay = replay_paper_portfolio(
        events,
        initial_equity=200.0,
        risk_policy=RiskPolicy.v1(),
        dataset_manifest_hash=MANIFEST_HASH,
    )

    assert replay["open_positions"] == []
    assert replay["closed_positions"][0]["exit_reason"] == "ISOLATED_LIQUIDATION"
    assert replay["final_cash_equity"] < 200.0


def test_signal_level_and_portfolio_realizable_reports_remain_separate() -> None:
    signal_report = build_signal_level_research_report(
        [
            {
                "signal_id": "signal-1",
                "signal_triggered_at": 100,
                "outcome": "TP2_FIRST",
            }
        ],
        dataset_manifest_hash=MANIFEST_HASH,
    )
    portfolio_report = replay_paper_portfolio(
        [],
        initial_equity=200.0,
        risk_policy=RiskPolicy.v1(),
        dataset_manifest_hash=MANIFEST_HASH,
    )

    assert signal_report["report_type"] == "SIGNAL_LEVEL_RESEARCH"
    assert signal_report["portfolio_realizability_applied"] is False
    assert portfolio_report["report_type"] == "PORTFOLIO_REALIZABLE"
    assert "rows" not in portfolio_report
    assert "event_log" not in signal_report
    assert portfolio_report["cost_attribution"] == {
        "entry_cost": 0.0,
        "exit_cost": 0.0,
        "modeled_trading_cost": 0.0,
        "net_funding": 0.0,
    }


def test_partial_fill_scales_isolated_exposure_and_rejection_is_attributed() -> None:
    partial = PortfolioEvent(
        event_id="partial",
        occurred_at=100,
        event_type="OPEN",
        position_id="partial-position",
        signal_id="partial-signal",
        cluster_id="MEME_HIGH_BETA",
        execution_plan=_plan("partial-signal"),
        fill_fraction=0.25,
    )
    rejected = PortfolioEvent(
        event_id="rejected",
        occurred_at=101,
        event_type="OPEN",
        position_id="rejected-position",
        signal_id="rejected-signal",
        cluster_id="OTHER",
        execution_plan=_plan("rejected-signal"),
        fill_fraction=0,
        rejection_reason="VENUE_MIN_NOTIONAL",
    )
    replay = replay_paper_portfolio(
        [rejected, partial],
        initial_equity=200.0,
        risk_policy=RiskPolicy.v1(),
        dataset_manifest_hash=MANIFEST_HASH,
    )

    assert replay["event_order"] == ["partial", "rejected"]
    assert replay["event_log"][0]["status"] == "PARTIALLY_FILLED"
    assert replay["partial_fills"][0]["fill_fraction"] == 0.25
    assert replay["open_positions"][0]["risk_at_stop"] == pytest.approx(
        _plan("partial-signal")["risk_at_stop"] * 0.25,
    )
    assert replay["rejected_orders"] == [
        {
            "signal_id": "rejected-signal",
            "position_id": "rejected-position",
            "reason": "VENUE_MIN_NOTIONAL",
        }
    ]


@pytest.mark.parametrize(
    ("event_changes", "replay_policy", "expected_reason"),
    [
        ({"cluster_id": "OTHER"}, RiskPolicy.v1(), "PLAN_CLUSTER_ID_MISMATCH"),
        ({"signal_id": "other"}, RiskPolicy.v1(), "PLAN_SIGNAL_ID_MISMATCH"),
        (
            {},
            RiskPolicy.create(max_open_positions=4),
            "PLAN_RISK_POLICY_HASH_MISMATCH",
        ),
        ({"occurred_at": 80}, RiskPolicy.v1(), "PLAN_FROM_FUTURE"),
    ],
)
def test_open_events_are_bound_to_plan_identity_policy_and_time(
    event_changes: dict,
    replay_policy: RiskPolicy,
    expected_reason: str,
) -> None:
    values = {
        "event_id": "open",
        "occurred_at": 100,
        "event_type": "OPEN",
        "position_id": "position",
        "signal_id": "signal",
        "cluster_id": "MEME_HIGH_BETA",
        "execution_plan": _plan("signal"),
    }
    event = PortfolioEvent(**{**values, **event_changes})
    replay = replay_paper_portfolio(
        [event],
        initial_equity=200.0,
        risk_policy=replay_policy,
        dataset_manifest_hash=MANIFEST_HASH,
    )

    assert replay["skipped_signals"][0]["reason"] == expected_reason


def test_replay_rechecks_position_margin_against_current_equity() -> None:
    replay = replay_paper_portfolio(
        [_open("open", "position", "signal")],
        initial_equity=20.0,
        risk_policy=RiskPolicy.v1(),
        dataset_manifest_hash=MANIFEST_HASH,
    )

    assert replay["skipped_signals"][0]["reason"] == (
        "PORTFOLIO_POSITION_MARGIN_CAP_EXCEEDED"
    )


def test_replay_rejects_mutated_execution_plan_material() -> None:
    plan = _plan("signal")
    plan["quantity_contracts"] *= 10
    event = PortfolioEvent(
        event_id="open",
        occurred_at=100,
        event_type="OPEN",
        position_id="position",
        signal_id="signal",
        cluster_id="MEME_HIGH_BETA",
        execution_plan=plan,
    )
    replay = replay_paper_portfolio(
        [event],
        initial_equity=200.0,
        risk_policy=RiskPolicy.v1(),
        dataset_manifest_hash=MANIFEST_HASH,
    )

    assert replay["skipped_signals"][0]["reason"] == (
        "EXECUTION_PLAN_HASH_MISMATCH"
    )


def test_explicit_close_at_liquidation_price_uses_liquidation_semantics() -> None:
    plan = _plan("signal")
    replay = replay_paper_portfolio(
        [
            _open("open", "position", "signal"),
            PortfolioEvent(
                event_id="close",
                occurred_at=110,
                event_type="CLOSE",
                position_id="position",
                price=plan["liquidation_price"] + 1.0,
                exit_cost=0.2,
            ),
        ],
        initial_equity=200.0,
        risk_policy=RiskPolicy.v1(),
        dataset_manifest_hash=MANIFEST_HASH,
    )

    assert replay["closed_positions"][0]["exit_reason"] == "ISOLATED_LIQUIDATION"


def test_exit_bearing_events_require_an_explicit_modeled_cost() -> None:
    with pytest.raises(ValidationError, match="modeled exit cost"):
        PortfolioEvent(
            event_id="close",
            occurred_at=110,
            event_type="CLOSE",
            position_id="position",
            price=100.0,
        )


def test_cost_attribution_sums_raw_costs_before_rounding() -> None:
    second_plan = _plan("signal-2")
    second_plan["cluster_id"] = "OTHER"
    second_plan["execution_plan_hash"] = canonical_sha256(
        {key: value for key, value in second_plan.items() if key != "execution_plan_hash"}
    )
    replay = replay_paper_portfolio(
        [
            _open("open-1", "position-1", "signal-1", occurred_at=100),
            PortfolioEvent(
                event_id="open-2",
                occurred_at=101,
                event_type="OPEN",
                position_id="position-2",
                signal_id="signal-2",
                cluster_id="OTHER",
                execution_plan=second_plan,
            ),
            PortfolioEvent(event_id="close-1", occurred_at=110, event_type="CLOSE", position_id="position-1", price=99, exit_cost=0.000000006),
            PortfolioEvent(event_id="close-2", occurred_at=111, event_type="CLOSE", position_id="position-2", price=99, exit_cost=0.000000006),
        ],
        initial_equity=200,
        risk_policy=RiskPolicy.v1(),
        dataset_manifest_hash=MANIFEST_HASH,
    )

    assert replay["cost_attribution"]["exit_cost"] == 0.00000001


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("entry_cost", -0.01, "entry_cost must be non-negative"),
        ("liquidation_fee_rate", 1.0, "liquidation_fee_rate must be below one"),
    ],
)
def test_rehashed_execution_plan_still_rejects_unsafe_numeric_fields(
    field: str,
    value: float,
    message: str,
) -> None:
    plan = _plan("signal")
    plan[field] = value
    plan["execution_plan_hash"] = canonical_sha256(
        {key: item for key, item in plan.items() if key != "execution_plan_hash"}
    )
    event = PortfolioEvent(
        event_id="open",
        occurred_at=100,
        event_type="OPEN",
        position_id="position",
        signal_id="signal",
        cluster_id="MEME_HIGH_BETA",
        execution_plan=plan,
    )

    with pytest.raises(ValueError, match=message):
        replay_paper_portfolio(
            [event],
            initial_equity=200,
            risk_policy=RiskPolicy.v1(),
            dataset_manifest_hash=MANIFEST_HASH,
        )


def test_funding_moves_isolated_margin_and_liquidation_threshold() -> None:
    plan = _plan("signal")
    replay = replay_paper_portfolio(
        [
            _open("open", "position", "signal"),
            PortfolioEvent(
                event_id="funding",
                occurred_at=110,
                event_type="FUNDING",
                position_id="position",
                amount=-0.25,
            ),
        ],
        initial_equity=200.0,
        risk_policy=RiskPolicy.v1(),
        dataset_manifest_hash=MANIFEST_HASH,
    )

    open_position = replay["open_positions"][0]
    assert open_position["funding"] == -0.25
    assert open_position["liquidation_price"] < plan["liquidation_price"]


def test_signal_report_validates_lineage_timestamp_and_detaches_rows() -> None:
    source = [{"signal_id": "signal", "signal_triggered_at": 100}]
    report = build_signal_level_research_report(
        source,
        dataset_manifest_hash=MANIFEST_HASH,
    )
    source[0]["signal_id"] = "mutated"

    assert report["rows"][0]["signal_id"] == "signal"
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        build_signal_level_research_report(source, dataset_manifest_hash="invalid")
    fractional = [{"signal_id": "signal", "signal_triggered_at": 100.5}]
    with pytest.raises(ValueError, match="non-negative integer"):
        build_signal_level_research_report(
            fractional,
            dataset_manifest_hash=MANIFEST_HASH,
        )
