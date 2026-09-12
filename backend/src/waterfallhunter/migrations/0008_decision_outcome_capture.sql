CREATE TABLE decision_outcome_capture (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_event_id INTEGER NOT NULL UNIQUE,
    capture_version TEXT NOT NULL,
    captured_at INTEGER NOT NULL,
    outcome_status TEXT NOT NULL,
    capture_json TEXT NOT NULL,
    capture_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(decision_event_id) REFERENCES entry_decision_events(id),
    CHECK(capture_version = 'decision_outcome_capture_v1'),
    CHECK(typeof(captured_at) = 'integer' AND captured_at >= 0),
    CHECK(outcome_status IN ('UNOBSERVED','OBSERVED')),
    CHECK(json_valid(capture_json)),
    CHECK(length(capture_hash) = 64 AND capture_hash NOT GLOB '*[^0-9a-f]*'),
    CHECK(typeof(created_at) = 'integer' AND created_at >= 0)
);

CREATE INDEX idx_decision_outcome_capture_status_at
ON decision_outcome_capture(outcome_status, captured_at);

CREATE TRIGGER decision_outcome_capture_no_update
BEFORE UPDATE ON decision_outcome_capture
BEGIN
    SELECT RAISE(ABORT, 'decision outcome captures are immutable');
END;

CREATE TRIGGER decision_outcome_capture_no_delete
BEFORE DELETE ON decision_outcome_capture
BEGIN
    SELECT RAISE(ABORT, 'decision outcome captures are immutable');
END;

PRAGMA user_version=8;
