CREATE TABLE lifecycle_v2_shadow_events (
    event_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    v1_state TEXT NOT NULL,
    v2_from_state TEXT NOT NULL,
    v2_to_state TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    observed_at INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    feature_registry_hash TEXT NOT NULL,
    strategy_profile TEXT NOT NULL,
    transition_hash TEXT NOT NULL,
    comparison_hash TEXT NOT NULL,
    shadow_only INTEGER NOT NULL,
    promotion_allowed INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(transition_hash),
    CHECK(typeof(event_id) = 'text' AND length(event_id) > 0),
    CHECK(typeof(episode_id) = 'text' AND length(episode_id) > 0),
    CHECK(typeof(symbol) = 'text' AND length(symbol) > 0),
    CHECK(json_valid(reason_codes_json)),
    CHECK(json_valid(evidence_refs_json)),
    CHECK(typeof(observed_at) = 'integer' AND observed_at >= 0),
    CHECK(length(transition_hash) = 64 AND transition_hash NOT GLOB '*[^0-9a-f]*'), -- NOSONAR
    CHECK(length(comparison_hash) = 64 AND comparison_hash NOT GLOB '*[^0-9a-f]*'), -- NOSONAR
    CHECK(length(policy_hash) = 64 AND policy_hash NOT GLOB '*[^0-9a-f]*'), -- NOSONAR
    CHECK(length(feature_registry_hash) = 64 AND feature_registry_hash NOT GLOB '*[^0-9a-f]*'), -- NOSONAR
    CHECK(shadow_only = 1),
    CHECK(promotion_allowed = 0),
    CHECK(typeof(created_at) = 'integer' AND created_at >= 0)
);

CREATE INDEX idx_lifecycle_v2_shadow_symbol_observed
ON lifecycle_v2_shadow_events(symbol, observed_at);

CREATE TRIGGER lifecycle_v2_shadow_events_no_update -- NOSONAR: SQLite has no CREATE OR REPLACE TRIGGER.
BEFORE UPDATE ON lifecycle_v2_shadow_events
BEGIN
    SELECT RAISE(ABORT, 'lifecycle v2 shadow events are immutable');
END;

CREATE TRIGGER lifecycle_v2_shadow_events_no_delete -- NOSONAR: SQLite has no CREATE OR REPLACE TRIGGER.
BEFORE DELETE ON lifecycle_v2_shadow_events
BEGIN
    SELECT RAISE(ABORT, 'lifecycle v2 shadow events are immutable');
END;

PRAGMA user_version=5;
