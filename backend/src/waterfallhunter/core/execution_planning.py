"""Hash-bound LBank constraints and risk-first SIGNAL_ONLY planning."""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from waterfallhunter.core.signal_metadata import canonical_sha256

SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"


class ConstraintError(ValueError):
    """Raised when venue constraints are unavailable, stale, or inconsistent."""


class PriceBand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum: float = Field(gt=0, allow_inf_nan=False)
    maximum: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ordered(self) -> "PriceBand":
        if self.minimum >= self.maximum:
            raise ValueError("price band minimum must be below maximum")
        return self


class MaintenanceMarginTier(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    notional_floor: float = Field(ge=0, allow_inf_nan=False)
    notional_cap: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    maintenance_margin_rate: float = Field(ge=0, lt=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ordered(self) -> "MaintenanceMarginTier":
        if self.notional_cap is not None and self.notional_cap <= self.notional_floor:
            raise ValueError("maintenance tier cap must exceed its floor")
        return self


def _validate_maintenance_tier_partition(
    tiers: tuple[MaintenanceMarginTier, ...],
) -> None:
    if not tiers or tiers[0].notional_floor != 0:
        raise ValueError("maintenance tiers must begin at zero notional")
    for index, tier in enumerate(tiers):
        is_final = index == len(tiers) - 1
        if tier.notional_cap is None and not is_final:
            raise ValueError("only the final maintenance tier may be uncapped")
        if not is_final and tier.notional_cap != tiers[index + 1].notional_floor:
            raise ValueError("maintenance tiers must be contiguous and non-overlapping")
    if tiers[-1].notional_cap is not None:
        raise ValueError("final maintenance tier must be uncapped")


class ValidatedMarketConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["validated_lbank_constraints_v1"]
    venue: Literal["LBANK"]
    canonical_symbol: str = Field(min_length=1)
    venue_symbol: str = Field(min_length=1)
    contract_type: Literal["LINEAR_PERPETUAL"]
    quote_asset: Literal["USDT"]
    margin_asset: Literal["USDT"]
    status: Literal["ACTIVE"]
    tick_size: float = Field(gt=0, allow_inf_nan=False)
    quantity_step: float = Field(gt=0, allow_inf_nan=False)
    contract_size: float = Field(gt=0, allow_inf_nan=False)
    min_quantity: float = Field(gt=0, allow_inf_nan=False)
    min_notional: float = Field(gt=0, allow_inf_nan=False)
    maximum_leverage: float = Field(gt=0, allow_inf_nan=False)
    price_band: PriceBand
    maintenance_margin_tiers: tuple[MaintenanceMarginTier, ...]
    liquidation_fee_rate: float = Field(ge=0, lt=1, allow_inf_nan=False)
    funding_semantics: str = Field(min_length=1)
    constraints_observed_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)
    constraints_hash: str = Field(pattern=SHA256_HEX_PATTERN)

    @model_validator(mode="after")
    def _validate_contract(self, info: ValidationInfo) -> "ValidatedMarketConstraints":
        if self.expires_at <= self.constraints_observed_at:
            raise ValueError("constraints expiry must follow observation time")
        if not self.maintenance_margin_tiers:
            raise ValueError("at least one maintenance margin tier is required")
        ordered = sorted(
            self.maintenance_margin_tiers,
            key=lambda tier: tier.notional_floor,
        )
        if tuple(ordered) != self.maintenance_margin_tiers:
            raise ValueError("maintenance margin tiers must be ordered")
        _validate_maintenance_tier_partition(self.maintenance_margin_tiers)
        if (
            not (info.context or {}).get("skip_hash")
            and self.constraints_hash != canonical_sha256(self.hash_material())
        ):
            raise ValueError("constraints_hash does not match canonical material")
        return self

    def hash_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"constraints_hash"})

    def require_usable(self, *, evaluation_time: int, expected_hash: str) -> None:
        if isinstance(evaluation_time, bool) or not isinstance(evaluation_time, int):
            raise ConstraintError("EXECUTION_EVALUATION_CLOCK_INVALID")
        actual_hash = canonical_sha256(self.hash_material())
        if actual_hash != self.constraints_hash:
            raise ConstraintError("EXECUTION_CONSTRAINTS_TAMPERED")
        if expected_hash != actual_hash:
            raise ConstraintError("EXECUTION_CONSTRAINT_HASH_MISMATCH")
        if evaluation_time < self.constraints_observed_at:
            raise ConstraintError("EXECUTION_CONSTRAINTS_FROM_FUTURE")
        if evaluation_time >= self.expires_at:
            raise ConstraintError("EXECUTION_CONSTRAINTS_STALE")

    def maintenance_rate(self, notional: float) -> float:
        for tier in self.maintenance_margin_tiers:
            if notional >= tier.notional_floor and (
                tier.notional_cap is None or notional < tier.notional_cap
            ):
                return tier.maintenance_margin_rate
        raise ConstraintError("EXECUTION_MAINTENANCE_TIER_UNAVAILABLE")

    @classmethod
    def create(cls, **material: Any) -> "ValidatedMarketConstraints":
        payload = {
            "contract_version": "validated_lbank_constraints_v1",
            "venue": "LBANK",
            "contract_type": "LINEAR_PERPETUAL",
            "quote_asset": "USDT",
            "margin_asset": "USDT",
            "status": "ACTIVE",
            **material,
        }
        normalized = cls.model_validate(
            {**payload, "constraints_hash": "0" * 64},
            context={"skip_hash": True},
        )
        body = normalized.model_dump(mode="json", exclude={"constraints_hash"})
        return cls.model_validate({**body, "constraints_hash": canonical_sha256(body)})


class ExecutionCostPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["execution_cost_policy_v1"] = "execution_cost_policy_v1"
    taker_fee_rate: float = Field(ge=0, lt=0.1, allow_inf_nan=False)
    maker_fee_rate: float = Field(ge=0, lt=0.1, allow_inf_nan=False)
    entry_slippage_p95_rate: float = Field(ge=0, lt=0.1, allow_inf_nan=False)
    exit_slippage_p95_rate: float = Field(ge=0, lt=0.1, allow_inf_nan=False)
    expected_funding_rate: float = Field(ge=-0.1, lt=0.1, allow_inf_nan=False)
    liquidation_buffer_rate: float = Field(ge=0, lt=1, allow_inf_nan=False)
    policy_hash: str = Field(pattern=SHA256_HEX_PATTERN)

    @model_validator(mode="after")
    def _hash_matches(self, info: ValidationInfo) -> "ExecutionCostPolicy":
        material = self.model_dump(mode="json", exclude={"policy_hash"})
        if (
            not (info.context or {}).get("skip_hash")
            and self.policy_hash != canonical_sha256(material)
        ):
            raise ValueError("execution cost policy hash mismatch")
        return self

    def require_integrity(self) -> None:
        material = self.model_dump(mode="json", exclude={"policy_hash"})
        if self.policy_hash != canonical_sha256(material):
            raise ConstraintError("EXECUTION_COST_POLICY_TAMPERED")

    @classmethod
    def create(cls, **values: Any) -> "ExecutionCostPolicy":
        normalized = cls.model_validate(
            {
                "contract_version": "execution_cost_policy_v1",
                "policy_hash": "0" * 64,
                **values,
            },
            context={"skip_hash": True},
        )
        material = normalized.model_dump(mode="json", exclude={"policy_hash"})
        return cls.model_validate(
            {**material, "policy_hash": canonical_sha256(material)}
        )


