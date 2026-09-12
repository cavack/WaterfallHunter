from __future__ import annotations

import pytest
from pydantic import ValidationError

from waterfallhunter.core.lifecycle_feature_registry import (
    LifecycleProfile,
    lifecycle_feature_registry,
    lifecycle_v2_policy_v1,
)
from waterfallhunter.core.lifecycle_v2_shadow import (
    LifecycleV2Evidence,
    LifecycleV2State,
    build_lifecycle_v2_evidence_from_metrics,
    compare_v1_v2_shadow,
    evaluate_lifecycle_v2_shadow,
)


def _evidence(**overrides) -> LifecycleV2Evidence:
    values = {
        "eligible_data": True,
        "fuel_rich": True,
        "structure_count": 2,
        "flow_family_pass": True,
        "relative_family_pass": False,
        "anti_chase_pass": True,
        "strict_setup_ready": True,
        "lower_tf_trigger_closed": False,
        "distance_to_trigger_atr": 0.4,
        "lbank_constraints_fresh": True,
        "orderbook_fresh": True,
        "levels_constructible": True,
        "estimated_round_trip_cost_r": 0.10,
        "executable_depth_multiple": 12.0,
        "preliminary_portfolio_capacity": True,
        "confirmation_count": 2,
        "confirmation_family_count": 2,
        "extension_atr": 0.2,
        "oldest_required_observed_at": 990,
        "required_observed_at": (1_000, 999, 995, 990),
        "decision_at": 1_000,
        "evidence_refs": ("candle:3m:1000", "orderbook:lbank:995"),
    }
    return LifecycleV2Evidence.model_validate({**values, **overrides})


def test_v2_progresses_forward_with_armed_and_triggered_invariants() -> None:
    watch = evaluate_lifecycle_v2_shadow(
        episode_id="episode-1",
        current_state=LifecycleV2State.WATCH,
        evidence=_evidence(),
    )
    fuel = evaluate_lifecycle_v2_shadow(
        episode_id="episode-1",
        current_state=watch.to_state,
        evidence=_evidence(),
    )
    pre = evaluate_lifecycle_v2_shadow(
        episode_id="episode-1",
        current_state=fuel.to_state,
        evidence=_evidence(),
    )
    armed = evaluate_lifecycle_v2_shadow(
        episode_id="episode-1",
        current_state=pre.to_state,
        evidence=_evidence(lower_tf_trigger_closed=True),
    )

    assert [watch.to_state, fuel.to_state, pre.to_state, armed.to_state] == [
        LifecycleV2State.FUEL_RICH,
        LifecycleV2State.PRE_TRIGGER,
        LifecycleV2State.ARMED,
        LifecycleV2State.TRIGGERED,
    ]
    assert all(
        transition.shadow_only is True and transition.trade_eligible is False
        for transition in (watch, fuel, pre, armed)
    )


def test_trigger_requires_closed_candle_and_two_confirmation_families() -> None:
    transition = evaluate_lifecycle_v2_shadow(
        episode_id="episode-1",
        current_state=LifecycleV2State.ARMED,
        evidence=_evidence(
            lower_tf_trigger_closed=True,
            confirmation_count=2,
            confirmation_family_count=1,
        ),
    )

    assert transition.to_state is LifecycleV2State.ARMED
    assert transition.reason_codes == ("TRIGGER_NOT_CLOSED",)


def test_late_expired_and_invalidated_are_terminal_for_the_episode() -> None:
    late = evaluate_lifecycle_v2_shadow(
        episode_id="episode-1",
        current_state=LifecycleV2State.ARMED,
        evidence=_evidence(extension_atr=0.81),
    )
    repeated = evaluate_lifecycle_v2_shadow(
        episode_id="episode-1",
        current_state=late.to_state,
        evidence=_evidence(extension_atr=0.0),
    )
    expired = evaluate_lifecycle_v2_shadow(
        episode_id="episode-2",
        current_state=LifecycleV2State.PRE_TRIGGER,
        evidence=_evidence(expired=True),
    )
    invalidated = evaluate_lifecycle_v2_shadow(
        episode_id="episode-3",
        current_state=LifecycleV2State.ARMED,
        evidence=_evidence(invalidation_closed=True),
    )

    assert late.to_state is LifecycleV2State.LATE
    assert repeated.to_state is LifecycleV2State.LATE
    assert repeated.reason_codes == ("TERMINAL_EPISODE_IMMUTABLE",)
    assert expired.to_state is LifecycleV2State.EXPIRED
    assert invalidated.to_state is LifecycleV2State.INVALIDATED


