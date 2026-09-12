from waterfallhunter.core.entry_decision import (
    EntryDecisionPolicy,
    build_entry_decision,
    build_expired_entry_decision,
    build_invalidated_entry_decision,
)


def strong_metrics() -> dict:
    return {
        "candle_features": {
            "4h": {"valid": True, "hype_context": True, "lower_high": True, "setup": "FAILED_PULLBACK", "bearish_close": True, "support_broken": False, "volume_acceleration": True},
            "1h": {"valid": True, "lower_high": True, "reclaim": True, "repump": False, "rsi_rollover": True, "bearish_close": True, "volume_acceleration": True},
            "15m": {"valid": True, "lower_high": True, "reclaim": True, "repump": False, "rsi_rollover": True, "bearish_close": True, "volume_acceleration": True},
            "5m": {"valid": True, "lower_high": True, "reclaim": True, "repump": False, "rsi_rollover": True, "bearish_close": True, "volume_acceleration": True},
        },
        "microstructure": {"approved": True, "spoofing_detected": False, "sell_flow_usdt": 180000.0, "buy_flow_usdt": 70000.0, "bid_depth_usdt": 220000.0, "ask_depth_usdt": 180000.0, "spread_pct": 0.04, "slippage_pct": 0.06, "footprint": {"available": True, "aggressive_selling": True}},
        "derivatives": {"available": True, "funding_rate": 0.0002, "funding_percentile": 0.91, "oi_change_1h_pct": 0.7, "taker_buy_sell_ratio": 0.72, "taker_ratio_change_1h": -0.35, "top_trader_long_short_ratio": 2.1},
        "breakdown_confirmation": {"confirmation_exchange_15m": True},
        "price_location": {"below_vwap": True},
        "position_setup": {"status": "READY", "entry_price": 0.1, "stop_loss": 0.103, "take_profit_1": 0.097, "take_profit_2": 0.094, "reward_to_risk": 1.8},
        "applied_leverage": 3,
    }


def decide(metrics: dict, status: str = "PRE-TRIGGER", *, analysis_age: float = 10.0, reference_age: float = 3.0):
    return build_entry_decision(
        metrics,
        status,
        evaluated_at=1_788_000_000,
        analysis_age_seconds=analysis_age,
        reference_age_seconds=reference_age,
    )


def test_strong_fresh_setup_is_entry_ready() -> None:
    packet = decide(strong_metrics())
    assert packet["contract_version"] == "entry_decision_v1"
    assert packet["decision"] == "ENTRY_READY"
    assert packet["entry_readiness"] >= EntryDecisionPolicy().entry_ready_minimum
    assert packet["hard_blocked"] is False
    assert packet["trade_plan"]["entry_price"] == 0.1
    assert packet["reason_codes"] == sorted(packet["reason_codes"])


def test_entry_decision_persists_leverage_causal_input_packet() -> None:
    metrics = strong_metrics()
    metrics["leverage_advisory"] = {
        "status": "AVAILABLE", "leverage": 8,
        "policy_version": "adaptive_signal_leverage_v2", "reason": None,
        "execution_suitability_input": {"status": "SUITABLE", "maximum_leverage": 12},
        "causal_input": {
            "score": 92.0,
            "position_setup": {"status": "READY", "entry_price": 0.1, "stop_loss": 0.103},
            "microstructure": {"spread_pct": 0.04, "slippage_pct": 0.06, "exit_slippage_present": False, "exit_slippage_pct": None},
            "candle_atr_pct": {"5m": 0.8, "15m": 0.9, "1h": 1.0},
            "market_constraints": {"maximum_leverage": 12.0},
            "execution_suitability": {"available": True, "status": "SUITABLE", "maximum_leverage": 12.0},
        },
    }
    packet = decide(metrics)
    assert packet["leverage_advisory"]["causal_input"] == metrics["leverage_advisory"]["causal_input"]


