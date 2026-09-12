from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from schema_test_support import migrate_test_database
from waterfallhunter.core import legacy_signal_classifier
from waterfallhunter.core.legacy_signal_classifier import (
    LegacyClassificationError,
    LegacyClassificationStatus,
    apply_legacy_classification,
    classify_legacy_evidence,
    preview_legacy_classification,
)
from waterfallhunter.core.managed_sqlite import ManagedSQLiteError
from waterfallhunter.core.schema_contract import CURRENT_RUNTIME_SCHEMA_VERSION
from waterfallhunter.core.signal_metadata_store import (
    verify_signal_metadata_completeness,
)


_FIXTURE = Path(__file__).with_name("fixtures") / "legacy_signal_metadata_cases.json"


def _cases() -> list[dict]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _case(name: str) -> dict:
    return next(item for item in _cases() if item["name"] == name)


def _insert_legacy_signal(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    metrics: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO lbank_signal_ledger (
            id,
            symbol,
            triggered_at,
            state_before,
            score,
            position_setup_json,
            trigger_metrics_json,
            execution_status,
            execution_failed_checks_json,
            execution_suitability_json,
            observational_only,
            trade_eligible,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?)
        """,
        (
            signal_id,
            f"LEGACY{signal_id}/USDT:USDT",
            1_700_000_010 + signal_id,
            "PRE-TRIGGER",
            55.0,
            "{}",
            json.dumps(metrics, allow_nan=False, sort_keys=True, separators=(",", ":")),
            "SUITABLE",
            "[]",
            "{}",
            1_700_000_020 + signal_id,
        ),
    )


def _seed_cases(db_path: Path, names: tuple[str, ...]) -> None:
    with sqlite3.connect(db_path) as conn:
        for name in names:
            row = _case(name)["signal_row"]
            _insert_legacy_signal(
                conn,
                signal_id=int(row["id"]),
                metrics=json.loads(row["trigger_metrics_json"]),
            )


def test_fixture_cases_classify_fail_closed_and_without_default_to_strict() -> None:
    for item in _cases():
        decision = classify_legacy_evidence(item["signal_row"])
        assert decision.status.value == item["expected_status"], item["name"]
        if decision.status is LegacyClassificationStatus.RESOLVED:
            assert decision.metadata is not None
            assert decision.metadata.signal_class.value == item["expected_signal_class"]
            assert decision.metadata.classification_method.value == "LEGACY_PROFILE_EXACT_MATCH"
            assert decision.metadata.classification_evidence_hash is not None
        else:
            assert decision.metadata is None


def test_experimental_profile_alone_is_not_enough() -> None:
    decision = classify_legacy_evidence(
        _case("profile_only_experimental")["signal_row"]
    )
    assert decision.status is LegacyClassificationStatus.UNRESOLVED
    assert "MISSING_MANDATORY_EVIDENCE" in decision.reason_codes


def test_evidence_hash_is_deterministic_for_equivalent_json_key_order() -> None:
    row = _case("complete_experimental")["signal_row"]
    metrics = json.loads(row["trigger_metrics_json"])
    reordered = {
        key: metrics[key]
        for key in reversed(tuple(metrics.keys()))
    }
    first = classify_legacy_evidence(row)
    second = classify_legacy_evidence(
        {
            **row,
            "trigger_metrics_json": json.dumps(reordered),
        }
    )
    assert first.status is LegacyClassificationStatus.RESOLVED
    assert second.status is LegacyClassificationStatus.RESOLVED
    assert first.evidence_hash == second.evidence_hash
    assert first.metadata is not None
    assert second.metadata is not None
    assert (
        first.metadata.classification_evidence_hash
        == second.metadata.classification_evidence_hash
    )


def test_preview_is_read_only_and_report_hash_is_deterministic(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "preview.db")
    _seed_cases(db_path, ("complete_experimental", "missing_decision_hash"))

    before = db_path.read_bytes()
    first = preview_legacy_classification(db_path)
    second = preview_legacy_classification(db_path)
    after = db_path.read_bytes()

    assert before == after
    assert first.report_hash == second.report_hash
    assert first.total_count == 2
    assert first.resolved_ids == (11,)
    assert first.unresolved_ids == (13,)
    assert first.conflict_ids == ()


def test_preview_closes_connection_when_read_only_setup_fails(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "setup-failure.db"
    db_path.touch()

    class FakeConnection:
        closed = False

        def execute(self, sql: str):
            if sql == "PRAGMA query_only=ON":
                return self
            raise sqlite3.OperationalError("injected pragma failure")

        def close(self) -> None:
            self.closed = True

    fake = FakeConnection()
    monkeypatch.setattr(
        legacy_signal_classifier.sqlite3,
        "connect",
        lambda *args, **kwargs: fake,
    )

    with pytest.raises(
        LegacyClassificationError,
        match="LEGACY_CLASSIFICATION_DATABASE_UNREADABLE",
    ):
        preview_legacy_classification(db_path)

    assert fake.closed is True


def test_apply_rejects_mismatched_report_hash_before_write(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "hash-mismatch.db")
    _seed_cases(db_path, ("complete_experimental",))

    with pytest.raises(LegacyClassificationError, match="REPORT_HASH_MISMATCH"):
        apply_legacy_classification(
            db_path,
            expected_report_hash="0" * 64,
            created_at=1_700_000_100,
        )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM signal_metadata").fetchone() == (0,)


def test_apply_wraps_managed_connection_failure_with_stable_error(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = migrate_test_database(tmp_path / "unsafe-connection.db")

    def reject_connection(*args, **kwargs):
        raise ManagedSQLiteError("SQLITE_FOREIGN_KEYS_UNAVAILABLE")

    monkeypatch.setattr(
        legacy_signal_classifier,
        "connect_managed_sqlite",
        reject_connection,
    )

    with pytest.raises(
        LegacyClassificationError,
        match="LEGACY_CLASSIFICATION_DATABASE_UNSAFE",
    ):
        apply_legacy_classification(
            db_path,
            expected_report_hash="0" * 64,
            created_at=1_700_000_100,
        )


def test_apply_inserts_only_resolved_and_leaves_unresolved_noncanonical(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "apply.db")
    _seed_cases(db_path, ("complete_experimental", "missing_decision_hash"))
    preview = preview_legacy_classification(db_path)

    applied = apply_legacy_classification(
        db_path,
        expected_report_hash=preview.report_hash,
        created_at=1_700_000_100,
    )

    assert applied.report_hash == preview.report_hash
    with sqlite3.connect(db_path) as conn:
        ledger_count = conn.execute("SELECT COUNT(*) FROM lbank_signal_ledger").fetchone()
        metadata_rows = conn.execute(
            "SELECT signal_id, signal_class, strategy_profile, classification_method "
            "FROM signal_metadata ORDER BY signal_id"
        ).fetchall()
        canonical_ids = conn.execute(
            "SELECT signal_id FROM canonical_signal_view ORDER BY signal_id"
        ).fetchall()
    assert ledger_count == (2,)
    assert metadata_rows == [
        (11, "EXPERIMENTAL", "experimental_pretrigger_v1", "LEGACY_PROFILE_EXACT_MATCH")
    ]
    assert canonical_ids == [(11,)]

    completeness = verify_signal_metadata_completeness(db_path)
    assert completeness.complete is False
    assert completeness.ledger_count == 2
    assert completeness.metadata_count == 1
    assert completeness.canonical_count == 1
    assert completeness.missing_metadata_count == 1


def test_apply_is_append_only_and_accepts_only_equivalent_existing_metadata(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "idempotent.db")
    _seed_cases(db_path, ("complete_strict",))
    preview = preview_legacy_classification(db_path)

    apply_legacy_classification(
        db_path,
        expected_report_hash=preview.report_hash,
        created_at=1_700_000_100,
    )
    apply_legacy_classification(
        db_path,
        expected_report_hash=preview.report_hash,
        created_at=1_700_000_100,
    )
    apply_legacy_classification(
        db_path,
        expected_report_hash=preview.report_hash,
        created_at=1_700_000_999,
    )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM signal_metadata").fetchone() == (1,)
        assert conn.execute("SELECT created_at FROM signal_metadata").fetchone() == (
            1_700_000_100,
        )


def test_apply_rolls_back_all_new_rows_when_existing_metadata_conflicts(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "conflict.db")
    _seed_cases(db_path, ("complete_experimental", "complete_strict"))
    preview = preview_legacy_classification(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO signal_metadata (
                signal_id, signal_class, strategy_profile, score_version,
                model_generation, decision_contract_hash, analysis_observed_at,
                reference_observed_at, metadata_contract_version,
                classification_method, classification_evidence_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                17,
                "STRICT",
                "strict_score_v2",
                "score_v2",
                "waterfall_signal_model_v1",
                "f" * 64,
                1_700_000_000,
                1_699_999_990,
                "signal_metadata_v1",
                "LEGACY_PROFILE_EXACT_MATCH",
                "f" * 64,
                1_700_000_100,
            ),
        )

    with pytest.raises(LegacyClassificationError, match="EXISTING_METADATA_CONFLICT"):
        apply_legacy_classification(
            db_path,
            expected_report_hash=preview.report_hash,
            created_at=1_700_000_100,
        )

    with sqlite3.connect(db_path) as conn:
        ids = conn.execute("SELECT signal_id FROM signal_metadata ORDER BY signal_id").fetchall()
    assert ids == [(17,)]


def test_classifier_rejects_an_unsupported_future_schema_version(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "future.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version={CURRENT_RUNTIME_SCHEMA_VERSION + 1}")

    with pytest.raises(LegacyClassificationError, match="FUTURE_SCHEMA_UNSUPPORTED"):
        preview_legacy_classification(db_path)
