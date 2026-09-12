from __future__ import annotations

import pytest
from pydantic import ValidationError

from waterfallhunter.core.execution_planning import (
    ConstraintError,
    ExecutionCostPolicy,
    LBankRiskParameters,
    MaintenanceMarginTier,
    OpenExposure,
    PortfolioCapacity,
    PriceBand,
    RawShortLevels,
    RiskPolicy,
    SafeLeverageBounds,
    ValidatedMarketConstraints,
    build_short_paper_execution_plan,
    conservative_short_levels,
    isolated_short_liquidation_price,
    tiered_isolated_short_liquidation,
    validated_constraints_from_lbank_observation,
)


def _constraints(**overrides) -> ValidatedMarketConstraints:
    values = {
        "canonical_symbol": "TEST-USDT-PERP",
        "venue_symbol": "TEST/USDT:USDT",
        "tick_size": 0.1,
        "quantity_step": 0.001,
        "contract_size": 1.0,
        "min_quantity": 0.001,
        "min_notional": 5.0,
        "maximum_leverage": 20.0,
        "price_band": PriceBand(minimum=1.0, maximum=1_000.0),
        "maintenance_margin_tiers": (
            MaintenanceMarginTier(
                notional_floor=0.0,
                notional_cap=None,
                maintenance_margin_rate=0.005,
            ),
        ),
        "liquidation_fee_rate": 0.002,
        "funding_semantics": "timestamped_8h",
        "constraints_observed_at": 100,
        "expires_at": 200,
    }
    return ValidatedMarketConstraints.create(**{**values, **overrides})


def _costs(**overrides) -> ExecutionCostPolicy:
    values = {
        "taker_fee_rate": 0.0006,
        "maker_fee_rate": 0.0002,
        "entry_slippage_p95_rate": 0.0005,
        "exit_slippage_p95_rate": 0.0007,
        "expected_funding_rate": 0.0001,
        "liquidation_buffer_rate": 0.02,
    }
    return ExecutionCostPolicy.create(**{**values, **overrides})


def _leverage_bounds(**overrides) -> SafeLeverageBounds:
    values = {
        "exchange_max": 20.0,
        "maintenance_liquidation": 10.0,
        "volatility": 6.0,
        "liquidity_slippage": 5.0,
        "evidence_uncertainty": 4.0,
        "portfolio_open_risk": 3.8,
    }
    return SafeLeverageBounds.create(**{**values, **overrides})


def _plan(**overrides):
    constraints = overrides.pop("constraints", _constraints())
    values = {
        "signal_id": "signal-1",
        "cluster_id": "MEME_HIGH_BETA",
        "evaluation_time": 150,
        "constraints": constraints,
        "expected_constraints_hash": constraints.constraints_hash,
        "cost_policy": _costs(),
        "risk_policy": RiskPolicy.v1(),
        "portfolio": PortfolioCapacity(cash_equity=200.0, unrealized_pnl=0.0),
        "leverage_bounds": _leverage_bounds(),
        "raw_levels": RawShortLevels(
            entry=100.09,
            tp1=98.01,
            tp2=96.01,
            stop=101.51,
        ),
    }
    return build_short_paper_execution_plan(**{**values, **overrides})


def test_lbank_constraints_are_content_addressed_fresh_and_tamper_evident() -> None:
    constraints = _constraints()
    constraints.require_usable(
        evaluation_time=150,
        expected_hash=constraints.constraints_hash,
    )
    with pytest.raises(ConstraintError, match="STALE"):
        constraints.require_usable(
            evaluation_time=200,
            expected_hash=constraints.constraints_hash,
        )
    with pytest.raises(ConstraintError, match="HASH_MISMATCH"):
        constraints.require_usable(evaluation_time=150, expected_hash="0" * 64)

    tampered = constraints.model_dump(mode="json")
    tampered["min_notional"] = 1.0
    with pytest.raises(ValidationError, match="constraints_hash"):
        ValidatedMarketConstraints.model_validate(tampered)