def test_stale_or_expensive_setup_cannot_arm() -> None:
    stale = evaluate_lifecycle_v2_shadow(
        episode_id="episode-1",
        current_state=LifecycleV2State.PRE_TRIGGER,
        evidence=_evidence(
            oldest_required_observed_at=900,
            required_observed_at=(1_000, 999, 995, 900),
        ),
    )
    expensive = evaluate_lifecycle_v2_shadow(
        episode_id="episode-1",
        current_state=LifecycleV2State.PRE_TRIGGER,
        evidence=_evidence(
            estimated_round_trip_cost_r=0.16,
            executable_depth_multiple=9.9,
        ),
    )

    assert stale.to_state is LifecycleV2State.PRE_TRIGGER
    assert stale.reason_codes == ("DATA_STALE",)
    assert expensive.to_state is LifecycleV2State.PRE_TRIGGER
    assert expensive.reason_codes == (
        "ESTIMATED_COST_TOO_HIGH",
        "DEPTH_INSUFFICIENT",
    )


def test_v1_v2_comparison_is_hash_bound_and_never_promotes_or_mutates_v1() -> None:
    first = compare_v1_v2_shadow(
        episode_id="episode-1",
        v1_state="WATCH",
        v2_state=LifecycleV2State.WATCH,
        evidence=_evidence(),
    )
    second = compare_v1_v2_shadow(
        episode_id="episode-1",
        v1_state="WATCH",
        v2_state=LifecycleV2State.WATCH,
        evidence=_evidence(),
    )

    assert first == second
    assert first["v1_state_unchanged"] == "WATCH"
    assert first["v2_to_state"] == "FUEL_RICH"
    assert first["shadow_only"] is True
    assert first["promotion_allowed"] is False
    assert len(first["comparison_hash"]) == 64


def test_runtime_mapper_declares_missing_execution_facts_without_defaults() -> None:
    evidence = build_lifecycle_v2_evidence_from_metrics(
        metrics={
            "stage_lifecycle": {"confirmed": {"hype": True, "setup": True}},
            "candle_features": {
                "5m": {
                    "lower_high": True,
                    "distance_to_support_atr": 0.4,
                    "extension_from_support_atr": 0.2,
                },
                "15m": {
                    "lower_high": True,
                    "distance_to_support_atr": 0.5,
                    "extension_from_support_atr": 0.2,
                },
            },
            "microstructure": {
                "observed_at": 998,
                "footprint": {"aggressive_selling": True},
            },
            "relative_weakness_features": {
                "timeframes": {
                    "5m": {
                        "relative_return_3bars_pct": -1.0,
                        "relative_return_6bars_pct": -2.0,
                    }
                }
            },
            "breakdown_confirmation": {
                "primary_breakdown_confirmed": True,
                "confirmation_exchange_15m": True,
                "composite_breakdown_confirmed": True,
            },
            "trade_eligible": True,
        },
        decision_at=1_000,
        analysis_observed_at=1_000,
        reference_observed_at=999,
    )
    transition = evaluate_lifecycle_v2_shadow(
        episode_id="episode-runtime",
        current_state=LifecycleV2State.PRE_TRIGGER,
        evidence=evidence,
    )

    assert evidence.eligible_data is False
    assert "lbank_constraints_fresh" in evidence.unavailable_fields
    assert "estimated_round_trip_cost_r" in evidence.unavailable_fields
    assert evidence.estimated_round_trip_cost_r is None
    assert transition.to_state is LifecycleV2State.PRE_TRIGGER
    assert transition.reason_codes == ("INSUFFICIENT_EVIDENCE",)

    early_transition = evaluate_lifecycle_v2_shadow(
        episode_id="episode-runtime-early",
        current_state=LifecycleV2State.WATCH,
        evidence=evidence,
    )
    assert early_transition.to_state is LifecycleV2State.FUEL_RICH
    assert early_transition.reason_codes == ("FUEL_EXPANSION_CONFIRMED",)
    structure_transition = evaluate_lifecycle_v2_shadow(
        episode_id="episode-runtime-early",
        current_state=early_transition.to_state,
        evidence=evidence,
    )
    assert structure_transition.to_state is LifecycleV2State.PRE_TRIGGER


