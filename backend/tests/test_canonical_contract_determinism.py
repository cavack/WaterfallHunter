from waterfallhunter.core.canonical_json import canonical_json_bytes
from waterfallhunter.core.contracts import DecisionStatus


def test_decision_status_semantic_order_has_identical_canonical_bytes():
    left = DecisionStatus(
        primary="CONFIRMED",
        qualifiers=["STALE_REFERENCE", "AI_CAUTION"],
    )
    right = DecisionStatus(
        primary="CONFIRMED",
        qualifiers=["AI_CAUTION", "STALE_REFERENCE", "AI_CAUTION"],
    )

    assert canonical_json_bytes(left.model_dump(mode="json")) == canonical_json_bytes(
        right.model_dump(mode="json")
    )


def test_decision_status_canonical_bytes_are_stable():
    status = DecisionStatus(
        primary="CONFIRMED",
        qualifiers=["AI_CAUTION", "STALE_REFERENCE"],
    )

    first = canonical_json_bytes(status.model_dump(mode="json"))
    second = canonical_json_bytes(status.model_dump(mode="json"))

    assert first == second
    assert first == (
        b'{"primary":"CONFIRMED","qualifiers":["AI_CAUTION","STALE_REFERENCE"]}'
    )
