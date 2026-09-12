from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = ROOT / "scripts/audit_host_inventory.py"
CLEANUP_PATH = ROOT / "scripts/cleanup_legacy_wfh.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_classification_protects_general_agent_tooling() -> None:
    audit = _load_module(AUDIT_PATH, "audit_host_inventory")
    assert audit.classify_path(Path("/root/.codex"))[0] == "PROTECTED"
    assert audit.classify_path(Path("/root/.vscode-server"))[0] == "PROTECTED"
    assert audit.classify_path(Path("/home/ubuntu/.ssh"))[0] == "PROTECTED"


def test_audit_classification_marks_known_legacy_wfh_roots_only() -> None:
    audit = _load_module(AUDIT_PATH, "audit_host_inventory_legacy")
    assert audit.classify_path(Path("/srv/wfh-worktrees"))[0] == "DELETE_AFTER_CERTIFICATION"
    assert audit.classify_path(Path("/root/wfh-v7-local-rollback"))[0] == "DELETE_AFTER_CERTIFICATION"
    assert audit.classify_path(Path("/srv/waterfallhunter/app"))[0] == "KEEP"
    assert audit.classify_path(Path("/srv/waterfallhunter/backups"))[0] == "KEEP"


def test_cleanup_refuses_unknown_path_even_if_inventory_marks_delete(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"entries": [{
        "type": "path",
        "path_or_resource": "/root/.codex",
        "disposition": "DELETE_AFTER_CERTIFICATION",
        "reason": "malicious fixture",
    }]}))
    release = tmp_path / "release.json"
    db = tmp_path / "db.json"
    release.write_text(json.dumps({
        "certificate_type": "waterfallhunter_release_v1", "status": "PASS",
        "release_sha": "a" * 40, "production_healthy": True,
        "live_trading_enabled": False,
    }))
    db.write_text(json.dumps({
        "certificate_type": "waterfallhunter_db_backup_v1", "status": "PASS",
        "sha256": "b" * 64, "integrity_check": "ok", "backup_path": "/safe/backup.db",
    }))
    result = subprocess.run([
        sys.executable, str(CLEANUP_PATH), "--inventory", str(inventory),
        "--release-certificate", str(release), "--db-certificate", str(db), "--execute",
    ], text=True, capture_output=True)
    assert result.returncode != 0
    assert "protected" in (result.stderr + result.stdout).lower()


def test_cleanup_requires_release_and_db_certificates(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"entries": []}))
    result = subprocess.run([
        sys.executable, str(CLEANUP_PATH), "--inventory", str(inventory), "--execute",
    ], text=True, capture_output=True)
    assert result.returncode != 0
    assert "certificate" in (result.stderr + result.stdout).lower()


def test_docker_image_classification_is_wfh_scoped() -> None:
    audit = _load_module(AUDIT_PATH, "audit_host_inventory_images")
    assert audit.classify_docker_resource("image", "wfh-rollback-backend:old", {})[0] == "DELETE_AFTER_CERTIFICATION"
    assert audit.classify_docker_resource("image", "waterfallhunter-waterfall-backend:latest", {})[0] == "KEEP"
    assert audit.classify_docker_resource("image", "python:3.13-slim", {})[0] == "REVIEW"


def test_wfh_ollama_resources_are_cleanup_scoped_but_generic_image_is_not_blindly_deleted() -> None:
    audit = _load_module(AUDIT_PATH, "audit_host_inventory_ollama")
    assert audit.classify_docker_resource("container", "waterfallhunter-ollama-1", {"com.docker.compose.project": "waterfallhunter"})[0] == "DELETE_AFTER_CERTIFICATION"
    assert audit.classify_docker_resource("volume", "waterfallhunter_ollama_models", {"com.docker.compose.project": "waterfallhunter"})[0] == "DELETE_AFTER_CERTIFICATION"
    assert audit.classify_docker_resource("image", "ollama/ollama:latest", {})[0] == "REVIEW"


