CREATE TABLE IF NOT EXISTS lbank_catalog (
    symbol TEXT PRIMARY KEY,
    last_price REAL,
    quote_volume REAL,
    is_meme BOOLEAN,
    scan_eligible BOOLEAN DEFAULT 0,
    status TEXT DEFAULT 'WATCH',
    first_seen_at INTEGER,
    last_added_at INTEGER,
    last_seen_at INTEGER,
    removed_at INTEGER,
    consecutive_missing_snapshots INTEGER DEFAULT 0,
    lifecycle_id INTEGER NOT NULL DEFAULT 1,
    trigger_data TEXT
);

CREATE TABLE IF NOT EXISTS catalog_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    event_type TEXT,
    timestamp INTEGER
);

CREATE TABLE IF NOT EXISTS lbank_signal_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    triggered_at INTEGER NOT NULL,
    state_before TEXT NOT NULL,
    score REAL NOT NULL,
    entry_price REAL,
    stop_loss REAL,
    take_profit_1 REAL,
    take_profit_2 REAL,
    position_setup_json TEXT NOT NULL,
    trigger_metrics_json TEXT NOT NULL,
    execution_status TEXT NOT NULL,
    execution_evidence_status TEXT,
    execution_observed_samples INTEGER,
    execution_observation_span_hours REAL,
    execution_availability_rate REAL,
    execution_cost_100_p90_pct REAL,
    execution_spread_p90_pct REAL,
    execution_depth_25bps_p50_usdt REAL,
    execution_failed_checks_json TEXT NOT NULL,
    execution_suitability_json TEXT NOT NULL,
    quote_volume_at_trigger REAL,
    volume_gate_passed INTEGER CHECK (volume_gate_passed IN (0, 1)),
    proxy_execution_disagreement TEXT,
    observational_only INTEGER NOT NULL DEFAULT 1 CHECK (observational_only = 1),
    trade_eligible INTEGER CHECK (trade_eligible IS NULL),
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lbank_signal_ledger_symbol_triggered
ON lbank_signal_ledger (symbol, triggered_at);

CREATE TRIGGER IF NOT EXISTS lbank_signal_ledger_no_update
BEFORE UPDATE ON lbank_signal_ledger
BEGIN
    SELECT RAISE(ABORT, 'lbank_signal_ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS lbank_signal_ledger_no_delete
BEFORE DELETE ON lbank_signal_ledger
BEGIN
    SELECT RAISE(ABORT, 'lbank_signal_ledger is immutable');
END;

CREATE TABLE IF NOT EXISTS lbank_signal_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    outcome_status TEXT NOT NULL,
    signal_triggered_at INTEGER NOT NULL,
    observation_started_at INTEGER,
    observation_ended_at INTEGER,
    horizon_seconds INTEGER NOT NULL,
    price_source TEXT NOT NULL,
    source_exchange TEXT,
    source_mapped_symbol TEXT,
    first_tp1_at INTEGER,
    first_tp2_at INTEGER,
    first_stop_at INTEGER,
    min_price REAL,
    max_price REAL,
    mfe_pct REAL,
    mae_pct REAL,
    observed_candles INTEGER NOT NULL,
    expected_candles INTEGER NOT NULL,
    details_json TEXT NOT NULL,
    observational_only INTEGER NOT NULL DEFAULT 1 CHECK (observational_only = 1),
    trade_eligible INTEGER CHECK (trade_eligible IS NULL),
    resolved_at INTEGER NOT NULL,
    FOREIGN KEY(signal_id) REFERENCES lbank_signal_ledger(id)
);

CREATE INDEX IF NOT EXISTS idx_lbank_signal_outcomes_status
ON lbank_signal_outcomes (outcome_status, resolved_at);

CREATE TRIGGER IF NOT EXISTS lbank_signal_outcomes_no_update
BEFORE UPDATE ON lbank_signal_outcomes
BEGIN
    SELECT RAISE(ABORT, 'lbank_signal_outcomes is immutable');
END;

CREATE TRIGGER IF NOT EXISTS lbank_signal_outcomes_no_delete
BEFORE DELETE ON lbank_signal_outcomes
BEGIN
    SELECT RAISE(ABORT, 'lbank_signal_outcomes is immutable');
END;

CREATE TABLE IF NOT EXISTS lbank_stage_lifecycle (
    symbol TEXT NOT NULL,
    lifecycle_id INTEGER NOT NULL,
    hype_seen_at INTEGER,
    damage_seen_at INTEGER,
    setup_seen_at INTEGER,
    setup_type TEXT,
    trigger_seen_at INTEGER,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (symbol, lifecycle_id)
);

CREATE INDEX IF NOT EXISTS idx_lbank_stage_lifecycle_updated
ON lbank_stage_lifecycle (updated_at);

