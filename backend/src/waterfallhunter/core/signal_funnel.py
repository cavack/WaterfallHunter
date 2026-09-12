from collections import Counter
from typing import Any


class SignalFunnel:
    """Read-only diagnostics over the current candidate snapshot."""

    VERSION = "signal_funnel_observational_v2"
    LIFECYCLE_STATES = (
        "WATCH",
        "FUEL-RICH",
        "PRE-TRIGGER",
        "ARMED",
        "TRIGGERED",
    )
    STAGES = (
        "hype",
        "damage",
        "setup",
        "trigger",
        "passed",
    )
    QUALITY_GATES = (
        "live_orderbook",
        "all_timeframes_valid",
        "complete_candle_packet",
        "complete_microstructure_packet",
        "complete_fresh_derivatives_packet",
        "taker_sell_dominance",
        "cross_exchange_confirmed",
        "complete_price_location",
        "channel_stage_chain",
    )

    @staticmethod
    def _tri_state(values: list[Any]) -> dict[str, int | float | None]:
        passed = sum(value is True for value in values)
        failed = sum(value is False for value in values)
        unavailable = len(values) - passed - failed
        evaluated = passed + failed
        return {
            "passed": passed,
            "failed": failed,
            "unavailable": unavailable,
            "evaluated": evaluated,
            "pass_rate": (
                round(passed / evaluated, 6)
                if evaluated
                else None
            ),
        }

    @classmethod
    def build(
        cls,
        candidates: dict[str, dict],
        *,
        generated_at: float | None = None,
        attention_min_sample: int = 20,
    ) -> dict:
        rows = candidates if isinstance(candidates, dict) else {}
        lifecycle = Counter()
        reasons = Counter()
        stage_values = {stage: [] for stage in cls.STAGES}
        lifecycle_stage_values = {stage: [] for stage in cls.STAGES}
        lifecycle_stage_members = {stage: [] for stage in cls.STAGES}
        lifecycle_available: list[Any] = []
        gate_values = {gate: [] for gate in cls.QUALITY_GATES}
        breakdown_values = {
            "primary_breakdown_confirmed": [],
            "confirmation_exchange_15m": [],
            "composite_breakdown_confirmed": [],
        }
        microstructure_values: list[Any] = []
        analysis_available = 0

        for symbol, candidate in rows.items():
            packet = candidate if isinstance(candidate, dict) else {}
            status = packet.get("status")
            lifecycle[
                status if status in cls.LIFECYCLE_STATES else "OTHER"
            ] += 1

            metrics = packet.get("metrics")
            if not isinstance(metrics, dict) or not metrics:
                reasons["analysis unavailable"] += 1
                for values in stage_values.values():
                    values.append(None)
                for values in lifecycle_stage_values.values():
                    values.append(None)
                lifecycle_available.append(None)
                for values in gate_values.values():
                    values.append(None)
                for values in breakdown_values.values():
                    values.append(None)
                microstructure_values.append(None)
                continue

            analysis_available += 1
            reason = metrics.get("analysis_reason")
            reasons[
                str(reason) if reason else "analysis complete"
            ] += 1

            stages = metrics.get("strategy_stages")
            stage_lifecycle = metrics.get("stage_lifecycle")
            gates = metrics.get("quality_gates")
            microstructure = metrics.get("microstructure")
            breakdown = metrics.get("breakdown_confirmation")
            for stage in cls.STAGES:
                stage_values[stage].append(
                    stages.get(stage) if isinstance(stages, dict) else None
                )
            confirmed_lifecycle = (
                stage_lifecycle.get("confirmed")
                if isinstance(stage_lifecycle, dict)
                else None
            )
            lifecycle_available.append(
                stage_lifecycle.get("available")
                if isinstance(stage_lifecycle, dict)
                else None
            )
            for stage in cls.STAGES:
                value = (
                    confirmed_lifecycle.get(stage)
                    if isinstance(confirmed_lifecycle, dict)
                    else None
                )
                lifecycle_stage_values[stage].append(value)
                if value is True:
                    lifecycle_stage_members[stage].append(str(symbol))
            for gate in cls.QUALITY_GATES:
                gate_values[gate].append(
                    gates.get(gate) if isinstance(gates, dict) else None
                )
            microstructure_values.append(
                microstructure.get("approved")
                if isinstance(microstructure, dict)
                else None
            )
            for field in breakdown_values:
                breakdown_values[field].append(
                    breakdown.get(field)
                    if isinstance(breakdown, dict)
                    else None
                )

        lifecycle_packet = {
            state: lifecycle.get(state, 0)
            for state in (*cls.LIFECYCLE_STATES, "OTHER")
        }
        stages_packet = {
            stage: cls._tri_state(values)
            for stage, values in stage_values.items()
        }
        lifecycle_stages_packet = {
            stage: cls._tri_state(values)
            for stage, values in lifecycle_stage_values.items()
        }
        lifecycle_members_packet = {
            stage: sorted(symbols)
            for stage, symbols in lifecycle_stage_members.items()
        }
        gates_packet = {
            gate: cls._tri_state(values)
            for gate, values in gate_values.items()
        }
        gates_packet["microstructure_approved"] = cls._tri_state(
            microstructure_values
        )
        breakdown_packet = {
            field: cls._tri_state(values)
            for field, values in breakdown_values.items()
        }

        cross_exchange = breakdown_packet["confirmation_exchange_15m"]
        minimum = max(1, int(attention_min_sample))
        systemic_zero = bool(
            cross_exchange["evaluated"] >= minimum
            and cross_exchange["passed"] == 0
            and cross_exchange["failed"] == cross_exchange["evaluated"]
        )

        return {
            "version": cls.VERSION,
            "generated_at": generated_at,
            "observational_only": True,
            "hard_gating_allowed": False,
            "candidate_count": len(rows),
            "analysis": {
                "available": analysis_available,
                "unavailable": len(rows) - analysis_available,
                "reasons": dict(
                    sorted(
                        reasons.items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                ),
            },
            "lifecycle": lifecycle_packet,
            "stages": stages_packet,
            "stage_lifecycle": {
                "version": "stage_lifecycle_v1",
                "observational_only": True,
                "hard_gating_allowed": False,
                "availability": cls._tri_state(lifecycle_available),
                "stages": lifecycle_stages_packet,
                "members": lifecycle_members_packet,
            },
            "quality_gates": gates_packet,
            "breakdown_evidence": breakdown_packet,
            "attention": {
                "required": systemic_zero,
                "cross_exchange_systemic_zero": systemic_zero,
                "minimum_sample": minimum,
                "reason": (
                    "cross_exchange_confirmed has zero passes across the evaluated snapshot"
                    if systemic_zero
                    else None
                ),
            },
        }
