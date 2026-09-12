from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

from waterfallhunter.core.migration_preflight import (
    PreflightResult,
    PreflightState,
    classify_database,
)
from waterfallhunter.core.migrations import (
    MigrationError,
    MigrationRunner,
    discover_migrations,
)
from waterfallhunter.core.schema_contract import CURRENT_RUNTIME_SCHEMA_VERSION


def _emit(payload: dict) -> None:
    """Emit one bounded machine-readable result without database contents."""
    print(
        json.dumps(
            payload,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _base_payload(
    *,
    ok: bool,
    mode: str | None,
    state: str | None = None,
    reason_codes: Sequence[str] = (),
    applied_versions: Sequence[int] = (),
    user_version: int | None = None,
) -> dict:
    payload: dict = {
        "ok": bool(ok),
        "mode": mode,
        "state": state,
        "reason_codes": list(reason_codes),
        "applied_versions": [int(value) for value in applied_versions],
        "user_version": user_version,
    }
    return payload


def _preflight_payload(result: PreflightResult) -> dict:
    return _base_payload(
        ok=result.compatible,
        mode="preflight",
        state=result.state.value,
        reason_codes=result.reason_codes,
        applied_versions=result.applied_versions,
        user_version=result.user_version,
    )


def _validated_db_path(raw_value: str) -> Path | None:
    """Accept one canonical local SQLite file path from the operator boundary."""
    if "\x00" in raw_value:
        return None

    candidate = Path(raw_value)
    if not candidate.is_absolute() or candidate.is_symlink():
        return None

    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None

    # Reject parent-symlink and traversal aliases so the validated target is
    # exactly the file SQLite will open. Production uses /app/data directly;
    # tests use canonical absolute tmp paths.
    if resolved != candidate:
        return None

    if candidate.exists() and not candidate.is_file():
        return None

    return candidate


def _set_wal_mode(db_path: Path) -> str:
    """Set the persistent journal mode only after successful explicit migration."""
    conn = sqlite3.connect(
        db_path,
        timeout=5.0,
        isolation_level=None,
        uri=False,
    )
    try:
        row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        mode = str(row[0] if row else "").lower()
        if mode != "wal":
            raise MigrationError("database journal mode did not become WAL")
        verify = conn.execute("PRAGMA journal_mode").fetchone()
        verified = str(verify[0] if verify else "").lower()
        if verified != "wal":
            raise MigrationError("database journal mode verification failed")
        return verified
    finally:
        conn.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m waterfallhunter.migrate_database",
        description="Preflight or explicitly apply WaterfallHunter SQLite migrations.",
    )
    parser.add_argument("--db-path")
    parser.add_argument("--source-revision")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser


def _validate_operation_args(args: argparse.Namespace) -> tuple[Path, str] | None:
    """Validate the operator boundary and emit the existing error payload."""
    reason_code: str | None = None
    mode: str | None = None

    if not args.db_path:
        reason_code = "DB_PATH_REQUIRED"
    elif args.preflight and args.apply:
        reason_code = "MODE_CONFLICT"
    elif not args.preflight and not args.apply:
        reason_code = "MODE_REQUIRED"
    elif args.apply and not str(args.source_revision or "").strip():
        reason_code = "SOURCE_REVISION_REQUIRED"
        mode = "apply"

    if reason_code is not None:
        _emit(
            _base_payload(
                ok=False,
                mode=mode,
                reason_codes=(reason_code,),
            )
        )
        return None

    db_path = _validated_db_path(str(args.db_path))
    if db_path is None:
        _emit(
            _base_payload(
                ok=False,
                mode="apply" if args.apply else "preflight",
                reason_codes=("DB_PATH_INVALID",),
            )
        )
        return None

    return db_path, str(args.source_revision or "").strip()


def _run_apply(
    *,
    db_path: Path,
    source_revision: str,
    preflight: PreflightResult,
) -> int:
    """Apply, verify, and emit one result while preserving CLI exit codes."""
    if not preflight.compatible:
        _emit(
            _base_payload(
                ok=False,
                mode="apply",
                state=preflight.state.value,
                reason_codes=preflight.reason_codes,
                applied_versions=preflight.applied_versions,
                user_version=preflight.user_version,
            )
        )
        return 3

    try:
        packaged_versions = tuple(
            migration.version for migration in discover_migrations()
        )
        runner = MigrationRunner(
            db_path=db_path,
            source_revision=source_revision,
        )
        applied = runner.apply()
        runner.verify()
        journal_mode = _set_wal_mode(db_path)
        postflight = classify_database(db_path=db_path)
    except (MigrationError, sqlite3.Error, OSError):
        _emit(
            _base_payload(
                ok=False,
                mode="apply",
                reason_codes=("MIGRATION_FAILED",),
            )
        )
        return 4

    if (
        postflight.state is not PreflightState.MIGRATED_COMPATIBLE
        or postflight.user_version != CURRENT_RUNTIME_SCHEMA_VERSION
        or postflight.applied_versions != packaged_versions
    ):
        _emit(
            _base_payload(
                ok=False,
                mode="apply",
                state=postflight.state.value,
                reason_codes=("POSTFLIGHT_FAILED",),
                applied_versions=postflight.applied_versions,
                user_version=postflight.user_version,
            )
        )
        return 4

    payload = _base_payload(
        ok=True,
        mode="apply",
        state=postflight.state.value,
        applied_versions=applied,
        user_version=postflight.user_version,
    )
    payload["journal_mode"] = journal_mode
    _emit(payload)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run one explicit migration operation; never mutates without ``--apply``."""
    args = _parser().parse_args(list(argv) if argv is not None else None)
    validated = _validate_operation_args(args)
    if validated is None:
        return 2
    db_path, source_revision = validated

    preflight = classify_database(db_path=db_path)

    if args.preflight:
        _emit(_preflight_payload(preflight))
        return 0 if preflight.compatible else 3

    return _run_apply(
        db_path=db_path,
        source_revision=source_revision,
        preflight=preflight,
    )


if __name__ == "__main__":  # pragma: no cover - exercised through main(argv)
    sys.exit(main())