class LBankRiskParameters(BaseModel):
    """Hash-bound risk fields not exposed by LBank's public instrument packet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["lbank_risk_parameters_v1"]
    canonical_symbol: str = Field(min_length=1)
    maintenance_margin_tiers: tuple[MaintenanceMarginTier, ...]
    liquidation_fee_rate: float = Field(ge=0, lt=1, allow_inf_nan=False)
    funding_semantics: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    source_payload_hash: str = Field(pattern=SHA256_HEX_PATTERN)
    observed_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)
    parameters_hash: str = Field(pattern=SHA256_HEX_PATTERN)

    @model_validator(mode="after")
    def _validate_parameters(self, info: ValidationInfo) -> "LBankRiskParameters":
        if not self.maintenance_margin_tiers:
            raise ValueError("LBank risk parameters require maintenance tiers")
        _validate_maintenance_tier_partition(self.maintenance_margin_tiers)
        if self.expires_at <= self.observed_at:
            raise ValueError("LBank risk parameter expiry must follow observation")
        material = self.model_dump(mode="json", exclude={"parameters_hash"})
        if (
            not (info.context or {}).get("skip_hash")
            and self.parameters_hash != canonical_sha256(material)
        ):
            raise ValueError("LBank risk parameters hash mismatch")
        return self

    def require_usable(self, *, evaluation_time: int, expected_hash: str) -> None:
        material = self.model_dump(mode="json", exclude={"parameters_hash"})
        actual_hash = canonical_sha256(material)
        if actual_hash != self.parameters_hash:
            raise ConstraintError("EXECUTION_RISK_PARAMETERS_TAMPERED")
        if expected_hash != actual_hash:
            raise ConstraintError("EXECUTION_RISK_PARAMETER_HASH_MISMATCH")
        if evaluation_time < self.observed_at or evaluation_time >= self.expires_at:
            raise ConstraintError("EXECUTION_RISK_PARAMETERS_STALE")

    @classmethod
    def create(cls, **values: Any) -> "LBankRiskParameters":
        normalized = cls.model_validate(
            {
                "contract_version": "lbank_risk_parameters_v1",
                "parameters_hash": "0" * 64,
                **values,
            },
            context={"skip_hash": True},
        )
        material = normalized.model_dump(mode="json", exclude={"parameters_hash"})
        return cls.model_validate(
            {**material, "parameters_hash": canonical_sha256(material)}
        )


def validated_constraints_from_lbank_observation(
    *,
    canonical_symbol: str,
    observation: dict[str, Any],
    risk_parameters: LBankRiskParameters,
    expected_risk_parameters_hash: str,
    evaluation_time: int,
    max_observation_age_seconds: int = 60,
) -> ValidatedMarketConstraints:
    """Combine public LBank filters with separately proven risk parameters."""
    observed_at, filters = _validated_public_lbank_observation(
        observation,
        evaluation_time=evaluation_time,
        max_observation_age_seconds=max_observation_age_seconds,
    )
    risk_parameters.require_usable(
        evaluation_time=evaluation_time,
        expected_hash=expected_risk_parameters_hash,
    )
    if risk_parameters.canonical_symbol != canonical_symbol:
        raise ConstraintError("EXECUTION_RISK_PARAMETER_SYMBOL_MISMATCH")

    mark_price = _positive_number(
        observation.get("mark_price"),
        unavailable_code="EXECUTION_MARK_PRICE_UNAVAILABLE",
    )
    lower_rate = _positive_filter(filters, "price_limit_lower_rate")
    upper_rate = _positive_filter(filters, "price_limit_upper_rate")
    if lower_rate >= 1 or upper_rate >= 1:
        raise ConstraintError("EXECUTION_PRICE_BAND_INVALID")
    venue_symbol = str(observation.get("symbol") or "").strip()
    if not venue_symbol:
        raise ConstraintError("EXECUTION_VENUE_SYMBOL_UNAVAILABLE")
    return ValidatedMarketConstraints.create(
        canonical_symbol=canonical_symbol,
        venue_symbol=venue_symbol,
        tick_size=_positive_filter(filters, "price_tick"),
        quantity_step=_positive_filter(filters, "quantity_step"),
        contract_size=_positive_filter(filters, "contract_size"),
        min_quantity=_positive_filter(filters, "minimum_amount"),
        min_notional=_positive_filter(filters, "effective_min_notional"),
        maximum_leverage=_positive_filter(filters, "maximum_leverage"),
        price_band=PriceBand(
            minimum=mark_price * (1.0 - lower_rate),
            maximum=mark_price * (1.0 + upper_rate),
        ),
        maintenance_margin_tiers=risk_parameters.maintenance_margin_tiers,
        liquidation_fee_rate=risk_parameters.liquidation_fee_rate,
        funding_semantics=risk_parameters.funding_semantics,
        constraints_observed_at=min(observed_at, risk_parameters.observed_at),
        expires_at=min(
            observed_at + max_observation_age_seconds + 1,
            risk_parameters.expires_at,
        ),
    )


def _validated_public_lbank_observation(
    observation: dict[str, Any],
    *,
    evaluation_time: int,
    max_observation_age_seconds: int,
) -> tuple[int, dict[str, Any]]:
    if observation.get("available") is not True:
        raise ConstraintError("EXECUTION_LEVELS_UNAVAILABLE")
    if str(observation.get("source_exchange") or "").lower() != "lbank":
        raise ConstraintError("EXECUTION_CONSTRAINT_SOURCE_NOT_LBANK")
    observed_at_raw = observation.get("observed_at")
    if (
        isinstance(observed_at_raw, bool)
        or not isinstance(observed_at_raw, (int, float))
    ):
        raise ConstraintError("EXECUTION_CONSTRAINT_TIMESTAMP_UNAVAILABLE")
    observed_at_number = float(observed_at_raw)
    if not math.isfinite(observed_at_number):
        raise ConstraintError("EXECUTION_CONSTRAINT_TIMESTAMP_UNAVAILABLE")
    if (
        evaluation_time < observed_at_number
        or evaluation_time - observed_at_number > max_observation_age_seconds
    ):
        raise ConstraintError("EXECUTION_CONSTRAINTS_STALE")
    observed_at = math.ceil(observed_at_number)
    filters = observation.get("market_filters")
    if not isinstance(filters, dict):
        raise ConstraintError("EXECUTION_MARKET_FILTERS_UNAVAILABLE")
    if filters.get("price_limit_semantics") != "relative_to_reference_fraction":
        raise ConstraintError("EXECUTION_PRICE_BAND_SEMANTICS_UNAVAILABLE")
    return observed_at, filters


def _positive_number(value: Any, *, unavailable_code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConstraintError(unavailable_code)
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ConstraintError(unavailable_code)
    return number


def _positive_filter(filters: dict[str, Any], name: str) -> float:
    return _positive_number(
        filters.get(name),
        unavailable_code=f"EXECUTION_{name.upper()}_UNAVAILABLE",
    )


class SafeLeverageBounds(BaseModel):
    """Independent leverage ceilings; the planner may only use their minimum."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["safe_leverage_bounds_v1"] = "safe_leverage_bounds_v1"
    exchange_max: float = Field(gt=0, allow_inf_nan=False)
    maintenance_liquidation: float = Field(gt=0, allow_inf_nan=False)
    volatility: float = Field(gt=0, allow_inf_nan=False)
    liquidity_slippage: float = Field(gt=0, allow_inf_nan=False)
    evidence_uncertainty: float = Field(gt=0, allow_inf_nan=False)
    portfolio_open_risk: float = Field(gt=0, allow_inf_nan=False)
    product_max: float = Field(default=20.0, gt=0, le=20, allow_inf_nan=False)
    bounds_hash: str = Field(pattern=SHA256_HEX_PATTERN)

    @model_validator(mode="after")
    def _hash_matches(self, info: ValidationInfo) -> "SafeLeverageBounds":
        material = self.model_dump(mode="json", exclude={"bounds_hash"})
        if (
            not (info.context or {}).get("skip_hash")
            and self.bounds_hash != canonical_sha256(material)
        ):
            raise ValueError("safe leverage bounds hash mismatch")
        return self

    @property
    def safe_leverage(self) -> float:
        return min(
            self.exchange_max,
            self.maintenance_liquidation,
            self.volatility,
            self.liquidity_slippage,
            self.evidence_uncertainty,
            self.portfolio_open_risk,
            self.product_max,
        )

    def require_integrity(self) -> None:
        material = self.model_dump(mode="json", exclude={"bounds_hash"})
        if self.bounds_hash != canonical_sha256(material):
            raise ConstraintError("EXECUTION_LEVERAGE_BOUNDS_TAMPERED")

    @classmethod
    def create(cls, **values: Any) -> "SafeLeverageBounds":
        validated = cls.model_validate(
            {
                "contract_version": "safe_leverage_bounds_v1",
                "bounds_hash": "0" * 64,
                **values,
            },
            context={"skip_hash": True},
        )
        normalized = validated.model_dump(mode="json", exclude={"bounds_hash"})
        return cls.model_validate(
            {**normalized, "bounds_hash": canonical_sha256(normalized)}
        )