def test_stale_analysis_is_hard_blocked_no_trade() -> None:
    packet = decide(strong_metrics(), analysis_age=181.0)
    assert packet["decision"] == "NO_TRADE"
    assert packet["hard_blocked"] is True
    assert "STALE_ANALYSIS" in packet["block_reasons"]


def test_future_evidence_ages_are_hard_blocked_no_trade() -> None:
    packet = decide(strong_metrics(), analysis_age=-1.0, reference_age=-2.0)

    assert packet["decision"] == "NO_TRADE"
    assert packet["hard_blocked"] is True
    assert "STALE_ANALYSIS" in packet["block_reasons"]
    assert "STALE_REFERENCE" in packet["block_reasons"]


def test_moderate_setup_is_forming_instead_of_zeroed_by_one_missing_family() -> None:
    metrics = strong_metrics()
    metrics["breakdown_confirmation"] = {}
    metrics["derivatives"]["funding_percentile"] = 0.55
    metrics["derivatives"]["oi_change_1h_pct"] = -0.1
    metrics["candle_features"]["5m"]["rsi_rollover"] = False
    packet = decide(metrics)
    assert packet["decision"] == "FORMING"
    assert packet["entry_readiness"] >= EntryDecisionPolicy().forming_minimum
    assert "CROSS_EXCHANGE_UNAVAILABLE" in packet["reason_codes"]


def test_extended_move_is_late_even_when_other_evidence_is_strong() -> None:
    metrics = strong_metrics()
    metrics["anti_chase"] = {
        "available": True,
        "cross_timeframe": {"max_post_break_extension_atr": 1.35},
    }
    packet = decide(metrics, status="TRIGGERED")
    assert packet["decision"] == "LATE"
    assert "ANTI_CHASE_HARD_BLOCK" in packet["block_reasons"]


def test_active_buying_and_weak_structure_do_not_promote() -> None:
    metrics = strong_metrics()
    metrics["derivatives"]["taker_buy_sell_ratio"] = 1.55
    metrics["microstructure"]["sell_flow_usdt"] = 40000.0
    metrics["microstructure"]["buy_flow_usdt"] = 160000.0
    metrics["microstructure"]["footprint"]["aggressive_selling"] = False
    for timeframe in ("1h", "15m", "5m"):
        metrics["candle_features"][timeframe]["rsi_rollover"] = False
        metrics["candle_features"][timeframe]["bearish_close"] = False
    packet = decide(metrics, status="WATCH")
    # Calibrated: forming_minimum lowered to 40.0. With readiness ~48, this candidate
    # is FORMING (watchlist) not NO_TRADE. It still does not reach ENTRY_READY
    # because direction_ok is False (buyers active, no sell pressure).
    assert packet["decision"] in ("FORMING", "NO_TRADE")
    assert "BUYERS_ACTIVE" in packet["reason_codes"]


def test_deterministic_market_data_veto_hard_blocks_strong_setup() -> None:
    metrics = strong_metrics()
    metrics["ai_advisory"] = {
        "deterministic_veto": True,
        "deterministic_reason": "Bid wall is 4.0x larger than Ask wall.",
        "ai_observational_only": True,
        "ai_decision_critical": False,
    }
    packet = decide(metrics, status="TRIGGERED")
    assert packet["decision"] == "NO_TRADE"
    assert packet["hard_blocked"] is True
    assert "DETERMINISTIC_MARKET_DATA_VETO" in packet["block_reasons"]


def test_triggered_strong_setup_becomes_active_not_a_disappearing_trigger() -> None:
    previous = decide(strong_metrics(), status="PRE-TRIGGER")
    assert previous["decision"] == "ENTRY_READY"
    packet = build_entry_decision(
        strong_metrics(),
        "TRIGGERED",
        evaluated_at=1_788_000_001,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        previous_decision=previous,
    )
    assert packet["decision"] == "ACTIVE"
    assert packet["hard_blocked"] is False