def test_public_lbank_filters_require_separate_fresh_hash_bound_risk_packet() -> None:
    observation = {
        "available": True,
        "source_exchange": "lbank",
        "symbol": "TEST/USDT:USDT",
        "observed_at": 990,
        "midpoint": 100.0,
        "mark_price": 100.0,
        "market_filters": {
            "price_tick": 0.1,
            "quantity_step": 0.001,
            "contract_size": 1.0,
            "minimum_amount": 0.001,
            "effective_min_notional": 5.0,
            "maximum_leverage": 20.0,
            "price_limit_lower_rate": 0.05,
            "price_limit_upper_rate": 0.05,
            "price_limit_semantics": "relative_to_reference_fraction",
        },
    }
    risk = LBankRiskParameters.create(
        canonical_symbol="TEST-USDT-PERP",
        maintenance_margin_tiers=(
            MaintenanceMarginTier(
                notional_floor=0.0,
                maintenance_margin_rate=0.005,
            ),
        ),
        liquidation_fee_rate=0.002,
        funding_semantics="timestamped_8h",
        source_reference="lbank-risk-rules:TESTUSDT:v1",
        source_payload_hash="a" * 64,
        observed_at=980,
        expires_at=1_100,
    )
    constraints = validated_constraints_from_lbank_observation(
        canonical_symbol="TEST-USDT-PERP",
        observation=observation,
        risk_parameters=risk,
        expected_risk_parameters_hash=risk.parameters_hash,
        evaluation_time=1_000,
    )

    assert constraints.price_band == PriceBand(minimum=95.0, maximum=105.0)
    assert constraints.maximum_leverage == 20.0
    assert len(constraints.constraints_hash) == 64
    tampered_risk = risk.model_copy(update={"expires_at": 999})
    with pytest.raises(ConstraintError, match="RISK_PARAMETERS_TAMPERED"):
        validated_constraints_from_lbank_observation(
            canonical_symbol="TEST-USDT-PERP",
            observation=observation,
            risk_parameters=tampered_risk,
            expected_risk_parameters_hash=risk.parameters_hash,
            evaluation_time=1_000,
        )


def test_short_rounding_is_conservative_and_revalidates_level_order() -> None:
    levels = conservative_short_levels(
        entry=100.09,
        tp1=98.01,
        tp2=96.01,
        stop=101.51,
        tick_size=0.1,
    )
    assert levels == {"entry": 100.0, "tp1": 98.1, "tp2": 96.1, "stop": 101.6}
    with pytest.raises(ConstraintError, match="LEVEL_ORDER"):
        conservative_short_levels(
            entry=100.01,
            tp1=99.99,
            tp2=99.98,
            stop=100.02,
            tick_size=0.1,
        )


def test_risk_first_plan_is_cost_complete_and_33_percent_is_only_a_cap() -> None:
    plan = _plan()

    assert plan["status"] == "READY"
    assert plan["execution_mode"] == "SIGNAL_ONLY"
    assert plan["system_leverage"] == 3
    assert plan["isolated_margin"] < 200.0 * 0.33
    assert plan["risk_at_stop_rate"] <= 0.0075
    assert plan["risk_at_stop_rate"] <= 0.006
    assert plan["rounding_policy_version"] == "short_rounding_policy_v1"
    assert plan["raw_safe_leverage"] == 3.8
    assert plan["leverage_bounds_hash"] == _leverage_bounds().bounds_hash
    assert plan["net_tp2_pnl"] < plan["gross_tp2_pnl"]
    assert plan["total_tp2_cost"] > 0
    assert plan["risk_at_stop"] == pytest.approx(
        plan["gross_stop_loss"]
        + plan["entry_cost"]
        + plan["stop_exit_cost"]
        + plan["expected_funding_cost"],
        abs=1e-7,
    )
    assert plan["net_reward_risk"] >= 1.5
    assert plan["constraints_hash"] == _constraints().constraints_hash
    assert plan["strategy_equivalent"] is False


