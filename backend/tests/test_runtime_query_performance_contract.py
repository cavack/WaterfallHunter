from __future__ import annotations

import sqlite3
from pathlib import Path

from schema_test_support import migrate_test_database
from waterfallhunter.core.feature_replay import FeatureReplayEngine, FeatureReplayStore
from waterfallhunter.core.entry_decision_store import EntryDecisionStore
from waterfallhunter.core.schema_contract import CURRENT_RUNTIME_SCHEMA_VERSION


def _index_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA index_list({table})").fetchall()
        if str(row[1]).startswith("idx_")
    }


def test_runtime_schema_installs_query_performance_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "registry.db"
    migrate_test_database(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_RUNTIME_SCHEMA_VERSION
        assert "idx_production_evidence_replay_v9" in _index_names(
            conn, "production_evidence_snapshots"
        )
        assert "idx_production_evidence_replay_v8" in _index_names(
            conn, "production_evidence_snapshots"
        )
        assert "idx_entry_decision_symbol_id" in _index_names(
            conn, "entry_decision_events"
        )


def test_feature_replay_pending_query_is_index_driven(tmp_path: Path) -> None:
    db_path = tmp_path / "registry.db"
    migrate_test_database(db_path)
    store = FeatureReplayStore(str(db_path), verify_schema=False)

    with sqlite3.connect(db_path) as conn:
        plan = [
            str(row[3])
            for row in conn.execute(
                "EXPLAIN QUERY PLAN " + store._pending_select_sql(),
                (
                    FeatureReplayEngine.VERSION,
                    "0" * 64,
                    FeatureReplayEngine.VERSION,
                    3,
                ),
            ).fetchall()
        ]

    joined = "\n".join(plan)
    assert "SCAN production_evidence_snapshots" not in joined
    assert not any(detail.split()[:2] == ["SCAN", "s"] for detail in plan)
    assert "idx_production_evidence_replay_v9" in joined
    assert "idx_production_evidence_replay_v8" in joined


def test_feature_replay_pending_semantics_are_preserved(tmp_path: Path) -> None:
    db_path = tmp_path / "registry.db"
    migrate_test_database(db_path)
    store = FeatureReplayStore(str(db_path), verify_schema=False)
    code_hash = "1" * 64

    with sqlite3.connect(db_path) as conn:
        base = (
            100, "TEST/USDT:USDT", 1.0, "WATCH", "lbank", 1.0, 1,
            "WATCH", 1.0, "a" * 64, b"x", 1, 1, 1, 1, 1, 1, 1, 1,
        )
        columns = (
            "bucket_started_at,symbol,observed_at,candidate_state,reference_source,"
            "reference_price,result_valid,suggested_status,score,evidence_sha256,"
            "evidence_zlib,uncompressed_bytes,compressed_bytes,has_orderbook,"
            "orderbook_bid_levels,orderbook_ask_levels,has_candle_analysis,"
            "valid_candle_timeframes,has_derivatives"
        )
        # Use the recorder-era defaults for unrelated replay columns.
        conn.execute(
            f"INSERT INTO production_evidence_snapshots ({columns},has_confirmation_source,"
            "decision_packet_complete,schema_version,capture_mode,code_sha256_v5,"
            "production_evidence_complete_v5) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1,"
            "'production_decision_evidence_v9','test','',0)",
            base,
        )
        conn.execute(
            f"INSERT INTO production_evidence_snapshots ({columns},has_confirmation_source,"
            "decision_packet_complete,schema_version,capture_mode,code_sha256_v5,"
            "production_evidence_complete_v5) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1,"
            "'production_decision_evidence_v8','test',?,1)",
            (101, *base[1:-1], b"y", code_hash),
        )
        rows = conn.execute(
            "SELECT id,schema_version FROM production_evidence_snapshots ORDER BY id"
        ).fetchall()
        processed_id = int(rows[0][0])
        expected_pending_id = int(rows[1][0])
        conn.execute(
            "INSERT INTO production_feature_replay_results_v2 ("
            "snapshot_id,symbol,status,strategy_equivalent,differences_json,replay_version,"
            "replayed_at) VALUES (?,?,'EQUIVALENT',1,'{}',?,1.0)",
            (processed_id, "TEST/USDT:USDT", FeatureReplayEngine.VERSION),
        )
        conn.commit()

    # Avoid decompressing the deliberately tiny fixture payloads; inspect SQL result ids.
    with sqlite3.connect(db_path) as conn:
        ids = [
            int(row[0])
            for row in conn.execute(
                store._pending_select_sql(),
                (FeatureReplayEngine.VERSION, code_hash, FeatureReplayEngine.VERSION, 3),
            ).fetchall()
        ]
    assert ids == [expected_pending_id]


def test_latest_entry_decision_query_avoids_temp_order_sort(tmp_path: Path) -> None:
    db_path = tmp_path / "registry.db"
    migrate_test_database(db_path)
    sql = EntryDecisionStore._history_select(
        "WHERE e.symbol=? ORDER BY e.id DESC LIMIT 1"
    )

    with sqlite3.connect(db_path) as conn:
        plan = [
            str(row[3])
            for row in conn.execute(
                "EXPLAIN QUERY PLAN " + sql,
                ("TEST/USDT:USDT",),
            ).fetchall()
        ]

    joined = "\n".join(plan)
    assert "idx_entry_decision_symbol_id" in joined
    assert "USE TEMP B-TREE FOR ORDER BY" not in joined
