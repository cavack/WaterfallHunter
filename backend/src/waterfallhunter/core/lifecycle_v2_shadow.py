"""Deterministic Lifecycle V2 evaluator that cannot mutate V1 state."""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waterfallhunter.core.lifecycle_feature_registry import (
    LifecycleProfile,
    LifecycleV2Policy,
    lifecycle_feature_registry,
    lifecycle_v2_policy_v1,
)
from waterfallhunter.core.signal_metadata import canonical_sha256


POLICY_VERSION = "lifecycle_v2_shadow_policy_v1"
STRATEGY_PROFILE = LifecycleProfile.STRICT.value
SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"


class LifecycleV2State(str, Enum):
    WATCH = "WATCH"
    FUEL_RICH = "FUEL_RICH"
    PRE_TRIGGER = "PRE_TRIGGER"
    ARMED = "ARMED"
    TRIGGERED = "TRIGGERED"
    LATE = "LATE"
    EXHAUSTED = "EXHAUSTED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    EXECUTION_BLOCKED = "EXECUTION_BLOCKED"


TERMINAL_STATES = frozenset(
    {
        LifecycleV2State.LATE,
        LifecycleV2State.EXHAUSTED,
        LifecycleV2State.INVALIDATED,
        LifecycleV2State.EXPIRED,
        LifecycleV2State.EXECUTION_BLOCKED,
    }
)


class LifecycleV2Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    eligible_data: bool
    evaluated_profile: LifecycleProfile = LifecycleProfile.STRICT
    feature_registry_hash: str = Field(
        default_factory=lambda: str(lifecycle_feature_registry()["registry_hash"]),
        pattern=SHA256_HEX_PATTERN,
    )
    unavailable_fields: tuple[str, ...] = ()
    fuel_rich: bool | None = None
    structure_count: int | None = Field(default=None, ge=0)
    flow_family_pass: bool | None = None
    relative_family_pass: bool | None = None
    anti_chase_pass: bool | None = None
    strict_setup_ready: bool | None = None
    lower_tf_trigger_closed: bool | None = None
    distance_to_trigger_atr: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    lbank_constraints_fresh: bool | None = None
    orderbook_fresh: bool | None = None
    levels_constructible: bool | None = None
    estimated_round_trip_cost_r: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    executable_depth_multiple: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    preliminary_portfolio_capacity: bool | None = None
    confirmation_count: int | None = Field(default=None, ge=0)
    confirmation_family_count: int | None = Field(default=None, ge=0)
    extension_atr: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    invalidation_closed: bool = False
    expired: bool = False
    exhausted: bool = False
    liquidity_shock: bool = False
    oldest_required_observed_at: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    required_observed_at: tuple[float, ...] = ()
    decision_at: int = Field(ge=0)
    decision_clock_at: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    max_required_age_seconds: int = Field(default=60, ge=1)
    evidence_refs: tuple[str, ...]

    @model_validator(mode="after")
    def _clock_order(self) -> "LifecycleV2Evidence":
        required_fields = (
            "fuel_rich",
            "structure_count",
            "flow_family_pass",
            "relative_family_pass",
            "anti_chase_pass",
            "strict_setup_ready",
            "lower_tf_trigger_closed",
            "distance_to_trigger_atr",
            "lbank_constraints_fresh",
            "orderbook_fresh",
            "levels_constructible",
            "estimated_round_trip_cost_r",
            "executable_depth_multiple",
            "preliminary_portfolio_capacity",
            "confirmation_count",
            "confirmation_family_count",
            "extension_atr",
            "oldest_required_observed_at",
        )
        actually_missing = tuple(
            name for name in required_fields if getattr(self, name) is None
        )
        if self.eligible_data and (actually_missing or self.unavailable_fields):
            raise ValueError("eligible lifecycle evidence must be complete")
        if not self.eligible_data and not self.unavailable_fields:
            raise ValueError("ineligible lifecycle evidence must name unavailable fields")
        if set(actually_missing) - set(self.unavailable_fields):
            raise ValueError("every missing lifecycle field must be declared unavailable")
        decision_clock_at = (
            float(self.decision_clock_at)
            if self.decision_clock_at is not None
            else float(self.decision_at)
        )
        if (
            self.decision_clock_at is not None
            and self.decision_at != math.ceil(decision_clock_at)
        ):
            raise ValueError(
                "decision_at must equal ceil(decision_clock_at)"
            )
        if (
            self.oldest_required_observed_at is not None
            and self.oldest_required_observed_at > decision_clock_at
        ):
            raise ValueError(
                "required evidence cannot be observed after decision clock"
            )
        if self.eligible_data and not self.required_observed_at:
            raise ValueError("eligible lifecycle evidence must bind source timestamps")
        if any(
            timestamp > decision_clock_at
            for timestamp in self.required_observed_at
        ):
            raise ValueError(
                "required source timestamp cannot be after decision clock"
            )
        if (
            self.required_observed_at
            and self.oldest_required_observed_at is not None
            and self.oldest_required_observed_at != min(self.required_observed_at)
        ):
            raise ValueError("oldest required timestamp must match source timestamps")
        if not self.evidence_refs:
            raise ValueError("lifecycle transitions require evidence references")
        return self

    @property
    def fresh(self) -> bool:
        if self.oldest_required_observed_at is None:
            return False

        decision_clock_at = (
            float(self.decision_clock_at)
            if self.decision_clock_at is not None
            else float(self.decision_at)
        )

        return (
            decision_clock_at - self.oldest_required_observed_at
            <= self.max_required_age_seconds
        )