def test_registry_and_threshold_policy_are_content_addressed_and_not_silent() -> None:
    registry = lifecycle_feature_registry()
    policy = lifecycle_v2_policy_v1()

    assert registry["outcome_fields_allowed_as_features"] is False
    assert policy.threshold_status == "SHADOW_HYPOTHESES"
    assert policy.feature_registry_hash == registry["registry_hash"]
    assert policy.experimental_trigger_allowed is False
    tampered = policy.model_dump(mode="json")
    tampered["thresholds"]["late_atr"] = 9.0
    policy_type = type(policy)
    with pytest.raises(ValidationError, match="policy hash mismatch"):
        policy_type.model_validate(tampered)


def test_experimental_profile_cannot_arm_and_hard_antichase_block_is_terminal() -> None:
    experimental = evaluate_lifecycle_v2_shadow(
        episode_id="experimental",
        current_state=LifecycleV2State.PRE_TRIGGER,
        evidence=_evidence(evaluated_profile=LifecycleProfile.EXPERIMENTAL),
    )
    blocked = evaluate_lifecycle_v2_shadow(
        episode_id="hard-block",
        current_state=LifecycleV2State.PRE_TRIGGER,
        evidence=_evidence(extension_atr=1.0),
    )

    assert experimental.to_state is LifecycleV2State.PRE_TRIGGER
    assert experimental.reason_codes == ("EXPERIMENTAL_PROFILE_OBSERVATIONAL_ONLY",)
    assert experimental.strategy_profile is LifecycleProfile.EXPERIMENTAL
    assert blocked.to_state is LifecycleV2State.EXECUTION_BLOCKED
    assert blocked.reason_codes == ("ANTI_CHASE_HARD_BLOCK",)


def test_stale_evidence_cannot_create_an_antichase_terminal_state() -> None:
    transition = evaluate_lifecycle_v2_shadow(
        episode_id="stale-hard-block",
        current_state=LifecycleV2State.PRE_TRIGGER,
        evidence=_evidence(
            extension_atr=1.0,
            oldest_required_observed_at=900,
            required_observed_at=(1_000, 999, 995, 900),
        ),
    )

    assert transition.to_state is LifecycleV2State.PRE_TRIGGER
    assert transition.reason_codes == ("DATA_STALE",)


def test_v1_state_comparison_normalizes_equivalent_separator_vocabularies() -> None:
    comparison = compare_v1_v2_shadow(
        episode_id="normalized",
        v1_state="FUEL-RICH",
        v2_state=LifecycleV2State.WATCH,
        evidence=_evidence(),
    )

    assert comparison["v1_state_unchanged"] == "FUEL_RICH"
    assert comparison["v2_to_state"] == "FUEL_RICH"
    assert comparison["diverged"] is False


def test_every_required_source_timestamp_must_precede_the_decision_clock() -> None:
    with pytest.raises(ValidationError, match="source timestamp"):
        _evidence(required_observed_at=(1_001, 999, 995, 990))


def test_unavailable_footprint_is_missing_and_breakdowns_are_one_family() -> None:
    evidence = build_lifecycle_v2_evidence_from_metrics(
        metrics={
            "stage_lifecycle": {"confirmed": {"hype": True, "setup": True}},
            "candle_features": {
                "5m": {
                    "lower_high": True,
                    "distance_to_support_atr": 0.4,
                    "extension_from_support_atr": 0.2,
                }
            },
            "microstructure": {
                "observed_at": 998.0,
                "footprint": {"available": False, "aggressive_selling": False},
            },
            "relative_weakness_features": {
                "timeframes": {
                    "5m": {
                        "relative_return_3bars_pct": -1.0,
                        "relative_return_6bars_pct": -2.0,
                    }
                }
            },
            "breakdown_confirmation": {
                "primary_breakdown_confirmed": True,
                "confirmation_exchange_15m": True,
                "composite_breakdown_confirmed": True,
            },
            "trade_eligible": True,
        },
        decision_at=1_000,
        analysis_observed_at=1_000,
        reference_observed_at=999,
    )

    assert evidence.flow_family_pass is None
    assert "flow_family_pass" in evidence.unavailable_fields
    assert evidence.confirmation_count == 2
    assert evidence.confirmation_family_count == 1