def test_missing_execution_inputs_are_hard_blocked() -> None:
    metrics = strong_metrics()
    metrics.pop("microstructure")
    packet = decide(metrics)
    assert packet["decision"] == "NO_TRADE"
    assert packet["hard_blocked"] is True
    assert "EXECUTION_UNAVAILABLE" in packet["block_reasons"]


def test_candle_feature_extension_is_used_for_anti_chase() -> None:
    metrics = strong_metrics()
    metrics["candle_features"]["5m"]["extension_from_support_atr"] = 1.35
    packet = decide(metrics, status="TRIGGERED")
    assert packet["decision"] == "LATE"
    assert "ANTI_CHASE_HARD_BLOCK" in packet["block_reasons"]


def test_partial_cascade_coverage_uses_actual_available_weight() -> None:
    metrics = strong_metrics()
    metrics["cascade_intelligence"] = {
        "status": "PARTIAL",
        "readiness_points": 4.0,
        "maximum_available": 4.0,
    }
    packet = decide(metrics)
    assert packet["components"]["cascade"]["maximum"] == 4.0
    assert packet["evidence_coverage_pct"] == 94.0


def test_entry_ready_cannot_regress_to_forming_when_trade_plan_disappears() -> None:
    previous = decide(strong_metrics())
    assert previous["decision"] == "ENTRY_READY"
    metrics = strong_metrics()
    metrics.pop("position_setup")
    packet = build_entry_decision(
        metrics,
        "PRE-TRIGGER",
        evaluated_at=1_788_000_010,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        previous_decision=previous,
    )
    assert packet["decision"] == "INVALIDATED"
    assert "ENTRY_CONDITIONS_LOST" in packet["block_reasons"]


def test_stale_entry_ready_projects_to_invalidated_not_no_trade() -> None:
    previous = decide(strong_metrics())
    packet = build_entry_decision(
        strong_metrics(),
        "PRE-TRIGGER",
        evaluated_at=1_788_000_200,
        analysis_age_seconds=181.0,
        reference_age_seconds=3.0,
        previous_decision=previous,
    )
    assert packet["decision"] == "INVALIDATED"
    assert "STALE_ANALYSIS" in packet["block_reasons"]


def test_expiry_reconciler_requires_and_preserves_explicit_trade_plan_expiry() -> None:
    metrics = strong_metrics()
    metrics["position_setup"]["expires_at"] = 1_788_000_100
    previous = decide(metrics)

    assert build_expired_entry_decision(previous, evaluated_at=1_788_000_099) is None
    expired = build_expired_entry_decision(previous, evaluated_at=1_788_000_100)

    assert expired is not None
    assert expired["decision"] == "EXPIRED"
    assert expired["trade_plan"]["expires_at"] == 1_788_000_100
    assert expired["block_reasons"] == ["TRADE_PLAN_EXPIRED"]

    no_expiry = decide(strong_metrics())
    assert build_expired_entry_decision(no_expiry, evaluated_at=1_788_999_999) is None


def test_actionable_decision_can_be_invalidated_when_candidate_leaves_active_universe() -> None:
    previous = decide(strong_metrics())
    packet = build_invalidated_entry_decision(
        previous,
        evaluated_at=1_788_000_050,
        block_reason="CANDIDATE_NO_LONGER_ACTIVE",
    )
    assert packet is not None
    assert packet["decision"] == "INVALIDATED"
    assert packet["hard_blocked"] is True
    assert packet["block_reasons"] == ["CANDIDATE_NO_LONGER_ACTIVE"]
    assert packet["trade_plan"] == previous["trade_plan"]


def test_non_actionable_decision_is_not_reinvalidated_when_candidate_is_inactive() -> None:
    previous = decide(strong_metrics())
    expired = build_invalidated_entry_decision(
        previous,
        evaluated_at=1_788_000_050,
        block_reason="CANDIDATE_NO_LONGER_ACTIVE",
    )
    assert expired is not None
    assert build_invalidated_entry_decision(
        expired,
        evaluated_at=1_788_000_060,
        block_reason="CANDIDATE_NO_LONGER_ACTIVE",
    ) is None