class LifecycleV2Transition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["lifecycle_v2_shadow_transition_v1"]
    episode_id: str = Field(min_length=1)
    from_state: LifecycleV2State
    to_state: LifecycleV2State
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    observed_at: int = Field(ge=0)
    policy_version: Literal["lifecycle_v2_shadow_policy_v1"]
    policy_hash: str = Field(pattern=SHA256_HEX_PATTERN)
    feature_registry_hash: str = Field(pattern=SHA256_HEX_PATTERN)
    strategy_profile: LifecycleProfile
    shadow_only: Literal[True]
    trade_eligible: Literal[False]
    transition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def evaluate_lifecycle_v2_shadow(
    *,
    episode_id: str,
    current_state: LifecycleV2State,
    evidence: LifecycleV2Evidence,
    policy: LifecycleV2Policy | None = None,
) -> LifecycleV2Transition:
    active_policy = policy or lifecycle_v2_policy_v1()
    if evidence.feature_registry_hash != active_policy.feature_registry_hash:
        raise ValueError("LIFECYCLE_FEATURE_REGISTRY_MISMATCH")
    next_state, reasons = _next_state(current_state, evidence, active_policy)
    material = {
        "contract_version": "lifecycle_v2_shadow_transition_v1",
        "episode_id": episode_id,
        "from_state": current_state.value,
        "to_state": next_state.value,
        "reason_codes": list(reasons),
        "evidence_refs": list(evidence.evidence_refs),
        "observed_at": evidence.decision_at,
        "policy_version": POLICY_VERSION,
        "policy_hash": active_policy.policy_hash,
        "feature_registry_hash": active_policy.feature_registry_hash,
        "strategy_profile": evidence.evaluated_profile.value,
        "shadow_only": True,
        "trade_eligible": False,
    }
    return LifecycleV2Transition.model_validate(
        {**material, "transition_hash": canonical_sha256(material)}
    )


def compare_v1_v2_shadow(
    *,
    episode_id: str,
    v1_state: str,
    v2_state: LifecycleV2State,
    evidence: LifecycleV2Evidence,
    policy: LifecycleV2Policy | None = None,
) -> dict:
    transition = evaluate_lifecycle_v2_shadow(
        episode_id=episode_id,
        current_state=v2_state,
        evidence=evidence,
        policy=policy,
    )
    normalized_v1_state = canonical_lifecycle_state(v1_state)
    packet = {
        "contract_version": "lifecycle_v1_v2_shadow_comparison_v1",
        "episode_id": episode_id,
        "v1_state_unchanged": normalized_v1_state,
        "v2_from_state": transition.from_state.value,
        "v2_to_state": transition.to_state.value,
        "diverged": normalized_v1_state != transition.to_state.value,
        "v2_transition_hash": transition.transition_hash,
        "shadow_only": True,
        "promotion_allowed": False,
    }
    return {**packet, "comparison_hash": canonical_sha256(packet)}


