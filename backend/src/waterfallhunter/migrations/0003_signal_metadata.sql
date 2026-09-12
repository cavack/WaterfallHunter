CREATE TABLE signal_metadata (
    signal_id INTEGER PRIMARY KEY,
    signal_class TEXT NOT NULL,
    strategy_profile TEXT NOT NULL,
    score_version TEXT NOT NULL,
    model_generation TEXT NOT NULL,
    decision_contract_hash TEXT NOT NULL,
    analysis_observed_at INTEGER NOT NULL,
    reference_observed_at INTEGER,
    metadata_contract_version TEXT NOT NULL,
    classification_method TEXT NOT NULL,
    classification_evidence_hash TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(signal_id) REFERENCES lbank_signal_ledger(id),
    CHECK(signal_class IN ('STRICT', 'EXPERIMENTAL')),
    CHECK(length(strategy_profile) > 0),
    CHECK(length(score_version) > 0),
    CHECK(typeof(model_generation) = 'text' AND length(model_generation) > 0),
    CHECK(
        typeof(decision_contract_hash) = 'text'
        AND length(decision_contract_hash) = 64
    ),
    CHECK(decision_contract_hash NOT GLOB '*[^0-9a-f]*'),
    CHECK(
        typeof(analysis_observed_at) = 'integer' -- NOSONAR: SQLite type checks are not PL/SQL literals.
        AND analysis_observed_at >= 0
    ),
    CHECK(
        reference_observed_at IS NULL
        OR (
            typeof(reference_observed_at) = 'integer'
            AND reference_observed_at >= 0
        )
    ),
    CHECK(metadata_contract_version = 'signal_metadata_v1'),
    CHECK(classification_method IN (
        'FUTURE_PIPELINE_EXPLICIT',
        'LEGACY_PROFILE_EXACT_MATCH'
    )),
    CHECK(
        (
            classification_method = 'FUTURE_PIPELINE_EXPLICIT'
            AND classification_evidence_hash IS NULL
        )
        OR
        (
            classification_method = 'LEGACY_PROFILE_EXACT_MATCH'
            AND typeof(classification_evidence_hash) = 'text'
            AND length(classification_evidence_hash) = 64
            AND classification_evidence_hash NOT GLOB '*[^0-9a-f]*'
        )
    ),
    CHECK(
        (
            signal_class = 'STRICT'
            AND strategy_profile = 'strict_score_v2'
            AND score_version = 'score_v2'
        )
        OR
        (
            signal_class = 'EXPERIMENTAL'
            AND strategy_profile = 'experimental_pretrigger_v1'
            AND score_version = 'score_v2_watch_v1'
        )
    ),
    CHECK(typeof(created_at) = 'integer' AND created_at >= 0)
);

CREATE TRIGGER signal_metadata_no_update -- NOSONAR: SQLite has no CREATE OR REPLACE TRIGGER.
BEFORE UPDATE ON signal_metadata
BEGIN
    SELECT RAISE(ABORT, 'signal_metadata is immutable');
END;

CREATE TRIGGER signal_metadata_no_delete -- NOSONAR: SQLite has no CREATE OR REPLACE TRIGGER.
BEFORE DELETE ON signal_metadata
BEGIN
    SELECT RAISE(ABORT, 'signal_metadata is immutable');
END;

CREATE VIEW canonical_signal_view AS
SELECT
    s.id AS signal_id,
    s.symbol,
    s.triggered_at,
    s.state_before,
    s.score,
    s.entry_price,
    s.stop_loss,
    s.take_profit_1,
    s.take_profit_2,
    s.position_setup_json,
    s.trigger_metrics_json,
    s.execution_status,
    s.execution_evidence_status,
    s.execution_observed_samples,
    s.execution_observation_span_hours,
    s.execution_availability_rate,
    s.execution_cost_100_p90_pct,
    s.execution_spread_p90_pct,
    s.execution_depth_25bps_p50_usdt,
    s.execution_failed_checks_json,
    s.execution_suitability_json,
    s.quote_volume_at_trigger,
    s.volume_gate_passed,
    s.proxy_execution_disagreement,
    s.observational_only,
    s.trade_eligible,
    s.created_at,
    m.signal_class,
    m.strategy_profile,
    m.score_version,
    m.model_generation,
    m.decision_contract_hash,
    m.analysis_observed_at,
    m.reference_observed_at,
    m.metadata_contract_version,
    m.classification_method,
    m.classification_evidence_hash
FROM lbank_signal_ledger AS s
INNER JOIN signal_metadata AS m
    ON m.signal_id = s.id;

PRAGMA user_version=3;