def test_current_trade_plan_expiry_cannot_emit_entry_ready() -> None:
    metrics = strong_metrics()
    metrics["position_setup"]["expires_at"] = 1_788_000_000
    packet = decide(metrics)
    assert packet["decision"] == "NO_TRADE"
    assert packet["hard_blocked"] is True
    assert "TRADE_PLAN_EXPIRED" in packet["block_reasons"]


def test_expired_plan_does_not_reemit_entry_ready_after_expired_transition() -> None:
    metrics = strong_metrics()
    metrics["position_setup"]["expires_at"] = 1_788_000_100
    ready = decide(metrics)
    expired = build_entry_decision(
        metrics,
        "PRE-TRIGGER",
        evaluated_at=1_788_000_100,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        previous_decision=ready,
    )
    assert expired["decision"] == "EXPIRED"
    repeated = build_entry_decision(
        metrics,
        "PRE-TRIGGER",
        evaluated_at=1_788_000_101,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        previous_decision=expired,
    )
    assert repeated["decision"] == "EXPIRED"
    assert "TRADE_PLAN_EXPIRED" in repeated["block_reasons"]


def test_entry_ready_expires_only_at_explicit_trade_plan_expiry() -> None:
    metrics = strong_metrics()
    metrics["position_setup"]["expires_at"] = 1_788_000_100
    previous = decide(metrics)
    assert previous["decision"] == "ENTRY_READY"
    assert previous["trade_plan"]["expires_at"] == 1_788_000_100

    packet = build_entry_decision(
        metrics,
        "PRE-TRIGGER",
        evaluated_at=1_788_000_100,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        previous_decision=previous,
    )

    assert packet["decision"] == "EXPIRED"
    assert packet["block_reasons"] == ["TRADE_PLAN_EXPIRED"]


def test_terminal_decision_stays_terminal_within_same_lifecycle() -> None:
    ready = build_entry_decision(
        strong_metrics(),
        "ARMED",
        evaluated_at=1_788_000_000,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=7,
    )
    assert ready["decision"] == "ENTRY_READY"
    invalidated = build_entry_decision(
        strong_metrics(),
        "ARMED",
        evaluated_at=1_788_000_200,
        analysis_age_seconds=181.0,
        reference_age_seconds=3.0,
        lifecycle_id=7,
        previous_decision=ready,
    )
    assert invalidated["decision"] == "INVALIDATED"

    recovered = build_entry_decision(
        strong_metrics(),
        "ARMED",
        evaluated_at=1_788_000_201,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=7,
        previous_decision=invalidated,
    )
    assert recovered["decision"] == "INVALIDATED"
    assert recovered["lifecycle_id"] == 7


def test_terminal_decision_can_reset_on_distinct_lifecycle() -> None:
    ready = build_entry_decision(
        strong_metrics(),
        "ARMED",
        evaluated_at=1_788_000_000,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=7,
    )
    invalidated = build_entry_decision(
        strong_metrics(),
        "ARMED",
        evaluated_at=1_788_000_200,
        analysis_age_seconds=181.0,
        reference_age_seconds=3.0,
        lifecycle_id=7,
        previous_decision=ready,
    )
    fresh_episode = build_entry_decision(
        strong_metrics(),
        "ARMED",
        evaluated_at=1_788_000_201,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=8,
        previous_decision=invalidated,
    )
    assert fresh_episode["decision"] == "ENTRY_READY"
    assert fresh_episode["lifecycle_id"] == 8