def test_safe_leverage_below_three_is_blocked_instead_of_clamped_up() -> None:
    plan = _plan(
        leverage_bounds=_leverage_bounds(portfolio_open_risk=2.99),
    )

    assert plan["status"] == "BLOCKED"
    assert plan["reason_codes"] == ["ENTRY_BLOCKED_RISK_BUDGET"]
    assert plan["raw_safe_leverage"] == 2.99
    assert plan["system_leverage"] is None


def test_every_independent_leverage_bound_is_hash_bound_and_enforced() -> None:
    bounds = _leverage_bounds(liquidity_slippage=2.5)
    plan = _plan(leverage_bounds=bounds)

    assert bounds.safe_leverage == 2.5
    assert plan["status"] == "BLOCKED"
    assert plan["reason_codes"] == ["ENTRY_BLOCKED_RISK_BUDGET"]
    tampered = bounds.model_dump(mode="json")
    tampered["volatility"] = 1_000.0
    with pytest.raises(ValidationError, match="bounds hash"):
        SafeLeverageBounds.model_validate(tampered)


def test_requested_risk_cannot_exceed_hard_policy_cap() -> None:
    plan = _plan(requested_risk_rate=0.0076)

    assert plan["status"] == "BLOCKED"
    assert plan["reason_codes"] == ["RISK_RATE_OUTSIDE_POLICY"]


def test_portfolio_cluster_and_total_risk_caps_are_hard_gates() -> None:
    portfolio = PortfolioCapacity(
        cash_equity=200.0,
        unrealized_pnl=20.0,
        open_exposures=(
            OpenExposure(
                position_id="open-1",
                cluster_id="MEME_HIGH_BETA",
                locked_isolated_margin=20.0,
                risk_at_stop=2.0,
            ),
        ),
    )
    plan = _plan(portfolio=portfolio)

    assert portfolio.conservative_sizing_equity == 200.0
    assert plan["status"] == "BLOCKED"
    assert "PORTFOLIO_CLUSTER_RISK_CAP_EXCEEDED" in plan["reason_codes"]


def test_unrealized_losses_reduce_sizing_equity_and_minimum_notional_cannot_force_risk() -> None:
    portfolio = PortfolioCapacity(cash_equity=200.0, unrealized_pnl=-50.0)
    constraints = _constraints(min_notional=500.0)
    plan = _plan(portfolio=portfolio, constraints=constraints)

    assert portfolio.conservative_sizing_equity == 150.0
    assert plan["status"] == "BLOCKED"
    assert plan["reason_codes"] == ["EXECUTION_MIN_NOTIONAL_UNSAFE"]


def test_isolated_short_liquidation_model_is_monotonic_in_margin() -> None:
    lower = isolated_short_liquidation_price(
        entry_price=100.0,
        quantity=1.0,
        contract_size=1.0,
        isolated_margin=20.0,
        maintenance_margin_rate=0.005,
        liquidation_fee_rate=0.002,
    )
    higher = isolated_short_liquidation_price(
        entry_price=100.0,
        quantity=1.0,
        contract_size=1.0,
        isolated_margin=30.0,
        maintenance_margin_rate=0.005,
        liquidation_fee_rate=0.002,
    )

    assert higher > lower > 100.0


def test_hashes_are_recomputed_at_consumption_and_integer_factories_normalize() -> None:
    constraints = _constraints().model_copy(update={"min_notional": 1.0})
    plan = _plan(
        constraints=constraints,
        expected_constraints_hash=constraints.constraints_hash,
    )
    integer_bounds = SafeLeverageBounds.create(
        exchange_max=20,
        maintenance_liquidation=10,
        volatility=6,
        liquidity_slippage=5,
        evidence_uncertainty=4,
        portfolio_open_risk=3,
    )
    integer_risk = LBankRiskParameters.create(
        canonical_symbol="TEST-USDT-PERP",
        maintenance_margin_tiers=(
            MaintenanceMarginTier(
                notional_floor=0,
                maintenance_margin_rate=0,
            ),
        ),
        liquidation_fee_rate=0,
        funding_semantics="timestamped_8h",
        source_reference="test",
        source_payload_hash="a" * 64,
        observed_at=100,
        expires_at=200,
    )

    assert plan["status"] == "BLOCKED"
    assert plan["reason_codes"] == ["EXECUTION_CONSTRAINTS_TAMPERED"]
    integer_bounds.require_integrity()
    integer_risk.require_usable(
        evaluation_time=150,
        expected_hash=integer_risk.parameters_hash,
    )
    with pytest.raises(ConstraintError, match="RISK_PARAMETER_HASH_MISMATCH"):
        integer_risk.require_usable(
            evaluation_time=150,
            expected_hash="0" * 64,
        )


