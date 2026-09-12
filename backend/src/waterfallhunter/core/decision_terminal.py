"""Decision-first dashboard projection for canonical WaterfallHunter signals."""

from __future__ import annotations

from collections import Counter
from typing import Any


_DIAGNOSTIC_REASONS = frozenset({
    "STRUCTURE_UNAVAILABLE", "STRUCTURE_WEAK", "TIMING_UNAVAILABLE",
    "TIMING_INCOMPLETE", "BUYERS_ACTIVE", "DERIVATIVES_UNAVAILABLE",
    "SHORT_SQUEEZE_RISK", "OI_UNWINDING", "EXECUTION_DEGRADED",
    "CROSS_EXCHANGE_UNAVAILABLE", "CROSS_EXCHANGE_DISAGREEMENT",
    "PRICE_LOCATION_UNAVAILABLE", "ABOVE_VWAP", "CASCADE_EVIDENCE_UNAVAILABLE",
    "TRADE_PLAN_UNAVAILABLE",
})

_SYSTEMIC_UNAVAILABLE_REASONS = frozenset({
    "STRUCTURE_UNAVAILABLE",
    "TIMING_UNAVAILABLE",
    "DERIVATIVES_UNAVAILABLE",
    "EXECUTION_UNAVAILABLE",
    "CROSS_EXCHANGE_UNAVAILABLE",
    "PRICE_LOCATION_UNAVAILABLE",
    "CASCADE_EVIDENCE_UNAVAILABLE",
})

_DECISIONS = (
    "ENTRY_READY",
    "FORMING",
    "ACTIVE",
    "LATE",
    "INVALIDATED",
    "EXPIRED",
    "NO_TRADE",
    "UNAVAILABLE",
)


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _decision(candidate: dict[str, Any]) -> tuple[str, float]:
    metrics = _record(candidate.get("metrics"))
    packet = _record(metrics.get("entry_decision"))
    decision = str(packet.get("decision") or "UNAVAILABLE").upper()
    if decision not in _DECISIONS:
        decision = "UNAVAILABLE"
    readiness = packet.get("entry_readiness")
    if isinstance(readiness, bool) or not isinstance(readiness, (int, float)):
        readiness = -1.0
    return decision, float(readiness)


def _diagnostic_reasons(candidate: dict[str, Any]) -> set[str]:
    packet = _record(_record(candidate.get("metrics")).get("entry_decision"))
    reasons = {
        str(reason)
        for reason in packet.get("reason_codes", ())
        if isinstance(reason, str) and reason in _DIAGNOSTIC_REASONS
    }
    reasons.update(
        str(reason)
        for reason in packet.get("block_reasons", ())
        if isinstance(reason, str)
    )
    return reasons


def _zero_entry_ready_diagnostics(
    candidates: dict[str, dict[str, Any]],
    *,
    entry_ready_count: int,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for candidate in candidates.values():
        counts.update(_diagnostic_reasons(candidate))
    total = len(candidates)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    top = [
        {"reason": reason, "count": count, "share_pct": round(100.0 * count / total, 1) if total else 0.0}
        for reason, count in ordered[:8]
    ] if entry_ready_count == 0 else []
    systemic = [
        {"reason": reason, "count": count, "share_pct": 100.0}
        for reason, count in ordered
        if total > 0 and count == total and reason in _SYSTEMIC_UNAVAILABLE_REASONS
    ]
    return {
        "entry_ready_zero": entry_ready_count == 0,
        "evaluated_candidates": total,
        "top_reasons": top,
        "pipeline_degraded": bool(systemic),
        "systemic_unavailable_reasons": systemic,
    }


def build_decision_terminal(
    candidates: dict[str, dict[str, Any]],
    *,
    recent_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    if any(not isinstance(symbol, str) or not symbol.strip() for symbol in candidates):
        raise ValueError("decision terminal candidate symbol must be non-empty")
    ranked: dict[str, list[tuple[str, float]]] = {
        decision: [] for decision in _DECISIONS
    }
    for symbol, candidate in candidates.items():
        decision, readiness = _decision(candidate)
        ranked[decision].append((str(symbol), readiness))

    for values in ranked.values():
        values.sort(key=lambda item: (-item[1], item[0]))

    counts = {decision: len(ranked[decision]) for decision in _DECISIONS}
    return {
        "contract_version": "decision_terminal_v1",
        "counts": counts,
        "entry_ready": [symbol for symbol, _ in ranked["ENTRY_READY"][:3]],
        "forming": [symbol for symbol, _ in ranked["FORMING"][:6]],
        "active": [symbol for symbol, _ in ranked["ACTIVE"][:6]],
        "late": [symbol for symbol, _ in ranked["LATE"][:6]],
        "zero_entry_ready_diagnostics": _zero_entry_ready_diagnostics(
            candidates,
            entry_ready_count=counts["ENTRY_READY"],
        ),
        "recent_changes": list(recent_changes[:10]),
    }