def canonical_lifecycle_state(value: str) -> str:
    return str(value).strip().upper().replace("-", "_")


def _next_state(
    current: LifecycleV2State,
    evidence: LifecycleV2Evidence,
    policy: LifecycleV2Policy,
) -> tuple[LifecycleV2State, tuple[str, ...]]:
    if current in TERMINAL_STATES:
        return current, ("TERMINAL_EPISODE_IMMUTABLE",)
    if evidence.extension_atr is None or evidence.oldest_required_observed_at is None:
        return current, ("INSUFFICIENT_EVIDENCE",)
    if not evidence.fresh:
        return current, ("DATA_STALE",)
    terminal = _terminal_transition(evidence, policy)
    if terminal is not None:
        return terminal
    handlers = {
        LifecycleV2State.WATCH: _next_from_watch,
        LifecycleV2State.FUEL_RICH: _next_from_fuel_rich,
        LifecycleV2State.PRE_TRIGGER: _next_from_pre_trigger,
        LifecycleV2State.ARMED: _next_from_armed,
    }
    handler = handlers.get(current)
    if handler is None:
        return current, ("NO_FORWARD_TRANSITION",)
    return handler(evidence, policy)


def _terminal_transition(
    evidence: LifecycleV2Evidence,
    policy: LifecycleV2Policy,
) -> tuple[LifecycleV2State, tuple[str, ...]] | None:
    thresholds = policy.thresholds
    if evidence.invalidation_closed:
        return LifecycleV2State.INVALIDATED, ("INVALIDATION_CLOSED",)
    if evidence.expired:
        return LifecycleV2State.EXPIRED, ("SIGNAL_EXPIRED",)
    if evidence.liquidity_shock:
        return LifecycleV2State.EXECUTION_BLOCKED, ("EXECUTION_LIQUIDITY_SHOCK",)
    if evidence.exhausted:
        return LifecycleV2State.EXHAUSTED, ("MOVE_EXHAUSTED",)
    if evidence.extension_atr >= thresholds.hard_block_atr:
        return LifecycleV2State.EXECUTION_BLOCKED, ("ANTI_CHASE_HARD_BLOCK",)
    if evidence.extension_atr > thresholds.late_atr:
        return LifecycleV2State.LATE, ("LATE_EXTENSION",)
    return None


def _next_from_watch(
    evidence: LifecycleV2Evidence,
    _: LifecycleV2Policy,
) -> tuple[LifecycleV2State, tuple[str, ...]]:
    if evidence.fuel_rich is None:
        return LifecycleV2State.WATCH, ("INSUFFICIENT_EVIDENCE",)
    if evidence.fuel_rich:
        return LifecycleV2State.FUEL_RICH, ("FUEL_EXPANSION_CONFIRMED",)
    return LifecycleV2State.WATCH, ("FUEL_RICH_NOT_CONFIRMED",)


def _next_from_fuel_rich(
    evidence: LifecycleV2Evidence,
    policy: LifecycleV2Policy,
) -> tuple[LifecycleV2State, tuple[str, ...]]:
    if (
        evidence.structure_count is None
        or evidence.anti_chase_pass is None
        or (
            evidence.flow_family_pass is None
            and evidence.relative_family_pass is None
        )
    ):
        return LifecycleV2State.FUEL_RICH, ("INSUFFICIENT_EVIDENCE",)
    if (
        evidence.structure_count >= policy.thresholds.structure_count_minimum
        and (evidence.flow_family_pass or evidence.relative_family_pass)
        and evidence.anti_chase_pass
    ):
        return LifecycleV2State.PRE_TRIGGER, ("STRUCTURE_AND_WEAKNESS_CONFIRMED",)
    return LifecycleV2State.FUEL_RICH, ("STRUCTURE_NOT_CONFIRMED",)


