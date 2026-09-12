CREATE TABLE entry_decision_advisories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_event_id INTEGER NOT NULL,
    advisory_at INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    advisory_json TEXT NOT NULL,
    advisory_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(decision_event_id) REFERENCES entry_decision_events(id),
    CHECK(typeof(advisory_at) = 'integer' AND advisory_at >= 0),
    CHECK(typeof(provider) = 'text' AND length(provider) > 0),
    CHECK(typeof(model) = 'text' AND length(model) > 0),
    CHECK(status IN ('AVAILABLE','UNAVAILABLE')),
    CHECK(json_valid(advisory_json)),
    CHECK(length(advisory_hash) = 64 AND advisory_hash NOT GLOB '*[^0-9a-f]*'),
    CHECK(typeof(created_at) = 'integer' AND created_at >= 0)
);

CREATE INDEX idx_entry_decision_advisory_event
ON entry_decision_advisories(decision_event_id, id);

CREATE TRIGGER entry_decision_advisories_no_update
BEFORE UPDATE ON entry_decision_advisories
BEGIN
    SELECT RAISE(ABORT, 'entry decision advisories are immutable');
END;

CREATE TRIGGER entry_decision_advisories_no_delete
BEFORE DELETE ON entry_decision_advisories
BEGIN
    SELECT RAISE(ABORT, 'entry decision advisories are immutable');
END;

PRAGMA user_version=7;