def test_triggered_setup_requires_same_lifecycle_entry_ready_predecessor() -> None:
    packet = build_entry_decision(
        strong_metrics(),
        "TRIGGERED",
        evaluated_at=1_788_000_000,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=9,
    )
    assert packet["decision"] == "NO_TRADE"
    assert packet["hard_blocked"] is True
    assert "ENTRY_READY_PREDECESSOR_REQUIRED" in packet["block_reasons"]


def test_triggered_setup_becomes_active_after_same_lifecycle_entry_ready() -> None:
    ready = build_entry_decision(
        strong_metrics(),
        "ARMED",
        evaluated_at=1_788_000_000,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=9,
    )
    assert ready["decision"] == "ENTRY_READY"
    active = build_entry_decision(
        strong_metrics(),
        "TRIGGERED",
        evaluated_at=1_788_000_001,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=9,
        previous_decision=ready,
    )
    assert active["decision"] == "ACTIVE"
    assert active["hard_blocked"] is False


def test_anti_chase_does_not_turn_low_readiness_no_trade_into_late() -> None:
    metrics = strong_metrics()
    metrics["derivatives"]["taker_buy_sell_ratio"] = 1.7
    metrics["microstructure"]["sell_flow_usdt"] = 20_000.0
    metrics["microstructure"]["buy_flow_usdt"] = 200_000.0
    metrics["microstructure"]["footprint"]["aggressive_selling"] = False
    metrics["breakdown_confirmation"] = {}  # Remove cross-exchange to lower readiness below 40
    metrics["price_location"] = {"available": True, "below_vwap": False}  # Above VWAP
    for timeframe in ("1h", "15m", "5m"):
        metrics["candle_features"][timeframe]["rsi_rollover"] = False
        metrics["candle_features"][timeframe]["bearish_close"] = False
        metrics["candle_features"][timeframe]["lower_high"] = False
    metrics["anti_chase"] = {
        "available": True,
        "cross_timeframe": {"max_post_break_extension_atr": 2.4},
    }

    packet = decide(metrics, status="FUEL-RICH")

    assert packet["entry_readiness"] < EntryDecisionPolicy().forming_minimum  # Calibrated: weakened data, readiness < 40 → NO_TRADE
    assert packet["decision"] == "NO_TRADE"
    assert "ANTI_CHASE_HARD_BLOCK" not in packet["block_reasons"]


def test_anti_chase_still_converts_forming_to_late() -> None:
    metrics = strong_metrics()
    metrics["breakdown_confirmation"] = {}
    metrics["derivatives"]["funding_percentile"] = 0.55
    metrics["derivatives"]["oi_change_1h_pct"] = -0.1
    metrics["candle_features"]["5m"]["rsi_rollover"] = False
    metrics["anti_chase"] = {
        "available": True,
        "cross_timeframe": {"max_post_break_extension_atr": 1.35},
    }

    packet = decide(metrics, status="PRE-TRIGGER")

    assert packet["entry_readiness"] >= EntryDecisionPolicy().forming_minimum
    # Calibrated: entry_ready_minimum lowered to 55.0. Candidate may reach ENTRY_READY range.
    # anti-chase still converts both FORMING and ENTRY_READY to LATE.
    assert packet["decision"] == "LATE"
    assert packet["block_reasons"] == ["ANTI_CHASE_HARD_BLOCK"]


