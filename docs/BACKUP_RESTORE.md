# Backup and Restore

## Backup gate

Before a production migration or destructive cleanup, create a certified SQLite backup. Certification records source release SHA, schema/user version, file size, SHA-256, UTC timestamps, SQLite integrity result, and representative table counts.

Use `scripts/certify_sqlite_backup.py` for a certified backup and `scripts/rehearse_sqlite_migration.py` for isolated target migration/rollback rehearsal.


## Same-host cutover backup

`scripts/certify_cutover_sqlite_backup.py` is a separate, explicitly non-independent certificate for host-layout migration only. It may be used when the original live Docker volume remains untouched until the new canonical database passes post-cutover certification. Its certificate records `device_separation_enforced=false`, `independent_disaster_recovery=false`, and `source_volume_preserved_until_post_cutover=true`. The independently restored verification clone is retained beside the backup/report instead of being deleted, so certification evidence remains inspectable. It never replaces a true off-device/failure-domain backup policy.

## Restore rule

Never overwrite the only good backup. Restore into a new file/volume, run `PRAGMA integrity_check`, verify schema and canonical table/view invariants, then cut runtime to the restored data only through the guarded deployment path.

## Retention

Keep a small bounded recovery set, normally two certified backups, while retaining at least one valid pre-migration recovery point until post-cutover certification is complete.

## Cleanup dependency

Legacy databases/backups are deletion-eligible only after proving they are neither the canonical production DB nor a required certified recovery artifact.