def test_maintenance_tiers_must_partition_notional_and_liquidation_uses_exit_tier() -> None:
    tiers = (
        MaintenanceMarginTier(
            notional_floor=0.0,
            notional_cap=110.0,
            maintenance_margin_rate=0.005,
        ),
        MaintenanceMarginTier(
            notional_floor=110.0,
            maintenance_margin_rate=0.02,
        ),
    )
    price, rate = tiered_isolated_short_liquidation(
        entry_price=100.0,
        quantity=1.0,
        contract_size=1.0,
        isolated_margin=20.0,
        maintenance_margin_tiers=tiers,
        liquidation_fee_rate=0.0,
    )

    assert price == pytest.approx(120.0 / 1.02)
    assert rate == 0.02
    overlapping_tiers = (
        MaintenanceMarginTier(
            notional_floor=0.0,
            maintenance_margin_rate=0.005,
        ),
        MaintenanceMarginTier(
            notional_floor=100.0,
            maintenance_margin_rate=0.01,
        ),
    )
    with pytest.raises(ValidationError, match="only the final"):
        _constraints(maintenance_margin_tiers=overlapping_tiers)


def test_fractional_future_observation_fails_closed_and_mark_price_anchors_band() -> None:
    observation = {
        "available": True,
        "source_exchange": "lbank",
        "symbol": "TEST/USDT:USDT",
        "observed_at": 1_000.5,
        "midpoint": 100.0,
        "mark_price": 110.0,
        "market_filters": {
            "price_tick": 0.1,
            "quantity_step": 0.001,
            "contract_size": 1.0,
            "minimum_amount": 0.001,
            "effective_min_notional": 5.0,
            "maximum_leverage": 20.0,
            "price_limit_lower_rate": 0.05,
            "price_limit_upper_rate": 0.05,
            "price_limit_semantics": "relative_to_reference_fraction",
        },
    }
    risk = LBankRiskParameters.create(
        canonical_symbol="TEST-USDT-PERP",
        maintenance_margin_tiers=_constraints().maintenance_margin_tiers,
        liquidation_fee_rate=0.002,
        funding_semantics="timestamped_8h",
        source_reference="test",
        source_payload_hash="a" * 64,
        observed_at=900,
        expires_at=1_100,
    )
    with pytest.raises(ConstraintError, match="CONSTRAINTS_STALE"):
        validated_constraints_from_lbank_observation(
            canonical_symbol="TEST-USDT-PERP",
            observation=observation,
            risk_parameters=risk,
            expected_risk_parameters_hash=risk.parameters_hash,
            evaluation_time=1_000,
        )

    observation["observed_at"] = 1_000.0
    constraints = validated_constraints_from_lbank_observation(
        canonical_symbol="TEST-USDT-PERP",
        observation=observation,
        risk_parameters=risk,
        expected_risk_parameters_hash=risk.parameters_hash,
        evaluation_time=1_000,
    )
    assert constraints.price_band == PriceBand(minimum=104.5, maximum=115.5)


def test_negative_funding_rate_is_a_short_cost() -> None:
    plan = _plan(cost_policy=_costs(expected_funding_rate=-0.001))

    assert plan["status"] == "READY"
    assert plan["expected_funding_cost"] > 0


def test_ready_execution_plan_exposes_authoritative_constraints_expiry() -> None:
    constraints = _constraints(expires_at=250)
    plan = _plan(constraints=constraints, expected_constraints_hash=constraints.constraints_hash)
    assert plan["status"] == "READY"
    assert plan["expires_at"] == 250