CREATE TABLE IF NOT EXISTS production_evidence_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_started_at INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    observed_at REAL NOT NULL,
    candidate_state TEXT,
    reference_source TEXT,
    reference_price REAL,
    result_valid INTEGER NOT NULL,
    suggested_status TEXT,
    score REAL,
    evidence_sha256 TEXT NOT NULL,
    evidence_zlib BLOB NOT NULL,
    uncompressed_bytes INTEGER NOT NULL,
    compressed_bytes INTEGER NOT NULL,
    has_orderbook INTEGER NOT NULL,
    orderbook_bid_levels INTEGER NOT NULL,
    orderbook_ask_levels INTEGER NOT NULL,
    has_candle_analysis INTEGER NOT NULL,
    valid_candle_timeframes INTEGER NOT NULL,
    has_derivatives INTEGER NOT NULL,
    has_confirmation_source INTEGER NOT NULL,
    raw_ohlcv_captured INTEGER NOT NULL DEFAULT 0 CHECK(raw_ohlcv_captured = 0),
    raw_trades_captured INTEGER NOT NULL DEFAULT 0 CHECK(raw_trades_captured = 0),
    source_replay_ready INTEGER NOT NULL DEFAULT 0 CHECK(source_replay_ready = 0),
    decision_packet_complete INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    capture_mode TEXT NOT NULL,
    observational_only INTEGER NOT NULL DEFAULT 1 CHECK(observational_only = 1),
    hard_gating_allowed INTEGER NOT NULL DEFAULT 0 CHECK(hard_gating_allowed = 0),
    trade_eligible INTEGER CHECK(trade_eligible IS NULL),
    source_ohlcv_captured INTEGER NOT NULL DEFAULT 0 CHECK(source_ohlcv_captured IN (0, 1)),
    source_trades_captured INTEGER NOT NULL DEFAULT 0 CHECK(source_trades_captured IN (0, 1)),
    source_replay_ready_v2 INTEGER NOT NULL DEFAULT 0 CHECK(source_replay_ready_v2 IN (0, 1)),
    feature_replay_ready_v3 INTEGER NOT NULL DEFAULT 0 CHECK(feature_replay_ready_v3 IN (0, 1)),
    triggered_path_replay_ready_v4 INTEGER NOT NULL DEFAULT 0 CHECK(triggered_path_replay_ready_v4 IN (0, 1)),
    decision_provenance_ready_v5 INTEGER NOT NULL DEFAULT 0 CHECK(decision_provenance_ready_v5 IN (0, 1)),
    raw_derivatives_captured_v5 INTEGER NOT NULL DEFAULT 0 CHECK(raw_derivatives_captured_v5 IN (0, 1)),
    production_evidence_complete_v5 INTEGER NOT NULL DEFAULT 0 CHECK(production_evidence_complete_v5 IN (0, 1)),
    confirmation_ohlcv_captured_v5 INTEGER NOT NULL DEFAULT 0 CHECK(confirmation_ohlcv_captured_v5 IN (0, 1)),
    code_sha256_v5 TEXT NOT NULL DEFAULT '',
    UNIQUE(bucket_started_at, symbol)
);

CREATE INDEX IF NOT EXISTS idx_production_evidence_time
ON production_evidence_snapshots(observed_at);

CREATE INDEX IF NOT EXISTS idx_production_evidence_symbol
ON production_evidence_snapshots(symbol, observed_at);

CREATE TRIGGER IF NOT EXISTS production_evidence_no_update
BEFORE UPDATE ON production_evidence_snapshots
BEGIN SELECT RAISE(ABORT, 'production evidence is immutable'); END;

CREATE TRIGGER IF NOT EXISTS production_evidence_no_delete
BEFORE DELETE ON production_evidence_snapshots
BEGIN SELECT RAISE(ABORT, 'production evidence is immutable'); END;

CREATE TABLE IF NOT EXISTS production_feature_replay_results_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    decision_path TEXT NOT NULL DEFAULT 'UNKNOWN',
    status TEXT NOT NULL,
    strategy_equivalent INTEGER NOT NULL CHECK(strategy_equivalent IN (0, 1)),
    differences_json TEXT NOT NULL,
    replay_version TEXT NOT NULL,
    replayed_at REAL NOT NULL,
    observational_only INTEGER NOT NULL DEFAULT 1 CHECK(observational_only = 1),
    hard_gating_allowed INTEGER NOT NULL DEFAULT 0 CHECK(hard_gating_allowed = 0),
    trade_eligible INTEGER CHECK(trade_eligible IS NULL),
    FOREIGN KEY(snapshot_id) REFERENCES production_evidence_snapshots(id),
    UNIQUE(snapshot_id, replay_version)
);

CREATE INDEX IF NOT EXISTS idx_feature_replay_v2_status
ON production_feature_replay_results_v2(status, replayed_at);

CREATE TRIGGER IF NOT EXISTS production_feature_replay_v2_no_update
BEFORE UPDATE ON production_feature_replay_results_v2
BEGIN SELECT RAISE(ABORT, 'feature replay results are immutable'); END;