def _next_from_pre_trigger(
    evidence: LifecycleV2Evidence,
    policy: LifecycleV2Policy,
) -> tuple[LifecycleV2State, tuple[str, ...]]:
    required = (
        evidence.strict_setup_ready,
        evidence.lower_tf_trigger_closed,
        evidence.distance_to_trigger_atr,
        evidence.lbank_constraints_fresh,
        evidence.orderbook_fresh,
        evidence.levels_constructible,
        evidence.estimated_round_trip_cost_r,
        evidence.executable_depth_multiple,
        evidence.preliminary_portfolio_capacity,
    )
    if any(value is None for value in required):
        return LifecycleV2State.PRE_TRIGGER, ("INSUFFICIENT_EVIDENCE",)
    if evidence.evaluated_profile is LifecycleProfile.EXPERIMENTAL:
        return LifecycleV2State.PRE_TRIGGER, (
            "EXPERIMENTAL_PROFILE_OBSERVATIONAL_ONLY",
        )
    thresholds = policy.thresholds
    checks = (
        (not evidence.strict_setup_ready, "STRICT_SETUP_NOT_READY"),
        (evidence.lower_tf_trigger_closed, "TRIGGER_ALREADY_CLOSED_BEFORE_ARMING"),
        (
            evidence.distance_to_trigger_atr > thresholds.controlled_momentum_max_atr,
            "TRIGGER_DISTANCE_TOO_LARGE",
        ),
        (not evidence.lbank_constraints_fresh, "LBANK_CONSTRAINTS_STALE"),
        (not evidence.orderbook_fresh, "DATA_STALE"),
        (not evidence.levels_constructible, "EXECUTION_LEVELS_UNAVAILABLE"),
        (
            evidence.estimated_round_trip_cost_r > thresholds.estimated_cost_r_maximum,
            "ESTIMATED_COST_TOO_HIGH",
        ),
        (
            evidence.executable_depth_multiple
            < thresholds.executable_depth_multiple_minimum,
            "DEPTH_INSUFFICIENT",
        ),
        (not evidence.preliminary_portfolio_capacity, "PORTFOLIO_CAPACITY_FULL"),
    )
    reasons = tuple(reason for failed, reason in checks if failed)
    if reasons:
        return LifecycleV2State.PRE_TRIGGER, reasons
    return LifecycleV2State.ARMED, ("STRICT_SETUP_ARMED",)


def _next_from_armed(
    evidence: LifecycleV2Evidence,
    policy: LifecycleV2Policy,
) -> tuple[LifecycleV2State, tuple[str, ...]]:
    if (
        evidence.lower_tf_trigger_closed is None
        or evidence.confirmation_count is None
        or evidence.confirmation_family_count is None
    ):
        return LifecycleV2State.ARMED, ("INSUFFICIENT_EVIDENCE",)
    thresholds = policy.thresholds
    if (
        evidence.lower_tf_trigger_closed
        and evidence.confirmation_count >= thresholds.confirmation_count_minimum
        and evidence.confirmation_family_count
        >= thresholds.confirmation_family_count_minimum
    ):
        return LifecycleV2State.TRIGGERED, ("CLOSED_TRIGGER_CONFIRMED",)
    return LifecycleV2State.ARMED, ("TRIGGER_NOT_CLOSED",)


