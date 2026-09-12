CREATE TABLE decision_outcome_resolution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_event_id INTEGER NOT NULL UNIQUE,
    resolution_version TEXT NOT NULL,
    resolved_at INTEGER NOT NULL,
    outcome_status TEXT NOT NULL,
    resolution_json TEXT NOT NULL,
    resolution_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(decision_event_id) REFERENCES entry_decision_events(id),
    CHECK(resolution_version = 'decision_outcome_resolution_v1'),
    CHECK(typeof(resolved_at) = 'integer' AND resolved_at >= 0),
    CHECK(outcome_status IN ('OBSERVED','UNOBSERVABLE','UNAVAILABLE')),
    CHECK(json_valid(resolution_json)),
    CHECK(length(resolution_hash) = 64 AND resolution_hash NOT GLOB '*[^0-9a-f]*'),
    CHECK(typeof(created_at) = 'integer' AND created_at >= 0)
);

CREATE INDEX idx_decision_outcome_resolution_status_at
ON decision_outcome_resolution(outcome_status, resolved_at);

CREATE TRIGGER decision_outcome_resolution_no_update
BEFORE UPDATE ON decision_outcome_resolution
BEGIN
    SELECT RAISE(ABORT, 'decision outcome resolutions are immutable');
END;

CREATE TRIGGER decision_outcome_resolution_no_delete
BEFORE DELETE ON decision_outcome_resolution
BEGIN
    SELECT RAISE(ABORT, 'decision outcome resolutions are immutable');
END;

PRAGMA user_version=9;
