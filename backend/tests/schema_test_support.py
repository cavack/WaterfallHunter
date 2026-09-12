from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path

from waterfallhunter.migrate_database import main as run_migration_command


_FIXTURE = Path(__file__).with_name("fixtures") / "legacy_runtime_schema_v0.fixture"

_OPTIONAL_SCHEMA = """
CREATE TABLE lbank_execution_observations (
    symbol TEXT PRIMARY KEY,
    observation_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    observed_at REAL,
    reason TEXT,
    spread_pct REAL,
    cost_25_pct REAL,
    cost_50_pct REAL,
    cost_100_pct REAL,
    depth_10bps_min_usdt REAL,
    depth_25bps_min_usdt REAL,
    depth_50bps_min_usdt REAL,
    depth_100bps_min_usdt REAL,
    failures INTEGER NOT NULL DEFAULT 0,
    next_check_at REAL NOT NULL DEFAULT 0,
    payload TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL
);
CREATE INDEX idx_lbank_execution_queue
ON lbank_execution_observations (next_check_at, observed_at);
CREATE INDEX idx_lbank_execution_status
ON lbank_execution_observations (observation_status);

CREATE TABLE lbank_execution_observation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    observation_status TEXT NOT NULL,
    observed_at REAL NOT NULL,
    reason TEXT,
    spread_pct REAL,
    cost_25_pct REAL,
    cost_50_pct REAL,
    cost_100_pct REAL,
    depth_10bps_min_usdt REAL,
    depth_25bps_min_usdt REAL,
    depth_50bps_min_usdt REAL,
    depth_100bps_min_usdt REAL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX idx_lbank_execution_history_symbol_time
ON lbank_execution_observation_history (symbol, observed_at);
CREATE INDEX idx_lbank_execution_history_status_time
ON lbank_execution_observation_history (observation_status, observed_at);
CREATE INDEX idx_lbank_execution_history_observed_at
ON lbank_execution_observation_history (observed_at);

CREATE TABLE provider_states (
    provider_id TEXT PRIMARY KEY,
    upstream_identity TEXT NOT NULL,
    status TEXT NOT NULL,
    failure_class TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL,
    circuit_open_until REAL NOT NULL,
    replacement_generation INTEGER NOT NULL,
    last_success_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


def migrate_test_database(path: Path) -> Path:
    """Create/upgrade one disposable DB through the guarded migration command."""
    canonical = path.resolve()
    canonical.parent.mkdir(parents=True, exist_ok=True)
    output = io.StringIO()
    with redirect_stdout(output):
        status = run_migration_command(
            (
                "--db-path",
                str(canonical),
                "--source-revision",
                "test",
                "--apply",
            )
        )
    if status != 0:
        raise RuntimeError(
            f"test database migration rejected with status {status}: {output.getvalue().strip()}"
        )
    return canonical


def _insert_representative_rows(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO lbank_catalog ("
        "symbol,last_price,quote_volume,is_meme,scan_eligible,status,"
        "first_seen_at,last_added_at,last_seen_at,consecutive_missing_snapshots,"
        "lifecycle_id,trigger_data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("TEST/USDT:USDT", 0.125, 5_000_000.0, 0, 1, "PRE-TRIGGER", 100, 100, 101, 0, 1, "{}"),
    )
    conn.execute(
        "INSERT INTO catalog_events (symbol,event_type,timestamp) VALUES (?,?,?)",
        ("TEST/USDT:USDT", "ADDED", 100),
    )
    conn.execute(
        "INSERT INTO lbank_signal_ledger ("
        "symbol,triggered_at,state_before,score,position_setup_json,trigger_metrics_json,"
        "execution_status,execution_failed_checks_json,execution_suitability_json,"
        "quote_volume_at_trigger,volume_gate_passed,proxy_execution_disagreement,created_at"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "TEST/USDT:USDT", 200, "PRE-TRIGGER", 55.0, "{}", "{}", "SUITABLE",
            "[]", "{}", 5_000_000.0, 1, "AGREE_ACCEPT", 200,
        ),
    )
    conn.execute(
        "INSERT INTO lbank_stage_lifecycle ("
        "symbol,lifecycle_id,hype_seen_at,damage_seen_at,setup_seen_at,setup_type,updated_at"
        ") VALUES (?,?,?,?,?,?,?)",
        ("TEST/USDT:USDT", 1, 100, 120, 150, "breakdown", 150),
    )
    conn.execute(
        "INSERT INTO production_evidence_snapshots ("
        "bucket_started_at,symbol,observed_at,result_valid,evidence_sha256,evidence_zlib,"
        "uncompressed_bytes,compressed_bytes,has_orderbook,orderbook_bid_levels,"
        "orderbook_ask_levels,has_candle_analysis,valid_candle_timeframes,has_derivatives,"
        "has_confirmation_source,decision_packet_complete,schema_version,capture_mode"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (300, "TEST/USDT:USDT", 300.0, 1, "a" * 64, b"fixture", 7, 7, 1, 10, 10, 1, 4, 1, 1, 1, "fixture-v1", "test"),
    )
    conn.execute(
        "INSERT INTO lbank_execution_decision_log ("
        "bucket_started_at,source,symbol,first_observed_at,last_observed_at,evaluation_count,"
        "volume_gate_passed,suitability_status,disagreement_kind,scan_eligible,"
        "observational_only) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (400, "HUNTER_EVALUATION", "TEST/USDT:USDT", 400.0, 400.0, 1, 1, "SUITABLE", "AGREE_ACCEPT", 1, 1),
    )
    dataset_id = conn.execute(
        "INSERT INTO operational_historical_outcome_datasets ("
        "report_sha256,source,window_start_ms,window_end_ms,days,strategy,cost_basis,"
        "strategy_equivalent,source_provenance_json,imported_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("b" * 64, "fixture", 0, 86_400_000, 1, "fixture", "modeled", 1, "{}", 500),
    ).lastrowid
    conn.execute(
        "INSERT INTO operational_historical_signal_outcomes ("
        "dataset_id,event_key,symbol,signal_timestamp_ms,outcome,net_realized_r,cost_basis,"
        "details_json) VALUES (?,?,?,?,?,?,?,?)",
        (dataset_id, "c" * 64, "TEST/USDT:USDT", 500, "STOP_FIRST", -1.0, "modeled", "{}"),
    )


def build_legacy_runtime_database(
    path: Path,
    *,
    include_optional: bool = False,
    representative_rows: bool = True,
) -> Path:
    """Build the frozen current pre-migration runtime fixture for tests only."""
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_FIXTURE.read_text(encoding="utf-8"))
        if include_optional:
            conn.executescript(_OPTIONAL_SCHEMA)
        if representative_rows:
            _insert_representative_rows(conn)
    return path


def _json_safe(value: object) -> object:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    return value


def business_row_hashes(path: Path, tables: tuple[str, ...]) -> dict[str, str]:
    """Hash deterministic row projections without changing the target database."""
    result: dict[str, str] = {}
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        for table in tables:
            columns = [
                str(row[1])
                for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            ]
            rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            payload = [
                {column: _json_safe(value) for column, value in zip(columns, row)}
                for row in rows
            ]
            encoded = json.dumps(
                payload,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            result[table] = hashlib.sha256(encoded).hexdigest()
    return result
