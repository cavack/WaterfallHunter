import pytest

from waterfallhunter.core.decision_terminal import build_decision_terminal


def candidate(decision: str, readiness: float) -> dict:
    return {
        "metrics": {
            "entry_decision": {
                "decision": decision,
                "entry_readiness": readiness,
                "evidence_coverage_pct": 90.0,
            }
        }
    }


def test_terminal_limits_actionable_and_forming_lists() -> None:
    candidates = {
        **{f"E{i}": candidate("ENTRY_READY", 90 - i) for i in range(5)},
        **{f"F{i}": candidate("FORMING", 80 - i) for i in range(8)},
        "L1": candidate("LATE", 88),
        "N1": candidate("NO_TRADE", 20),
    }
    packet = build_decision_terminal(candidates, recent_changes=[])
    assert packet["contract_version"] == "decision_terminal_v1"
    assert len(packet["entry_ready"]) == 3
    assert len(packet["forming"]) == 6
    assert packet["entry_ready"] == ["E0", "E1", "E2"]
    assert packet["forming"] == ["F0", "F1", "F2", "F3", "F4", "F5"]
    assert packet["counts"] == {
        "ENTRY_READY": 5,
        "FORMING": 8,
        "ACTIVE": 0,
        "LATE": 1,
        "INVALIDATED": 0,
        "EXPIRED": 0,
        "NO_TRADE": 1,
        "UNAVAILABLE": 0,
    }


def test_terminal_marks_missing_decision_unavailable_and_bounds_history() -> None:
    candidates = {"A": {}, "B": candidate("ACTIVE", 82)}
    changes = [{"event_id": index} for index in range(20)]
    packet = build_decision_terminal(candidates, recent_changes=changes)
    assert packet["counts"]["UNAVAILABLE"] == 1
    assert packet["counts"]["ACTIVE"] == 1
    assert len(packet["recent_changes"]) == 10
    assert packet["recent_changes"][0]["event_id"] == 0


def test_terminal_explains_systemic_zero_entry_ready() -> None:
    blocked = candidate("NO_TRADE", 62)
    blocked["metrics"]["entry_decision"].update({
        "block_reasons": ["STALE_REFERENCE"],
        "reason_codes": ["TIMING_INCOMPLETE", "BUYERS_ACTIVE"],
    })
    weak = candidate("FORMING", 70)
    weak["metrics"]["entry_decision"].update({
        "block_reasons": [],
        "reason_codes": ["TIMING_INCOMPLETE", "CROSS_EXCHANGE_DISAGREEMENT"],
    })
    packet = build_decision_terminal(
        {"A": blocked, "B": weak},
        recent_changes=[],
    )
    diagnostics = packet["zero_entry_ready_diagnostics"]
    assert diagnostics["entry_ready_zero"] is True
    assert diagnostics["evaluated_candidates"] == 2
    assert diagnostics["top_reasons"][0] == {
        "reason": "TIMING_INCOMPLETE",
        "count": 2,
        "share_pct": 100.0,
    }
    assert {row["reason"] for row in diagnostics["top_reasons"]} >= {
        "STALE_REFERENCE",
        "BUYERS_ACTIVE",
        "CROSS_EXCHANGE_DISAGREEMENT",
    }


def test_terminal_rejects_empty_candidate_symbol() -> None:
    with pytest.raises(ValueError, match="candidate symbol"):
        build_decision_terminal({"": candidate("NO_TRADE", 20)}, recent_changes=[])



def test_terminal_flags_systemic_evidence_unavailability_as_pipeline_degraded() -> None:
    first = candidate("NO_TRADE", 40)
    first["metrics"]["entry_decision"].update({
        "block_reasons": [],
        "reason_codes": ["STRUCTURE_UNAVAILABLE", "TIMING_UNAVAILABLE", "BUYERS_ACTIVE"],
    })
    second = candidate("LATE", 45)
    second["metrics"]["entry_decision"].update({
        "block_reasons": ["ANTI_CHASE_HARD_BLOCK"],
        "reason_codes": ["STRUCTURE_UNAVAILABLE", "TIMING_UNAVAILABLE", "CASCADE_PARTIAL"],
    })

    diagnostics = build_decision_terminal(
        {"A": first, "B": second},
        recent_changes=[],
    )["zero_entry_ready_diagnostics"]

    assert diagnostics["pipeline_degraded"] is True
    assert diagnostics["systemic_unavailable_reasons"] == [
        {"reason": "STRUCTURE_UNAVAILABLE", "count": 2, "share_pct": 100.0},
        {"reason": "TIMING_UNAVAILABLE", "count": 2, "share_pct": 100.0},
    ]


def test_terminal_does_not_call_partial_unavailability_a_systemic_pipeline_failure() -> None:
    first = candidate("NO_TRADE", 40)
    first["metrics"]["entry_decision"].update({
        "block_reasons": [],
        "reason_codes": ["DERIVATIVES_UNAVAILABLE"],
    })
    second = candidate("NO_TRADE", 35)
    second["metrics"]["entry_decision"].update({
        "block_reasons": [],
        "reason_codes": ["BUYERS_ACTIVE"],
    })

    diagnostics = build_decision_terminal(
        {"A": first, "B": second},
        recent_changes=[],
    )["zero_entry_ready_diagnostics"]

    assert diagnostics["pipeline_degraded"] is False
    assert diagnostics["systemic_unavailable_reasons"] == []