def test_audit_preserves_compose_managed_alertmanager() -> None:
    audit = _load_module(AUDIT_PATH, "audit_host_inventory_alertmanager")
    labels = {"com.docker.compose.project": "waterfallhunter", "com.docker.compose.service": "alertmanager"}
    assert audit.classify_docker_resource("container", "waterfallhunter-alertmanager-1", labels)[0] == "KEEP"


def test_cleanup_refuses_certified_backup_or_ancestor_target(tmp_path: Path) -> None:
    cleanup = _load_module(CLEANUP_PATH, "cleanup_backup_protection")
    backup = Path("/srv/wfh-release-backups/certified/backup.db")
    assert cleanup._target_contains_certified_backup(Path("/srv/wfh-release-backups"), backup) is True
    assert cleanup._target_contains_certified_backup(backup, backup) is True
    assert cleanup._target_contains_certified_backup(Path("/srv/wfh-worktrees"), backup) is False


def test_direct_operational_scripts_are_importable_without_pythonpath() -> None:
    for script in (ROOT / "scripts/certify_cutover_sqlite_backup.py", ROOT / "scripts/rehearse_sqlite_migration.py"):
        result = subprocess.run([sys.executable, str(script), "--help"], cwd=ROOT, text=True, capture_output=True, env={"PATH": __import__("os").environ.get("PATH", "")})
        assert result.returncode == 0, result.stderr + result.stdout


def test_generated_release_certificate_is_accepted_by_cleanup_validator(tmp_path: Path) -> None:
    verify = _load_module(ROOT / "scripts/verify_production_cutover.py", "verify_release_cert_integration")
    cleanup = _load_module(CLEANUP_PATH, "cleanup_release_cert_integration")
    sha = "a" * 40
    snapshot = {
        "healthy": True,
        "running_revision": sha,
        "core_revisions": {
            "waterfall-backend": sha,
            "waterfall-frontend": sha,
            "waterfall-watchdog": sha,
        },
        "checkout_revision": sha,
        "live_trading_enabled": False,
        "backend_endpoints": {"/livez": True, "/readyz": True, "/healthz": True},
        "notification_delivery_ready": True,
        "notification_delivery": {
            "healthy": True,
            "transport": {
                "configured": True,
                "worker_running": True,
                "probe": {
                    "reachable": True,
                    "bot_reachable": True,
                    "chat_reachable": True,
                },
            }
        },
    }
    certificate = verify.build_release_certificate(snapshot, generated_at=123)
    path = tmp_path / "release.json"
    path.write_text(json.dumps(certificate), encoding="utf-8")
    accepted = cleanup.validate_release_certificate(path)
    assert accepted["release_sha"] == sha
    assert accepted["certificate_sha256"] == certificate["certificate_sha256"]


def test_cleanup_path_validation_rejects_traversal_alias() -> None:
    cleanup = _load_module(CLEANUP_PATH, "cleanup_path_validation")
    inventory = {"entries": [{
        "type": "path",
        "path_or_resource": "/srv/wfh-worktrees/../waterfallhunter/app",
        "disposition": "DELETE_AFTER_CERTIFICATION",
    }]}
    try:
        cleanup._validated_delete_entries(inventory)
    except ValueError:
        pass
    else:
        raise AssertionError("cleanup traversal aliases must be rejected")


def test_cleanup_docker_name_validation_rejects_option_injection() -> None:
    cleanup = _load_module(CLEANUP_PATH, "cleanup_docker_name_validation")
    try:
        cleanup._validated_docker_resource_name("container", "--force")
    except ValueError:
        pass
    else:
        raise AssertionError("option-like Docker resource names must be rejected")


def test_cleanup_certificate_output_is_canonical_runtime_only() -> None:
    cleanup = _load_module(CLEANUP_PATH, "cleanup_output_validation")
    try:
        cleanup._cleanup_certificate_output(Path("/tmp/cleanup.json"))
    except ValueError:
        pass
    else:
        raise AssertionError("cleanup certificate output must be runtime-scoped")


def test_certified_backup_path_is_restricted_to_configured_roots(tmp_path: Path) -> None:
    cleanup = _load_module(CLEANUP_PATH, "cleanup_certified_backup_root")
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"not-used")
    try:
        cleanup._canonical_certified_backup_path(outside)
    except ValueError:
        pass
    else:
        raise AssertionError("certified backup paths outside configured roots must be rejected")


