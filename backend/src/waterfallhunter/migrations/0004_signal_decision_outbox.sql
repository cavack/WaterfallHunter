CREATE TABLE signal_decisions (
    signal_id INTEGER PRIMARY KEY,
    decision_id TEXT NOT NULL,
    decision_version INTEGER NOT NULL,
    decision_status TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    predictive_evidence_score REAL NOT NULL,
    calibrated_probability REAL,
    analysis_observed_at INTEGER NOT NULL,
    reference_observed_at INTEGER,
    decision_contract_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(signal_id) REFERENCES lbank_signal_ledger(id),
    UNIQUE(decision_id),
    CHECK(typeof(decision_id) = 'text' AND length(decision_id) > 0),
    CHECK(typeof(decision_version) = 'integer' AND decision_version = 1), -- NOSONAR: SQLite type checks are not PL/SQL literals.
    CHECK(decision_status = 'CONFIRMED'),
    CHECK(lifecycle_state = 'TRIGGERED'),
    CHECK(
        typeof(predictive_evidence_score) IN ('integer', 'real')
        AND predictive_evidence_score >= 0
        AND predictive_evidence_score <= 100
    ),
    CHECK(calibrated_probability IS NULL),
    CHECK(typeof(analysis_observed_at) = 'integer' AND analysis_observed_at >= 0),
    CHECK(
        reference_observed_at IS NULL
        OR (
            typeof(reference_observed_at) = 'integer'
            AND reference_observed_at >= 0
        )
    ),
    CHECK(
        typeof(decision_contract_hash) = 'text'
        AND length(decision_contract_hash) = 64
        AND decision_contract_hash NOT GLOB '*[^0-9a-f]*' -- NOSONAR: SQLite hash checks are not PL/SQL literals.
    ),
    CHECK(typeof(payload_json) = 'text' AND json_valid(payload_json)),
    CHECK(
        typeof(payload_hash) = 'text'
        AND length(payload_hash) = 64
        AND payload_hash NOT GLOB '*[^0-9a-f]*'
    ),
    CHECK(typeof(created_at) = 'integer' AND created_at >= 0)
);

CREATE INDEX idx_signal_decisions_status_created
ON signal_decisions(decision_status, created_at);

CREATE TRIGGER signal_decisions_no_update -- NOSONAR: SQLite has no CREATE OR REPLACE TRIGGER.
BEFORE UPDATE ON signal_decisions
BEGIN
    SELECT RAISE(ABORT, 'signal_decisions are immutable');
END;

CREATE TRIGGER signal_decisions_no_delete -- NOSONAR: SQLite has no CREATE OR REPLACE TRIGGER.
BEFORE DELETE ON signal_decisions
BEGIN
    SELECT RAISE(ABORT, 'signal_decisions are immutable');
END;

CREATE TABLE domain_outbox_events (
    event_id TEXT PRIMARY KEY,
    signal_id INTEGER NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    event_sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    event_key TEXT NOT NULL,
    payload_contract_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at INTEGER NOT NULL,
    lease_owner TEXT,
    lease_expires_at INTEGER,
    last_error_code TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY(signal_id) REFERENCES lbank_signal_ledger(id),
    UNIQUE(event_key),
    UNIQUE(aggregate_type, aggregate_id, aggregate_version, event_sequence),
    CHECK(typeof(event_id) = 'text' AND length(event_id) > 0),
    CHECK(aggregate_type = 'signal'),
    CHECK(typeof(aggregate_id) = 'text' AND length(aggregate_id) > 0),
    CHECK(typeof(aggregate_version) = 'integer' AND aggregate_version >= 1),
    CHECK(typeof(event_sequence) = 'integer' AND event_sequence >= 1),
    CHECK(event_type = 'SIGNAL_CONFIRMED'),
    CHECK(typeof(event_key) = 'text' AND length(event_key) > 0),
    CHECK(payload_contract_version = 'signal_confirmed_event_v1'),
    CHECK(typeof(payload_json) = 'text' AND json_valid(payload_json)),
    CHECK(
        typeof(payload_hash) = 'text'
        AND length(payload_hash) = 64
        AND payload_hash NOT GLOB '*[^0-9a-f]*'
    ),
    CHECK(status IN ('PENDING', 'SENDING', 'DELIVERED', 'RETRY_WAIT', 'DEAD_LETTER', 'DELIVERY_UNCERTAIN')),
    CHECK(typeof(attempt_count) = 'integer' AND attempt_count >= 0),
    CHECK(typeof(available_at) = 'integer' AND available_at >= 0),
    CHECK(lease_expires_at IS NULL OR (typeof(lease_expires_at) = 'integer' AND lease_expires_at >= 0)),
    CHECK(typeof(created_at) = 'integer' AND created_at >= 0),
    CHECK(typeof(updated_at) = 'integer' AND updated_at >= created_at)
);

CREATE INDEX idx_domain_outbox_delivery_queue
ON domain_outbox_events(status, available_at, created_at);

CREATE TRIGGER domain_outbox_events_material_immutable -- NOSONAR: SQLite has no CREATE OR REPLACE TRIGGER.
BEFORE UPDATE OF
    event_id,
    signal_id,
    aggregate_type,
    aggregate_id,
    aggregate_version,
    event_sequence,
    event_type,
    event_key,
    payload_contract_version,
    payload_json,
    payload_hash,
    created_at
ON domain_outbox_events
BEGIN
    SELECT RAISE(ABORT, 'domain outbox event material is immutable');
END;

CREATE TRIGGER domain_outbox_events_no_delete -- NOSONAR: SQLite has no CREATE OR REPLACE TRIGGER.
BEFORE DELETE ON domain_outbox_events
BEGIN
    SELECT RAISE(ABORT, 'domain outbox events cannot be deleted');
END;

PRAGMA user_version=4;