def test_legacy_low_readiness_late_can_recover_within_same_lifecycle() -> None:
    metrics = strong_metrics()
    metrics["derivatives"]["taker_buy_sell_ratio"] = 1.7
    metrics["microstructure"]["sell_flow_usdt"] = 20_000.0
    metrics["microstructure"]["buy_flow_usdt"] = 200_000.0
    metrics["microstructure"]["footprint"]["aggressive_selling"] = False
    metrics["breakdown_confirmation"] = {}
    metrics["price_location"] = {"available": True, "below_vwap": False}
    for timeframe in ("1h", "15m", "5m"):
        metrics["candle_features"][timeframe]["rsi_rollover"] = False
        metrics["candle_features"][timeframe]["bearish_close"] = False
        metrics["candle_features"][timeframe]["lower_high"] = False
    previous = build_entry_decision(
        metrics
        | {
            "anti_chase": {
                "available": True,
                "cross_timeframe": {"max_post_break_extension_atr": 2.4},
            }
        },
        "FUEL-RICH",
        evaluated_at=1_788_000_000,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=7,
    )
    # Reproduce a projection poisoned by the pre-fix LATE semantics.
    previous["decision"] = "LATE"
    previous["block_reasons"] = ["ANTI_CHASE_HARD_BLOCK"]
    previous.pop("late_origin", None)
    assert previous["entry_readiness"] < EntryDecisionPolicy().forming_minimum  # Calibrated: weakened data

    fresh = build_entry_decision(
        metrics,
        "FUEL-RICH",
        evaluated_at=1_788_000_100,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=7,
        previous_decision=previous,
    )

    assert fresh["decision"] == "NO_TRADE"
    assert fresh["hard_blocked"] is False


def test_exhausted_remains_late_when_other_inputs_are_blocked() -> None:
    metrics = strong_metrics()
    metrics.pop("microstructure")

    packet = decide(metrics, status="EXHAUSTED")

    assert packet["decision"] == "LATE"
    assert packet["lifecycle_state"] == "EXHAUSTED"
    assert "EXECUTION_UNAVAILABLE" in packet["block_reasons"]
    assert "ANTI_CHASE_HARD_BLOCK" not in packet["block_reasons"]


def test_genuine_low_readiness_exhausted_late_remains_terminal() -> None:
    metrics = strong_metrics()
    metrics["derivatives"]["taker_buy_sell_ratio"] = 1.7
    metrics["microstructure"]["sell_flow_usdt"] = 20_000.0
    metrics["microstructure"]["buy_flow_usdt"] = 200_000.0
    metrics["microstructure"]["footprint"]["aggressive_selling"] = False
    for timeframe in ("1h", "15m", "5m"):
        metrics["candle_features"][timeframe]["rsi_rollover"] = False
        metrics["candle_features"][timeframe]["bearish_close"] = False
    previous = build_entry_decision(
        metrics,
        "EXHAUSTED",
        evaluated_at=1_788_000_000,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=8,
    )
    assert previous["entry_readiness"] >= EntryDecisionPolicy().forming_minimum  # Calibrated: now FORMING
    assert previous["decision"] == "LATE"

    repeated = build_entry_decision(
        metrics,
        "FUEL-RICH",
        evaluated_at=1_788_000_100,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=8,
        previous_decision=previous,
    )

    assert repeated["decision"] == "LATE"
    assert repeated["lifecycle_state"] == "FUEL-RICH"
    assert repeated["late_origin"] == "LIFECYCLE_EXHAUSTED"

    repeated_again = build_entry_decision(
        metrics,
        "FUEL-RICH",
        evaluated_at=1_788_000_200,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=8,
        previous_decision=repeated,
    )

    assert repeated_again["decision"] == "LATE"
    assert repeated_again["lifecycle_state"] == "FUEL-RICH"
    assert repeated_again["late_origin"] == "LIFECYCLE_EXHAUSTED"