def test_adopted_production_volume_is_protected_by_resolved_compose_topology() -> None:
    audit = _load_module(AUDIT_PATH, "audit_adopted_volume")
    adopted = f"{'a' * 40}_waterfall_data"
    labels = {"com.docker.compose.project": "a" * 40}
    disposition, _ = audit.classify_docker_resource(
        "volume", adopted, labels, protected_volume_names={adopted}
    )
    assert disposition == "KEEP"


def test_adopted_production_network_is_protected_by_resolved_compose_topology() -> None:
    audit = _load_module(AUDIT_PATH, "audit_adopted_network")
    adopted = f"{'a' * 40}_edge"
    labels = {"com.docker.compose.project": "a" * 40}
    disposition, _ = audit.classify_docker_resource(
        "network", adopted, labels, protected_network_names={adopted}
    )
    assert disposition == "KEEP"


def test_production_topology_resolver_avoids_service_env_file_resolution(
    tmp_path: Path, monkeypatch
) -> None:
    audit = _load_module(AUDIT_PATH, "audit_topology_resolver")
    app = tmp_path / "app"
    app.mkdir()
    (app / "docker-compose.yml").write_text("services: {}\n")
    env_file = tmp_path / "waterfallhunter.env"
    env_file.write_text("LIVE_TRADING_ENABLED=false\n")
    override = tmp_path / "production-volumes.override.yml"
    override.write_text("volumes: {}\n")
    monkeypatch.setattr(audit, "PRODUCTION_PROJECT_DIR", app)
    monkeypatch.setattr(audit, "PRODUCTION_ENV_FILE", env_file)
    monkeypatch.setattr(audit, "PRODUCTION_OVERRIDE", override)
    captured = {}

    class Result:
        returncode = 0
        stderr = ""
        stdout = json.dumps({
            "volumes": {
                "waterfall_data": {
                    "external": True,
                    "name": f"{'a' * 40}_waterfall_data",
                }
            },
            "networks": {
                "edge": {
                    "external": True,
                    "name": f"{'a' * 40}_edge",
                }
            },
        })

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return Result()

    monkeypatch.setattr(audit.subprocess, "run", fake_run)
    protected = audit._production_compose_resource_names()

    assert "--no-env-resolution" in captured["command"]
    assert captured["env"]["WFH_ENV_FILE"] == str(env_file)
    assert f"{'a' * 40}_waterfall_data" in protected["volume"]
    assert f"{'a' * 40}_edge" in protected["network"]


def test_docker_enumeration_failure_is_not_treated_as_empty(monkeypatch) -> None:
    audit = _load_module(AUDIT_PATH, "audit_docker_failure")

    class Result:
        returncode = 1
        stdout = ""
        stderr = "daemon unavailable"

    monkeypatch.setattr(audit.subprocess, "run", lambda *args, **kwargs: Result())
    try:
        audit._docker_json(["docker", "ps", "-a", "--format", "{{json .}}"])
    except RuntimeError as exc:
        assert "Docker inventory" in str(exc)
    else:
        raise AssertionError("Docker enumeration failures must fail the inventory")


def test_cleanup_revalidates_current_compose_labels_for_sha_project_resource(monkeypatch) -> None:
    cleanup = _load_module(CLEANUP_PATH, "cleanup_current_labels")
    name = "legacy-frontend-1"
    labels = {"com.docker.compose.project": "a" * 40}
    monkeypatch.setattr(cleanup, "_current_docker_labels", lambda kind, value: labels)

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(cleanup.subprocess, "run", lambda *args, **kwargs: Result())
    cleanup._delete_entry(
        {"type": "docker-container", "path_or_resource": name},
        protected_volume_names=set(),
        protected_network_names=set(),
    )