CREATE TRIGGER IF NOT EXISTS production_feature_replay_v2_no_delete
BEFORE DELETE ON production_feature_replay_results_v2
BEGIN SELECT RAISE(ABORT, 'feature replay results are immutable'); END;

CREATE TABLE IF NOT EXISTS lbank_execution_observations (
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

CREATE INDEX IF NOT EXISTS idx_lbank_execution_queue
ON lbank_execution_observations (next_check_at, observed_at);

CREATE INDEX IF NOT EXISTS idx_lbank_execution_status
ON lbank_execution_observations (observation_status);

CREATE TABLE IF NOT EXISTS lbank_execution_observation_history (
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

CREATE INDEX IF NOT EXISTS idx_lbank_execution_history_symbol_time
ON lbank_execution_observation_history (symbol, observed_at);

CREATE INDEX IF NOT EXISTS idx_lbank_execution_history_status_time
ON lbank_execution_observation_history (observation_status, observed_at);

CREATE INDEX IF NOT EXISTS idx_lbank_execution_history_observed_at
ON lbank_execution_observation_history (observed_at);

CREATE TABLE IF NOT EXISTS lbank_execution_decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_started_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    first_observed_at REAL NOT NULL,
    last_observed_at REAL NOT NULL,
    evaluation_count INTEGER NOT NULL,
    volume_gate_passed INTEGER NOT NULL,
    suitability_status TEXT NOT NULL,
    suitability_would_admit INTEGER,
    disagreement_kind TEXT NOT NULL,
    evidence_status TEXT,
    candidate_state TEXT,
    score REAL,
    scan_eligible INTEGER NOT NULL,
    quote_volume REAL,
    last_price REAL,
    observational_only INTEGER NOT NULL DEFAULT 1,
    trade_eligible INTEGER,
    UNIQUE (bucket_started_at, source, symbol)
);

CREATE INDEX IF NOT EXISTS idx_lbank_execution_decision_time
ON lbank_execution_decision_log (last_observed_at);

CREATE INDEX IF NOT EXISTS idx_lbank_execution_decision_comparison
ON lbank_execution_decision_log (source, disagreement_kind, bucket_started_at);

CREATE TABLE IF NOT EXISTS operational_historical_outcome_datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_sha256 TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    generated_at TEXT,
    window_start_ms INTEGER NOT NULL,
    window_end_ms INTEGER NOT NULL,
    days INTEGER NOT NULL,
    strategy TEXT NOT NULL,
    cost_basis TEXT NOT NULL,
    strategy_equivalent INTEGER NOT NULL CHECK(strategy_equivalent IN (0, 1)),
    source_provenance_json TEXT NOT NULL,
    imported_at INTEGER NOT NULL,
    observational_only INTEGER NOT NULL DEFAULT 1 CHECK(observational_only = 1),
    hard_gating_allowed INTEGER NOT NULL DEFAULT 0 CHECK(hard_gating_allowed = 0)
);

CREATE TABLE IF NOT EXISTS operational_historical_signal_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL,
    event_key TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    signal_timestamp_ms INTEGER NOT NULL,
    exit_timestamp_ms INTEGER,
    outcome TEXT NOT NULL,
    gross_realized_r REAL,
    net_realized_r REAL NOT NULL,
    exit_reason TEXT,
    cost_basis TEXT NOT NULL,
    details_json TEXT NOT NULL,
    observational_only INTEGER NOT NULL DEFAULT 1 CHECK(observational_only = 1),
    trade_eligible INTEGER CHECK(trade_eligible IS NULL),
    FOREIGN KEY(dataset_id) REFERENCES operational_historical_outcome_datasets(id)
);

CREATE INDEX IF NOT EXISTS idx_operational_historical_symbol
ON operational_historical_signal_outcomes(dataset_id, symbol, signal_timestamp_ms);

CREATE TRIGGER IF NOT EXISTS operational_historical_datasets_no_update
BEFORE UPDATE ON operational_historical_outcome_datasets
BEGIN SELECT RAISE(ABORT, 'operational historical datasets are immutable'); END;

CREATE TRIGGER IF NOT EXISTS operational_historical_datasets_no_delete
BEFORE DELETE ON operational_historical_outcome_datasets
BEGIN SELECT RAISE(ABORT, 'operational historical datasets are immutable'); END;

CREATE TRIGGER IF NOT EXISTS operational_historical_outcomes_no_update
BEFORE UPDATE ON operational_historical_signal_outcomes
BEGIN SELECT RAISE(ABORT, 'operational historical outcomes are immutable'); END;

CREATE TRIGGER IF NOT EXISTS operational_historical_outcomes_no_delete
BEFORE DELETE ON operational_historical_signal_outcomes
BEGIN SELECT RAISE(ABORT, 'operational historical outcomes are immutable'); END;

CREATE TABLE IF NOT EXISTS provider_states (
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
