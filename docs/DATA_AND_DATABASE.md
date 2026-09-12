# Data and Database

WaterfallHunter uses SQLite as the canonical persistent application store. Production data is persistent host/runtime state, not repository content.

## Canonical lineage

The existing production database is valuable evidence and must be preserved through upgrades. Never replace it with an empty database merely to simplify deployment.

The runtime database path is `/app/data/waterfall_registry.db`. The host deployment binds it through the canonical `waterfall_data` persistent volume/topology override.

## Schema

Schema changes are first-party numbered migrations. Deployment performs preflight, certified backup, migration apply, and post-migration integrity/schema checks. Historical pre-cutover rows may remain quarantined where metadata cannot be honestly reconstructed.

## Retention

Keep the canonical database and a bounded set of certified recovery backups. Evidence/replay tables may be large; retention changes require explicit data-ownership rules, not ad-hoc deletion.

## Never commit

Databases, WAL/SHM files, evidence dumps, backups, logs, or secrets are ignored and must not be tracked.