class OpenExposure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    position_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    locked_isolated_margin: float = Field(ge=0, allow_inf_nan=False)
    risk_at_stop: float = Field(ge=0, allow_inf_nan=False)


class PortfolioCapacity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cash_equity: float = Field(gt=0, allow_inf_nan=False)
    unrealized_pnl: float = Field(allow_inf_nan=False)
    open_exposures: tuple[OpenExposure, ...] = ()

    @property
    def conservative_sizing_equity(self) -> float:
        return self.cash_equity + min(self.unrealized_pnl, 0.0)


class RiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["paper_risk_policy_v1"] = "paper_risk_policy_v1"
    minimum_product_leverage: float = 3.0
    maximum_system_leverage: float = 20.0
    max_open_positions: int = 3
    max_margin_per_position_rate: float = 0.33
    max_total_locked_margin_rate: float = 0.70
    minimum_free_reserve_rate: float = 0.25
    default_risk_at_stop_per_position_rate: float = 0.006
    max_risk_at_stop_per_position_rate: float = 0.0075
    max_total_open_risk_rate: float = 0.02
    max_correlated_cluster_risk_rate: float = 0.0125
    minimum_net_reward_risk: float = 1.5
    policy_hash: str = Field(pattern=SHA256_HEX_PATTERN)

    @model_validator(mode="after")
    def _hash_matches(self, info: ValidationInfo) -> "RiskPolicy":
        material = self.model_dump(mode="json", exclude={"policy_hash"})
        if (
            not (info.context or {}).get("skip_hash")
            and self.policy_hash != canonical_sha256(material)
        ):
            raise ValueError("risk policy hash mismatch")
        return self

    def require_integrity(self) -> None:
        material = self.model_dump(mode="json", exclude={"policy_hash"})
        if self.policy_hash != canonical_sha256(material):
            raise ConstraintError("EXECUTION_RISK_POLICY_TAMPERED")

    @classmethod
    def create(cls, **overrides: Any) -> "RiskPolicy":
        normalized = cls.model_validate(
            {"policy_hash": "0" * 64, **overrides},
            context={"skip_hash": True},
        )
        material = normalized.model_dump(mode="json", exclude={"policy_hash"})
        return cls.model_validate({**material, "policy_hash": canonical_sha256(material)})

    @classmethod
    def v1(cls) -> "RiskPolicy":
        return cls.create()


