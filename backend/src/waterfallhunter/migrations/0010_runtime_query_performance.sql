CREATE INDEX idx_production_evidence_replay_v9
ON production_evidence_snapshots(schema_version, id);

CREATE INDEX idx_production_evidence_replay_v8
ON production_evidence_snapshots(
    schema_version,
    production_evidence_complete_v5,
    decision_packet_complete,
    code_sha256_v5,
    id
);

CREATE INDEX idx_entry_decision_symbol_id
ON entry_decision_events(symbol, id);

PRAGMA user_version=10;