def test_cleanup_binds_database_volume_to_its_certified_source(tmp_path: Path, monkeypatch) -> None:
    cleanup = _load_module(CLEANUP_PATH, "cleanup_database_binding")
    assert hasattr(cleanup, "_assert_database_target_certificate_binding")
    mountpoint = tmp_path / "legacy-volume"
    mountpoint.mkdir()
    source = mountpoint / "waterfall_registry.db"
    source.write_bytes(b"sqlite-fixture")
    stat = source.stat()
    legacy_sha = "a" * 40
    monkeypatch.setattr(cleanup, "_docker_volume_mountpoint", lambda name: mountpoint)
    entry = {
        "type": "docker-volume",
        "path_or_resource": "legacy_waterfall_data",
        "labels": {"com.docker.compose.project": legacy_sha},
    }
    certificate = {
        "source_revision": legacy_sha,
        "sqlite_backup_certification": {
            "source_path": str(source),
            "source_identity": {"device_id": int(stat.st_dev), "inode": int(stat.st_ino)},
        },
    }
    cleanup._assert_database_target_certificate_binding(entry, certificate)
    different_revision = {**certificate, "source_revision": "b" * 40}
    cleanup._assert_database_target_certificate_binding(entry, different_revision)
    bad_core = {**certificate["sqlite_backup_certification"], "source_path": str(tmp_path / "other.db")}
    bad = {**certificate, "sqlite_backup_certification": bad_core}
    try:
        cleanup._assert_database_target_certificate_binding(entry, bad)
    except ValueError:
        pass
    else:
        raise AssertionError("database cleanup must reject a certificate from another database source")


def test_cleanup_revalidates_release_certificate_against_live_runtime(monkeypatch) -> None:
    cleanup = _load_module(CLEANUP_PATH, "cleanup_release_revalidation")
    assert hasattr(cleanup, "_revalidate_release_certificate_for_cleanup")
    sha = "a" * 40
    release = {"release_sha": sha, "certified_at": 100}
    monkeypatch.setattr(cleanup.time, "time", lambda: 101)
    monkeypatch.setattr(
        cleanup,
        "release_evidence_snapshot",
        lambda project_dir, env_file: {
            "healthy": True,
            "running_revision": sha,
            "checkout_revision": sha,
            "core_revisions": {
                "waterfall-backend": sha,
                "waterfall-frontend": sha,
                "waterfall-watchdog": sha,
            },
            "live_trading_enabled": False,
            "notification_delivery_ready": True,
        },
    )
    cleanup._revalidate_release_certificate_for_cleanup(release)
    monkeypatch.setattr(cleanup.time, "time", lambda: 100 + cleanup.MAX_RELEASE_CERTIFICATE_AGE_SECONDS + 1)
    try:
        cleanup._revalidate_release_certificate_for_cleanup(release)
    except ValueError:
        pass
    else:
        raise AssertionError("stale release certificates must not authorize destructive cleanup")


def test_inventory_includes_dangling_wfh_image_by_image_id(monkeypatch) -> None:
    audit = _load_module(AUDIT_PATH, "audit_dangling_image")
    image_id = "a" * 12
    def fake_json(command):
        if "image" in command:
            return [{"Repository": "<none>", "Tag": "<none>", "ID": image_id}]
        return []
    class Result:
        returncode = 0
        stderr = ""
        stdout = json.dumps([{"Config": {"Labels": {"org.opencontainers.image.source": "https://github.com/cavack/WFH-ORG"}}}])
    monkeypatch.setattr(audit, "_docker_json", fake_json)
    monkeypatch.setattr(audit, "_production_compose_resource_names", lambda: {"volume": set(), "network": set()})
    monkeypatch.setattr(audit.subprocess, "run", lambda *args, **kwargs: Result())
    entries = audit._docker_entries()
    matches = [entry for entry in entries if entry["type"] == "docker-image"]
    assert matches == [{
        "type": "docker-image",
        "path_or_resource": image_id,
        "size_bytes": 0,
        "mtime": 0.0,
        "disposition": "DELETE_AFTER_CERTIFICATION",
        "reason": "legacy/orphan WaterfallHunter Docker resource",
        "labels": {"org.opencontainers.image.source": "https://github.com/cavack/WFH-ORG"},
    }]