class RawShortLevels(BaseModel):
    """Unrounded strategy levels supplied to the conservative execution matrix."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry: float = Field(gt=0, allow_inf_nan=False)
    tp1: float = Field(gt=0, allow_inf_nan=False)
    tp2: float = Field(gt=0, allow_inf_nan=False)
    stop: float = Field(gt=0, allow_inf_nan=False)


def _decimal(value: float) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("execution value must be finite")
    return Decimal(str(value))


def _round_step(value: float, step: float, rounding: str) -> float:
    units = (_decimal(value) / _decimal(step)).to_integral_value(rounding=rounding)
    return float(units * _decimal(step))


def conservative_short_levels(
    *,
    entry: float,
    tp1: float,
    tp2: float,
    stop: float,
    tick_size: float,
) -> dict[str, float]:
    levels = {
        "entry": round_execution_level(
            value=entry,
            step=tick_size,
            side="SHORT",
            purpose="ENTRY",
            order_type="MARKET_SIMULATED",
        ),
        "tp1": round_execution_level(
            value=tp1,
            step=tick_size,
            side="SHORT",
            purpose="TAKE_PROFIT",
            order_type="LIMIT",
        ),
        "tp2": round_execution_level(
            value=tp2,
            step=tick_size,
            side="SHORT",
            purpose="TAKE_PROFIT",
            order_type="LIMIT",
        ),
        "stop": round_execution_level(
            value=stop,
            step=tick_size,
            side="SHORT",
            purpose="STOP_LOSS",
            order_type="STOP_MARKET_SIMULATED",
        ),
    }
    if not 0 < levels["tp2"] < levels["tp1"] < levels["entry"] < levels["stop"]:
        raise ConstraintError("EXECUTION_LEVEL_ORDER_INVALID_AFTER_ROUNDING")
    return levels


def round_execution_level(
    *,
    value: float,
    step: float,
    side: Literal["SHORT"],
    purpose: Literal["ENTRY", "TAKE_PROFIT", "STOP_LOSS"],
    order_type: Literal["MARKET_SIMULATED", "LIMIT", "STOP_MARKET_SIMULATED"],
) -> float:
    """Versioned v1 short rounding matrix; every branch is conservative."""
    expected_order_type = {
        "ENTRY": "MARKET_SIMULATED",
        "TAKE_PROFIT": "LIMIT",
        "STOP_LOSS": "STOP_MARKET_SIMULATED",
    }[purpose]
    if side != "SHORT" or order_type != expected_order_type:
        raise ConstraintError("EXECUTION_ROUNDING_POLICY_UNAVAILABLE")
    rounding = ROUND_FLOOR if purpose == "ENTRY" else ROUND_CEILING
    return _round_step(value, step, rounding)


def isolated_short_liquidation_price(
    *,
    entry_price: float,
    quantity: float,
    contract_size: float,
    isolated_margin: float,
    maintenance_margin_rate: float,
    liquidation_fee_rate: float,
) -> float:
    exposure_units = quantity * contract_size
    if exposure_units <= 0 or isolated_margin <= 0:
        raise ValueError("positive isolated exposure and margin are required")
    return (
        isolated_margin + exposure_units * entry_price
    ) / (
        exposure_units * (1.0 + maintenance_margin_rate + liquidation_fee_rate)
    )


def tiered_isolated_short_liquidation(
    *,
    entry_price: float,
    quantity: float,
    contract_size: float,
    isolated_margin: float,
    maintenance_margin_tiers: tuple[MaintenanceMarginTier, ...],
    liquidation_fee_rate: float,
) -> tuple[float, float]:
    """Solve liquidation against the tier covering liquidation-time notional."""
    candidates: list[tuple[float, float]] = []
    for tier in maintenance_margin_tiers:
        price = isolated_short_liquidation_price(
            entry_price=entry_price,
            quantity=quantity,
            contract_size=contract_size,
            isolated_margin=isolated_margin,
            maintenance_margin_rate=tier.maintenance_margin_rate,
            liquidation_fee_rate=liquidation_fee_rate,
        )
        liquidation_notional = quantity * contract_size * price
        if liquidation_notional >= tier.notional_floor and (
            tier.notional_cap is None or liquidation_notional < tier.notional_cap
        ):
            candidates.append((price, tier.maintenance_margin_rate))
    if not candidates:
        raise ConstraintError("EXECUTION_LIQUIDATION_TIER_UNRESOLVED")
    return min(candidates, key=lambda candidate: candidate[0])


def build_short_paper_execution_plan(
    *,
    signal_id: str,
    cluster_id: str,
    evaluation_time: int,
    constraints: ValidatedMarketConstraints,
    expected_constraints_hash: str,
    cost_policy: ExecutionCostPolicy,
    risk_policy: RiskPolicy,
    portfolio: PortfolioCapacity,
    leverage_bounds: SafeLeverageBounds,
    raw_levels: RawShortLevels,
    requested_risk_rate: float | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    try:
        constraints.require_usable(
            evaluation_time=evaluation_time,
            expected_hash=expected_constraints_hash,
        )
        cost_policy.require_integrity()
        risk_policy.require_integrity()
        leverage_bounds.require_integrity()
        levels = conservative_short_levels(
            entry=raw_levels.entry,
            tp1=raw_levels.tp1,
            tp2=raw_levels.tp2,
            stop=raw_levels.stop,
            tick_size=constraints.tick_size,
        )
    except ConstraintError as exc:
        return _blocked_plan(
            signal_id,
            str(exc),
            constraints=constraints,
            evaluation_time=evaluation_time,
        )

    raw_safe_leverage = leverage_bounds.safe_leverage
    leverage_block = _leverage_block_reason(
        leverage_bounds=leverage_bounds,
        constraints=constraints,
        risk_policy=risk_policy,
    )
    if leverage_block is not None:
        return _blocked_plan(
            signal_id,
            leverage_block,
            constraints=constraints,
            evaluation_time=evaluation_time,
            raw_safe_leverage=raw_safe_leverage,
        )
    leverage = min(
        risk_policy.maximum_system_leverage,
        math.floor(raw_safe_leverage),
    )
    equity = portfolio.conservative_sizing_equity
    if equity <= 0:
        return _blocked_plan(
            signal_id,
            "PORTFOLIO_EQUITY_EXHAUSTED",
            constraints=constraints,
            evaluation_time=evaluation_time,
        )
    contract = constraints.contract_size
    entry_cost_rate = cost_policy.taker_fee_rate + cost_policy.entry_slippage_p95_rate
    short_funding_cost_rate = max(-cost_policy.expected_funding_rate, 0.0)
    stop_cost_rate = (
        cost_policy.taker_fee_rate
        + cost_policy.exit_slippage_p95_rate
        + short_funding_cost_rate
    )
    risk_per_contract = contract * (
        levels["stop"] - levels["entry"]
        + levels["entry"] * entry_cost_rate
        + levels["stop"] * stop_cost_rate
    )
    target_risk_rate = (
        risk_policy.default_risk_at_stop_per_position_rate
        if requested_risk_rate is None
        else float(requested_risk_rate)
    )
    if (
        not math.isfinite(target_risk_rate)
        or target_risk_rate <= 0
        or target_risk_rate > risk_policy.max_risk_at_stop_per_position_rate
    ):
        return _blocked_plan(
            signal_id,
            "RISK_RATE_OUTSIDE_POLICY",
            constraints=constraints,
            evaluation_time=evaluation_time,
            raw_safe_leverage=raw_safe_leverage,
        )
    risk_budget = equity * target_risk_rate
    raw_quantity = risk_budget / risk_per_contract
    margin_quantity_cap = (
        equity * risk_policy.max_margin_per_position_rate * leverage
    ) / (levels["entry"] * contract)
    quantity = _round_step(
        min(raw_quantity, margin_quantity_cap),
        constraints.quantity_step,
        ROUND_FLOOR,
    )
    notional = quantity * contract * levels["entry"]
    quantity_block = _quantity_block_reason(
        quantity=quantity,
        notional=notional,
        levels=levels,
        constraints=constraints,
    )
    if quantity_block is not None:
        return _blocked_plan(
            signal_id,
            quantity_block,
            constraints=constraints,
            evaluation_time=evaluation_time,
        )

    risk_at_stop = quantity * risk_per_contract
    gross_stop_loss = quantity * contract * (levels["stop"] - levels["entry"])
    entry_cost = quantity * contract * levels["entry"] * entry_cost_rate
    stop_exit_cost = quantity * contract * levels["stop"] * (
        cost_policy.taker_fee_rate + cost_policy.exit_slippage_p95_rate
    )
    expected_funding_cost = (
        quantity * contract * levels["stop"] * short_funding_cost_rate
    )
    isolated_margin = notional / leverage
    reasons.extend(
        _portfolio_capacity_reasons(
            portfolio=portfolio,
            risk_policy=risk_policy,
            cluster_id=cluster_id,
            equity=equity,
            isolated_margin=isolated_margin,
            risk_at_stop=risk_at_stop,
        )
    )

    liquidation_price, maintenance_rate = tiered_isolated_short_liquidation(
        entry_price=levels["entry"],
        quantity=quantity,
        contract_size=contract,
        isolated_margin=isolated_margin,
        maintenance_margin_tiers=constraints.maintenance_margin_tiers,
        liquidation_fee_rate=constraints.liquidation_fee_rate,
    )
    liquidation_buffer = liquidation_price - levels["stop"]
    if liquidation_buffer < levels["entry"] * cost_policy.liquidation_buffer_rate:
        reasons.append("LIQUIDATION_BUFFER_INSUFFICIENT")

    tp2_cost = quantity * contract * (
        levels["entry"] * entry_cost_rate
        + levels["tp2"] * (
            cost_policy.maker_fee_rate
            + cost_policy.exit_slippage_p95_rate
            + short_funding_cost_rate
        )
    )
    gross_tp2 = quantity * contract * (levels["entry"] - levels["tp2"])
    net_tp2 = gross_tp2 - tp2_cost
    net_reward_risk = net_tp2 / risk_at_stop if risk_at_stop > 0 else 0.0
    if net_reward_risk < risk_policy.minimum_net_reward_risk:
        reasons.append("NET_REWARD_RISK_BELOW_POLICY")
    if reasons:
        return _blocked_plan(
            signal_id,
            *reasons,
            constraints=constraints,
            evaluation_time=evaluation_time,
            raw_safe_leverage=raw_safe_leverage,
        )
    return _with_execution_plan_hash({
        "contract_version": "short_paper_execution_plan_v1",
        "execution_mode": "SIGNAL_ONLY",
        "status": "READY",
        "reason_codes": [],
        "signal_id": signal_id,
        "cluster_id": cluster_id,
        "venue": "LBANK",
        "venue_symbol": constraints.venue_symbol,
        "evaluation_time": evaluation_time,
        "expires_at": constraints.expires_at,
        "constraints_hash": constraints.constraints_hash,
        "cost_policy_hash": cost_policy.policy_hash,
        "risk_policy_hash": risk_policy.policy_hash,
        "leverage_bounds_hash": leverage_bounds.bounds_hash,
        "leverage_bounds": leverage_bounds.model_dump(mode="json"),
        "requested_risk_rate": target_risk_rate,
        "rounding_policy_version": "short_rounding_policy_v1",
        "raw_safe_leverage": raw_safe_leverage,
        "system_leverage": leverage,
        "levels": levels,
        "quantity_contracts": quantity,
        "contract_size": contract,
        "notional": round(notional, 8),
        "isolated_margin": round(isolated_margin, 8),
        "risk_at_stop": round(risk_at_stop, 8),
        "risk_at_stop_rate": round(risk_at_stop / equity, 8),
        "gross_stop_loss": round(gross_stop_loss, 8),
        "entry_cost": round(entry_cost, 8),
        "stop_exit_cost": round(stop_exit_cost, 8),
        "expected_funding_cost": round(expected_funding_cost, 8),
        "gross_tp2_pnl": round(gross_tp2, 8),
        "total_tp2_cost": round(tp2_cost, 8),
        "net_tp2_pnl": round(net_tp2, 8),
        "net_reward_risk": round(net_reward_risk, 8),
        "maintenance_margin_rate": maintenance_rate,
        "maintenance_margin_tiers": [
            tier.model_dump(mode="json")
            for tier in constraints.maintenance_margin_tiers
        ],
        "liquidation_fee_rate": constraints.liquidation_fee_rate,
        "liquidation_price": round(liquidation_price, 8),
        "liquidation_buffer": round(liquidation_buffer, 8),
        "conservative_sizing_equity": round(equity, 8),
        "strategy_equivalent": False,
    })


def _leverage_block_reason(
    *,
    leverage_bounds: SafeLeverageBounds,
    constraints: ValidatedMarketConstraints,
    risk_policy: RiskPolicy,
) -> str | None:
    if leverage_bounds.exchange_max > constraints.maximum_leverage:
        return "LEVERAGE_EXCHANGE_BOUND_CONFLICT"
    if leverage_bounds.safe_leverage < risk_policy.minimum_product_leverage:
        return "ENTRY_BLOCKED_RISK_BUDGET"
    return None


def _quantity_block_reason(
    *,
    quantity: float,
    notional: float,
    levels: dict[str, float],
    constraints: ValidatedMarketConstraints,
) -> str | None:
    if quantity < constraints.min_quantity:
        return "EXECUTION_MIN_QUANTITY_UNSAFE"
    if notional < constraints.min_notional:
        return "EXECUTION_MIN_NOTIONAL_UNSAFE"
    if any(
        price < constraints.price_band.minimum or price > constraints.price_band.maximum
        for price in levels.values()
    ):
        return "EXECUTION_PRICE_BAND_VIOLATION"
    return None


def _portfolio_capacity_reasons(
    *,
    portfolio: PortfolioCapacity,
    risk_policy: RiskPolicy,
    cluster_id: str,
    equity: float,
    isolated_margin: float,
    risk_at_stop: float,
) -> list[str]:
    existing_margin = sum(
        item.locked_isolated_margin for item in portfolio.open_exposures
    )
    existing_risk = sum(item.risk_at_stop for item in portfolio.open_exposures)
    cluster_risk = sum(
        item.risk_at_stop
        for item in portfolio.open_exposures
        if item.cluster_id == cluster_id
    )
    checks = (
        (
            len(portfolio.open_exposures) >= risk_policy.max_open_positions,
            "PORTFOLIO_SLOT_CAP_REACHED",
        ),
        (
            existing_margin + isolated_margin
            > equity * risk_policy.max_total_locked_margin_rate,
            "PORTFOLIO_LOCKED_MARGIN_CAP_EXCEEDED",
        ),
        (
            equity - existing_margin - isolated_margin
            < equity * risk_policy.minimum_free_reserve_rate,
            "PORTFOLIO_FREE_RESERVE_BREACHED",
        ),
        (
            existing_risk + risk_at_stop
            > equity * risk_policy.max_total_open_risk_rate,
            "PORTFOLIO_OPEN_RISK_CAP_EXCEEDED",
        ),
        (
            cluster_risk + risk_at_stop
            > equity * risk_policy.max_correlated_cluster_risk_rate,
            "PORTFOLIO_CLUSTER_RISK_CAP_EXCEEDED",
        ),
    )
    return [reason for violated, reason in checks if violated]


def _blocked_plan(
    signal_id: str,
    *reason_codes: str,
    constraints: ValidatedMarketConstraints,
    evaluation_time: int,
    raw_safe_leverage: float | None = None,
) -> dict[str, Any]:
    return _with_execution_plan_hash({
        "contract_version": "short_paper_execution_plan_v1",
        "execution_mode": "SIGNAL_ONLY",
        "status": "BLOCKED",
        "reason_codes": list(reason_codes),
        "signal_id": signal_id,
        "evaluation_time": evaluation_time,
        "expires_at": constraints.expires_at,
        "constraints_hash": constraints.constraints_hash,
        "raw_safe_leverage": raw_safe_leverage,
        "system_leverage": None,
        "levels": None,
        "strategy_equivalent": False,
    })


def _with_execution_plan_hash(plan: dict[str, Any]) -> dict[str, Any]:
    return {**plan, "execution_plan_hash": canonical_sha256(plan)}
