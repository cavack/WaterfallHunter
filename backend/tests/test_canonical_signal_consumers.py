from __future__ import annotations

import json
import sqlite3

from schema_test_support import migrate_test_database
from waterfallhunter.core.lbank_execution_outcome_report import (
    LBankExecutionOutcomeReport,
    ReportCohort,
)
from waterfallhunter.core.lbank_signal_outcome import LBankSignalOutcomeStore


def _insert_ledger_row(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    symbol: str,
    triggered_at: int,
) -> None:
    conn.execute(
        """
        INSERT INTO lbank_signal_ledger (
            id,
            symbol,
            triggered_at,
            state_before,
            score,
            entry_price,
            stop_loss,
            take_profit_1,
            take_profit_2,
            position_setup_json,
            trigger_metrics_json,
            execution_status,
            execution_failed_checks_json,
            execution_suitability_json,
            observational_only,
            trade_eligible,
            created_at
        ) VALUES (?, ?, ?, 'ARMED', 90.0, 100.0, 102.0, 98.0, 96.0, ?, ?, 'SUITABLE', '[]', '{}', 1, NULL, ?)
        """,
        (
            signal_id,
            symbol,
            triggered_at,
            "{}",
            json.dumps(
                {"exchange": "binance", "mapped_symbol": symbol},
                sort_keys=True,
                separators=(",", ":"),
            ),
            triggered_at,
        ),
    )


def _insert_metadata(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    signal_class: str,
    strategy_profile: str,
    score_version: str,
    hash_char: str,
) -> None:
    conn.execute(
        """
        INSERT INTO signal_metadata (
            signal_id,
            signal_class,
            strategy_profile,
            score_version,
            model_generation,
            decision_contract_hash,
            analysis_observed_at,
            reference_observed_at,
            metadata_contract_version,
            classification_method,
            classification_evidence_hash,
            created_at
        ) VALUES (?, ?, ?, ?, 'waterfall_signal_model_v1', ?, 1000, 999, 'signal_metadata_v1', 'FUTURE_PIPELINE_EXPLICIT', NULL, 1000)
        """,
        (
            signal_id,
            signal_class,
            strategy_profile,
            score_version,
            hash_char * 64,
        ),
    )


def _insert_outcome(conn: sqlite3.Connection, signal_id: int, symbol: str) -> None:
    conn.execute(
        """
        INSERT INTO lbank_signal_outcomes (
            signal_id, symbol, outcome_status, signal_triggered_at,
            horizon_seconds, price_source, observed_candles, expected_candles,
            details_json, observational_only, trade_eligible, resolved_at
        ) VALUES (?, ?, 'TP1_ONLY_24H', 100, 86400, 'closed_1m_trade_ohlcv_proxy', 1440, 1440, '{}', 1, NULL, 200)
        """,
        (signal_id, symbol),
    )


def _seed_three_cohorts(db_path) -> None:
    with sqlite3.connect(db_path) as conn:
        _insert_ledger_row(conn, signal_id=1, symbol="STRICT/USDT:USDT", triggered_at=100)
        _insert_metadata(
            conn,
            signal_id=1,
            signal_class="STRICT",
            strategy_profile="strict_score_v2",
            score_version="score_v2",
            hash_char="a",
        )
        _insert_outcome(conn, 1, "STRICT/USDT:USDT")

        _insert_ledger_row(conn, signal_id=2, symbol="EXPERIMENTAL/USDT:USDT", triggered_at=101)
        _insert_metadata(
            conn,
            signal_id=2,
            signal_class="EXPERIMENTAL",
            strategy_profile="experimental_pretrigger_v1",
            score_version="score_v2_watch_v1",
            hash_char="b",
        )
        _insert_outcome(conn, 2, "EXPERIMENTAL/USDT:USDT")

        _insert_ledger_row(conn, signal_id=3, symbol="UNRESOLVED/USDT:USDT", triggered_at=102)
        _insert_outcome(conn, 3, "UNRESOLVED/USDT:USDT")


def test_pending_signals_use_canonical_view_and_exclude_unresolved(tmp_path) -> None:
    db_path = tmp_path / "canonical-consumers.db"
    migrate_test_database(db_path)

    with sqlite3.connect(db_path) as conn:
        _insert_ledger_row(conn, signal_id=1, symbol="STRICT/USDT:USDT", triggered_at=100)
        _insert_metadata(
            conn,
            signal_id=1,
            signal_class="STRICT",
            strategy_profile="strict_score_v2",
            score_version="score_v2",
            hash_char="a",
        )
        _insert_ledger_row(conn, signal_id=2, symbol="EXPERIMENTAL/USDT:USDT", triggered_at=101)
        _insert_metadata(
            conn,
            signal_id=2,
            signal_class="EXPERIMENTAL",
            strategy_profile="experimental_pretrigger_v1",
            score_version="score_v2_watch_v1",
            hash_char="b",
        )
        _insert_ledger_row(conn, signal_id=3, symbol="UNRESOLVED/USDT:USDT", triggered_at=102)

    rows = LBankSignalOutcomeStore(str(db_path)).pending_signals(
        mature_before=1_000,
        limit=10,
    )

    assert [row["id"] for row in rows] == [1, 2]
    assert [(row["signal_class"], row["strategy_profile"]) for row in rows] == [
        ("STRICT", "strict_score_v2"),
        ("EXPERIMENTAL", "experimental_pretrigger_v1"),
    ]
    assert all("trigger_metrics_json" in row for row in rows)


def test_reports_default_strict_and_require_explicit_research_cohorts(tmp_path) -> None:
    db_path = tmp_path / "canonical-report-cohorts.db"
    migrate_test_database(db_path)
    _seed_three_cohorts(db_path)

    strict = LBankExecutionOutcomeReport(str(db_path)).build_report(now=200_000)
    experimental = LBankExecutionOutcomeReport(
        str(db_path), cohort=ReportCohort.EXPERIMENTAL
    ).build_report(now=200_000)
    mixed = LBankExecutionOutcomeReport(
        str(db_path), cohort=ReportCohort.MIXED_RESEARCH
    ).build_report(now=200_000)

    assert strict["settlement"]["signal_count"] == 1
    assert strict["signal_class_scope"] == ["STRICT"]
    assert strict["research_only"] is False

    assert experimental["settlement"]["signal_count"] == 1
    assert experimental["signal_class_scope"] == ["EXPERIMENTAL"]
    assert experimental["research_only"] is True

    assert mixed["settlement"]["signal_count"] == 2
    assert mixed["signal_class_scope"] == ["STRICT", "EXPERIMENTAL"]
    assert mixed["research_only"] is True

    assert all(report["settlement"]["signal_count"] != 3 for report in (strict, experimental, mixed))
    assert "calibrated_probability" not in strict
    assert "calibrated_probability" not in experimental
    assert "calibrated_probability" not in mixed
