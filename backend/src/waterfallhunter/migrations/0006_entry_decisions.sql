CREATE TABLE entry_decision_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    event_at INTEGER NOT NULL,
    decision TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    entry_readiness REAL NOT NULL,
    evidence_coverage_pct REAL NOT NULL,
    policy_version TEXT NOT NULL,
    packet_json TEXT NOT NULL,
    packet_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    CHECK(typeof(symbol) = 'text' AND length(symbol) > 0),
    CHECK(typeof(event_at) = 'integer' AND event_at >= 0),
    CHECK(decision IN ('NO_TRADE','FORMING','ENTRY_READY','ACTIVE','LATE','INVALIDATED','EXPIRED')),
    CHECK(typeof(entry_readiness) IN ('integer','real') AND entry_readiness >= 0 AND entry_readiness <= 100),
    CHECK(typeof(evidence_coverage_pct) IN ('integer','real') AND evidence_coverage_pct >= 0 AND evidence_coverage_pct <= 100),
    CHECK(json_valid(packet_json)),
    CHECK(length(packet_hash) = 64 AND packet_hash NOT GLOB '*[^0-9a-f]*'),
    CHECK(typeof(created_at) = 'integer' AND created_at >= 0)
);

CREATE INDEX idx_entry_decision_symbol_event
ON entry_decision_events(symbol, event_at, id);

CREATE INDEX idx_entry_decision_decision_event
ON entry_decision_events(decision, event_at);

CREATE TRIGGER entry_decision_events_no_update
BEFORE UPDATE ON entry_decision_events
BEGIN
    SELECT RAISE(ABORT, 'entry decision events are immutable');
END;

CREATE TRIGGER entry_decision_events_no_delete
BEFORE DELETE ON entry_decision_events
BEGIN
    SELECT RAISE(ABORT, 'entry decision events are immutable');
END;

CREATE TABLE entry_notification_outbox (
    event_id TEXT PRIMARY KEY,
    decision_event_id INTEGER NOT NULL,
    event_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
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
    FOREIGN KEY(decision_event_id) REFERENCES entry_decision_events(id),
    CHECK(event_type = 'ENTRY_READY'),
    CHECK(payload_contract_version = 'entry_ready_notification_v1'),
    CHECK(json_valid(payload_json)),
    CHECK(length(payload_hash) = 64 AND payload_hash NOT GLOB '*[^0-9a-f]*'),
    CHECK(status IN ('PENDING','SENDING','DELIVERED','RETRY_WAIT','DEAD_LETTER','DELIVERY_UNCERTAIN')),
    CHECK(typeof(attempt_count) = 'integer' AND attempt_count >= 0),
    CHECK(typeof(available_at) = 'integer' AND available_at >= 0),
    CHECK(lease_expires_at IS NULL OR (typeof(lease_expires_at) = 'integer' AND lease_expires_at >= 0)),
    CHECK(typeof(created_at) = 'integer' AND created_at >= 0),
    CHECK(typeof(updated_at) = 'integer' AND updated_at >= created_at)
);

CREATE INDEX idx_entry_notification_delivery_queue
ON entry_notification_outbox(status, available_at, created_at);

CREATE TRIGGER entry_notification_outbox_material_immutable
BEFORE UPDATE OF event_id,decision_event_id,event_key,event_type,payload_contract_version,payload_json,payload_hash,created_at
ON entry_notification_outbox
BEGIN
    SELECT RAISE(ABORT, 'entry notification material is immutable');
END;

CREATE TRIGGER entry_notification_outbox_no_delete
BEFORE DELETE ON entry_notification_outbox
BEGIN
    SELECT RAISE(ABORT, 'entry notification events cannot be deleted');
END;

PRAGMA user_version=6;
