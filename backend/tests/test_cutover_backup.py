from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/certify_cutover_sqlite_backup.py"
REHEARSE = ROOT / "scripts/rehearse_sqlite_migration.py"


def _seed(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=0")


def test_local_cutover_certificate_is_explicitly_non_independent(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    restore = tmp_path / "restore.db"
    report = tmp_path / "cutover.json"
    _seed(source)
    result = subprocess.run([
        sys.executable, str(SCRIPT),
        "--source", str(source.resolve()),
        "--backup", str(backup.resolve()),
        "--restore-target", str(restore.resolve()),
        "--report", str(report.resolve()),
        "--source-revision", "a" * 40,
    ], cwd=ROOT, env={**__import__('os').environ, "PYTHONPATH": "backend/src:."}, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout
    certificate = json.loads(report.read_text())
    assert certificate["certificate_type"] == "waterfallhunter_db_backup_v1"
    assert certificate["status"] == "PASS"
    assert certificate["device_separation_enforced"] is False
    assert certificate["source_volume_preserved_until_post_cutover"] is True
    assert certificate["integrity_check"] == "ok"
    assert len(certificate["sha256"]) == 64
    assert backup.exists()
    assert restore.exists()


def test_migration_rehearsal_accepts_cutover_wrapper(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    restore = tmp_path / "restore.db"
    cert = tmp_path / "cutover.json"
    migration = tmp_path / "migration.db"
    rollback = tmp_path / "rollback.db"
    rehearsal = tmp_path / "rehearsal.json"
    _seed(source)
    first = subprocess.run([
        sys.executable, str(SCRIPT), "--source", str(source.resolve()),
        "--backup", str(backup.resolve()), "--restore-target", str(restore.resolve()),
        "--report", str(cert.resolve()), "--source-revision", "a" * 40,
    ], cwd=ROOT, env={**__import__('os').environ, "PYTHONPATH": "backend/src:."}, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr + first.stdout
    second = subprocess.run([
        sys.executable, str(REHEARSE), "--backup-certification", str(cert.resolve()),
        "--migration-target", str(migration.resolve()), "--rollback-target", str(rollback.resolve()),
        "--source-revision", "a" * 40, "--report", str(rehearsal.resolve()),
    ], cwd=ROOT, env={**__import__('os').environ, "PYTHONPATH": "backend/src:."}, text=True, capture_output=True)
    assert second.returncode == 0, second.stderr + second.stdout
    data = json.loads(rehearsal.read_text())
    assert data["status"] == "MIGRATION_AND_ROLLBACK_REHEARSED"


def test_migration_rehearsal_rejects_non_object_backup_audit_cleanly(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    restore = tmp_path / "restore.db"
    cert = tmp_path / "cutover.json"
    migration = tmp_path / "migration.db"
    rollback = tmp_path / "rollback.db"
    rehearsal = tmp_path / "rehearsal.json"
    _seed(source)
    first = subprocess.run([
        sys.executable, str(SCRIPT), "--source", str(source.resolve()),
        "--backup", str(backup.resolve()), "--restore-target", str(restore.resolve()),
        "--report", str(cert.resolve()), "--source-revision", "a" * 40,
    ], cwd=ROOT, env={**__import__('os').environ, "PYTHONPATH": "backend/src:."}, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr + first.stdout
    data = json.loads(cert.read_text())
    data["sqlite_backup_certification"]["backup_audit"] = None
    from waterfallhunter.core.signal_metadata import canonical_sha256
    material = {key: value for key, value in data.items() if key != "certificate_sha256"}
    data["certificate_sha256"] = canonical_sha256(material)
    cert.write_text(json.dumps(data))
    second = subprocess.run([
        sys.executable, str(REHEARSE), "--backup-certification", str(cert.resolve()),
        "--migration-target", str(migration.resolve()), "--rollback-target", str(rollback.resolve()),
        "--source-revision", "a" * 40, "--report", str(rehearsal.resolve()),
    ], cwd=ROOT, env={**__import__('os').environ, "PYTHONPATH": "backend/src:."}, text=True, capture_output=True)
    assert second.returncode == 2
    assert "CUTOVER_BACKUP_CORE_CERTIFICATION_MISMATCH" in second.stdout


def test_generated_cutover_db_certificate_is_accepted_by_cleanup_validator(tmp_path: Path) -> None:
    import importlib.util
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    restore = tmp_path / "restore.db"
    report = tmp_path / "cutover.json"
    _seed(source)
    result = subprocess.run([
        sys.executable, str(SCRIPT),
        "--source", str(source.resolve()),
        "--backup", str(backup.resolve()),
        "--restore-target", str(restore.resolve()),
        "--report", str(report.resolve()),
        "--source-revision", "a" * 40,
    ], cwd=ROOT, env={**__import__('os').environ, "PYTHONPATH": "backend/src:."}, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout
    cleanup_path = ROOT / "scripts/cleanup_legacy_wfh.py"
    spec = importlib.util.spec_from_file_location("cleanup_cutover_cert_integration", cleanup_path)
    assert spec and spec.loader
    cleanup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cleanup)
    cleanup.CERTIFIED_BACKUP_ROOTS = (tmp_path,)
    accepted = cleanup.validate_db_certificate(report)
    assert accepted["status"] == "PASS"
    assert accepted["sha256"] == json.loads(report.read_text())["sha256"]


def test_migration_rehearsal_cli_supports_sequential_single_target_mode(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-sequential.db"
    backup = tmp_path / "backup-sequential.db"
    restore = tmp_path / "restore-sequential.db"
    cert = tmp_path / "cutover-sequential.json"
    working = tmp_path / "working-sequential.db"
    rehearsal = tmp_path / "rehearsal-sequential.json"
    _seed(source)
    first = subprocess.run([
        sys.executable, str(SCRIPT), "--source", str(source.resolve()),
        "--backup", str(backup.resolve()), "--restore-target", str(restore.resolve()),
        "--report", str(cert.resolve()), "--source-revision", "a" * 40,
    ], cwd=ROOT, env={**__import__('os').environ, "PYTHONPATH": "backend/src:."}, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr + first.stdout

    second = subprocess.run([
        sys.executable, str(REHEARSE), "--backup-certification", str(cert.resolve()),
        "--sequential-working-target", str(working.resolve()),
        "--source-revision", "a" * 40, "--report", str(rehearsal.resolve()),
    ], cwd=ROOT, env={**__import__('os').environ, "PYTHONPATH": "backend/src:."}, text=True, capture_output=True)
    assert second.returncode == 0, second.stderr + second.stdout
    data = json.loads(rehearsal.read_text())
    assert data["contract_version"] == "sqlite_migration_rollback_rehearsal_v2"
    assert data["rollback_artifact_retained"] is True
    assert working.is_file()