def _packet(container: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    value = container.get(key) if isinstance(container, dict) else None
    return value if isinstance(value, dict) else None


def _number_fact(packet: dict[str, Any] | None, key: str) -> float | None:
    value = packet.get(key) if packet is not None else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _timestamp(packet: dict[str, Any] | None, key: str) -> float | None:
    value = _number_fact(packet, key)
    return value if value is not None and value >= 0 else None


def _bool_fact(packet: dict[str, Any] | None, key: str) -> bool | None:
    value = packet.get(key) if packet is not None else None
    return value if isinstance(value, bool) else None


def _available_footprint_fact(footprint: dict[str, Any] | None) -> bool | None:
    if footprint is None or footprint.get("available") is not True:
        return None
    return _bool_fact(footprint, "aggressive_selling")


def _candle_facts(
    candles: dict[str, Any] | None,
) -> tuple[int | None, tuple[float, ...], tuple[float, ...]]:
    packets = tuple(
        value for value in (candles or {}).values() if isinstance(value, dict)
    )
    if not packets:
        return None, (), ()
    structure_count = sum(
        packet.get("lower_high") is True or packet.get("setup") is True
        for packet in packets
    )
    distances = tuple(
        abs(value)
        for packet in packets
        if (value := _number_fact(packet, "distance_to_support_atr")) is not None
    )
    extensions = tuple(
        max(0.0, value)
        for packet in packets
        if (value := _number_fact(packet, "extension_from_support_atr")) is not None
    )
    return structure_count, distances, extensions


def _relative_family_pass(relative: dict[str, Any] | None) -> bool | None:
    timeframes = _packet(relative, "timeframes")
    values = tuple(
        float(value)
        for packet in (timeframes or {}).values()
        if isinstance(packet, dict)
        for key, value in packet.items()
        if key.startswith("relative_return_")
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    )
    return sum(value < 0 for value in values) >= 2 if values else None


def _confirmation_facts(
    breakdown: dict[str, Any] | None,
    footprint: dict[str, Any] | None,
) -> tuple[int | None, int | None]:
    if breakdown is None:
        return None, None
    breakdown_facts = (
        _bool_fact(breakdown, "primary_breakdown_confirmed"),
        _bool_fact(breakdown, "confirmation_exchange_15m"),
    )
    footprint_fact = _available_footprint_fact(footprint)
    confirmation_count = sum(
        value is True for value in (*breakdown_facts, footprint_fact)
    )
    family_count = int(any(value is True for value in breakdown_facts)) + int(
        footprint_fact is True
    )
    return confirmation_count, family_count


def _strict_setup_ready(
    metrics: dict[str, Any],
    confirmed: dict[str, Any] | None,
) -> bool | None:
    if confirmed is None:
        return None
    trade_eligible = metrics.get("trade_eligible")
    if not isinstance(trade_eligible, bool):
        return None
    setup = _bool_fact(confirmed, "setup")
    return trade_eligible and setup is True if setup is not None else None


def _constraints_fresh(
    observed_at: float | None,
    expires_at: float | None,
    decision_at: int | float,
) -> bool | None:
    if observed_at is None or expires_at is None:
        return None
    return observed_at <= decision_at < expires_at


def _observation_fresh(
    observed_at: float | None,
    decision_at: int | float,
    *,
    maximum_age: int,
) -> bool | None:
    if observed_at is None:
        return None
    return 0 <= decision_at - observed_at <= maximum_age


def _ready_preview(preview: dict[str, Any] | None) -> bool | None:
    return preview.get("status") == "READY" if preview is not None else None


def _oldest_timestamp(*timestamps: int | float | None) -> float | None:
    available = tuple(float(value) for value in timestamps if value is not None)
    return min(available) if available else None


def build_lifecycle_v2_evidence_from_metrics(
    *,
    metrics: dict[str, Any],
    decision_at: int,
    analysis_observed_at: int | float | None,
    reference_observed_at: int | float | None,
    decision_clock_at: int | float | None = None,
) -> LifecycleV2Evidence:
    """Map runtime facts into V2 evidence without inventing missing values."""

    effective_decision_clock_at = (
        float(decision_clock_at)
        if decision_clock_at is not None
        else float(decision_at)
    )
    if (
        not math.isfinite(effective_decision_clock_at)
        or effective_decision_clock_at < 0
    ):
        raise ValueError(
            "decision clock must be a non-negative finite timestamp"
        )

    stage = _packet(metrics, "stage_lifecycle")
    confirmed = _packet(stage, "confirmed")
    candles = _packet(metrics, "candle_features")
    micro = _packet(metrics, "microstructure")
    relative = _packet(metrics, "relative_weakness_features")
    breakdown = _packet(metrics, "breakdown_confirmation")
    constraints = _packet(metrics, "validated_lbank_constraints")
    preview = _packet(metrics, "execution_preview")
    capacity = _packet(metrics, "portfolio_capacity")
    footprint = _packet(micro, "footprint")
    structure_count, distances, extensions = _candle_facts(candles)
    confirmation_count, confirmation_family_count = _confirmation_facts(
        breakdown,
        footprint,
    )
    orderbook_observed_at = _timestamp(micro, "observed_at")
    constraints_observed_at = _timestamp(constraints, "constraints_observed_at")
    constraints_expires_at = _timestamp(constraints, "expires_at")

    values: dict[str, Any] = {
        "fuel_rich": _bool_fact(confirmed, "hype"),
        "structure_count": structure_count,
        "flow_family_pass": _available_footprint_fact(footprint),
        "relative_family_pass": _relative_family_pass(relative),
        "anti_chase_pass": max(extensions) <= 0.8 if extensions else None,
        "strict_setup_ready": _strict_setup_ready(metrics, confirmed),
        "lower_tf_trigger_closed": _bool_fact(
            breakdown,
            "composite_breakdown_confirmed",
        ),
        "distance_to_trigger_atr": min(distances) if distances else None,
        "lbank_constraints_fresh": _constraints_fresh(
            constraints_observed_at,
            constraints_expires_at,
            effective_decision_clock_at,
        ),
        "orderbook_fresh": _observation_fresh(
            orderbook_observed_at,
            effective_decision_clock_at,
            maximum_age=5,
        ),
        "levels_constructible": _ready_preview(preview),
        "estimated_round_trip_cost_r": _number_fact(
            preview,
            "estimated_round_trip_cost_r",
        ),
        "executable_depth_multiple": _number_fact(
            preview,
            "executable_depth_multiple",
        ),
        "preliminary_portfolio_capacity": _bool_fact(capacity, "available"),
        "confirmation_count": confirmation_count,
        "confirmation_family_count": confirmation_family_count,
        "extension_atr": max(extensions) if extensions else None,
        "oldest_required_observed_at": _oldest_timestamp(
            analysis_observed_at,
            reference_observed_at,
            orderbook_observed_at,
            constraints_observed_at,
        ),
    }
    unavailable = tuple(sorted(name for name, value in values.items() if value is None))
    refs = tuple(
        f"{name}:{timestamp}"
        for name, timestamp in (
            ("analysis", analysis_observed_at),
            ("reference", reference_observed_at),
            ("orderbook", orderbook_observed_at),
            ("lbank_constraints", constraints_observed_at),
        )
        if timestamp is not None
    ) or (f"decision:{decision_at}",)
    registry = lifecycle_feature_registry()
    runtime_profile = (
        LifecycleProfile.EXPERIMENTAL
        if str(metrics.get("strategy_profile") or "").lower().startswith("experimental")
        else LifecycleProfile.STRICT
    )
    refs = (
        *refs,
        f"feature_registry:{registry['registry_hash']}",
        f"derived_evidence:{canonical_sha256(values)}",
    )
    return LifecycleV2Evidence.model_validate(
        {
            "eligible_data": not unavailable,
            "evaluated_profile": runtime_profile,
            "feature_registry_hash": registry["registry_hash"],
            "unavailable_fields": unavailable,
            "required_observed_at": tuple(
                timestamp
                for timestamp in (
                    analysis_observed_at,
                    reference_observed_at,
                    orderbook_observed_at,
                    constraints_observed_at,
                )
                if timestamp is not None
            ),
            **values,
            "decision_at": decision_at,
            "decision_clock_at": effective_decision_clock_at,
            "evidence_refs": refs,
        }
    )