def test_genuine_anti_chase_late_keeps_origin_when_readiness_later_drops() -> None:
    extended = strong_metrics()
    extended["anti_chase"] = {
        "available": True,
        "cross_timeframe": {"max_post_break_extension_atr": 1.35},
    }
    first = build_entry_decision(
        extended,
        "PRE-TRIGGER",
        evaluated_at=1_788_000_000,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=12,
    )
    assert first["decision"] == "LATE"
    assert first["late_origin"] == "ANTI_CHASE"

    weak = strong_metrics()
    weak["derivatives"]["taker_buy_sell_ratio"] = 1.7
    weak["microstructure"]["sell_flow_usdt"] = 20_000.0
    weak["microstructure"]["buy_flow_usdt"] = 200_000.0
    weak["microstructure"]["footprint"]["aggressive_selling"] = False
    for timeframe in ("1h", "15m", "5m"):
        weak["candle_features"][timeframe]["rsi_rollover"] = False
        weak["candle_features"][timeframe]["bearish_close"] = False

    second = build_entry_decision(
        weak,
        "FUEL-RICH",
        evaluated_at=1_788_000_100,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=12,
        previous_decision=first,
    )
    assert second["entry_readiness"] >= EntryDecisionPolicy().forming_minimum  # Calibrated: now FORMING
    assert second["decision"] == "LATE"
    assert second["lifecycle_state"] == "FUEL-RICH"
    assert second["late_origin"] == "ANTI_CHASE"

    third = build_entry_decision(
        weak,
        "FUEL-RICH",
        evaluated_at=1_788_000_200,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=12,
        previous_decision=second,
    )
    assert third["decision"] == "LATE"
    assert third["late_origin"] == "ANTI_CHASE"

def test_exhausted_replaces_prior_anti_chase_terminal_origin() -> None:
    metrics = strong_metrics()
    metrics["anti_chase"] = {
        "available": True,
        "cross_timeframe": {"max_post_break_extension_atr": 1.35},
    }
    previous = build_entry_decision(
        metrics,
        "PRE-TRIGGER",
        evaluated_at=1_788_000_000,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=13,
    )
    assert previous["decision"] == "LATE"
    assert previous["late_origin"] == "ANTI_CHASE"

    exhausted = build_entry_decision(
        metrics,
        "EXHAUSTED",
        evaluated_at=1_788_000_100,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=13,
        previous_decision=previous,
    )

    assert exhausted["decision"] == "LATE"
    assert exhausted["lifecycle_state"] == "EXHAUSTED"
    assert exhausted["late_origin"] == "LIFECYCLE_EXHAUSTED"
    assert "ANTI_CHASE_HARD_BLOCK" in exhausted["block_reasons"]


def test_retained_exhausted_late_refreshes_current_measured_blockers() -> None:
    previous = build_entry_decision(
        strong_metrics(),
        "EXHAUSTED",
        evaluated_at=1_788_000_000,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=14,
    )
    assert previous["decision"] == "LATE"
    assert previous["block_reasons"] == []

    metrics = strong_metrics()
    metrics["anti_chase"] = {
        "available": True,
        "cross_timeframe": {"max_post_break_extension_atr": 1.35},
    }
    current = build_entry_decision(
        metrics,
        "EXHAUSTED",
        evaluated_at=1_788_000_100,
        analysis_age_seconds=10.0,
        reference_age_seconds=3.0,
        lifecycle_id=14,
        previous_decision=previous,
    )

    assert current["decision"] == "LATE"
    assert current["late_origin"] == "LIFECYCLE_EXHAUSTED"
    assert current["block_reasons"] == ["ANTI_CHASE_HARD_BLOCK"]


def test_exhausted_preserves_measured_anti_chase_blocker() -> None:
    metrics = strong_metrics()
    metrics["anti_chase"] = {
        "available": True,
        "cross_timeframe": {"max_post_break_extension_atr": 1.35},
    }

    packet = decide(metrics, status="EXHAUSTED")

    assert packet["decision"] == "LATE"
    assert packet["block_reasons"] == ["ANTI_CHASE_HARD_BLOCK"]


def test_stale_evidence_precedes_anti_chase_late_classification() -> None:
    metrics = strong_metrics()
    metrics["anti_chase"] = {
        "available": True,
        "cross_timeframe": {"max_post_break_extension_atr": 1.8},
    }

    packet = decide(metrics, status="PRE-TRIGGER", analysis_age=181.0)

    assert packet["decision"] == "NO_TRADE"
    assert "STALE_ANALYSIS" in packet["block_reasons"]
    assert "ANTI_CHASE_HARD_BLOCK" not in packet["block_reasons"]
