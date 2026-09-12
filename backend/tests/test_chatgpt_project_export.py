from __future__ import annotations

import hashlib
import inspect
import json

import pytest
from pathlib import Path

from scripts import export_chatgpt_project_sources as exporter


EXPECTED_FILES = {
    "00-WFH-CHATGPT-ROUTER-v2.md",
    "01-WFH-SKILL-CATALOG-v2.md",
    "02-WFH-CAPABILITY-MAP-v2.md",
    "03-WFH-SKILL-AUDIT-SUMMARY-v2.md",
    "PROJECT-INSTRUCTIONS-v2.txt",
    "INSTALL-FA-v2.md",
    "TWFH-RESUME.md",
    "PROJECT-SOURCE-MANIFEST.json",
}


def test_export_is_lightweight_complete_and_hash_verified(monkeypatch, tmp_path: Path) -> None:
    export_root = tmp_path / "exports"
    out = export_root / "sources"
    monkeypatch.setattr(exporter, "EXPORT_ROOT", export_root)
    monkeypatch.setattr(exporter, "DEFAULT_EXPORT_DIR", out)
    monkeypatch.setattr(exporter, "_source_provenance", lambda: {
        "source_commit_sha": "a" * 40,
        "source_ref": "test",
        "source_worktree_dirty": False,
    })
    exporter.export_project_sources()

    assert {path.name for path in out.iterdir()} == EXPECTED_FILES
    assert not list(out.rglob("SKILL.md"))

    manifest = json.loads((out / "PROJECT-SOURCE-MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["contract_version"] == "wfh_chatgpt_project_sources_v2"
    assert manifest["canonical_repository"] == "cavack/WFH-ORG"
    assert manifest["canonical_skill_root"] == "skills/waterfallhunter"
    assert len(manifest["skills"]) == 14
    assert "skill-system-curator" in manifest["skills"]

    for name, expected_hash in manifest["sha256"].items():
        payload = (out / name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_hash


def test_export_is_deterministic_for_same_repository_state(monkeypatch, tmp_path: Path) -> None:
    export_root = tmp_path / "exports"
    out = export_root / "sources"
    monkeypatch.setattr(exporter, "EXPORT_ROOT", export_root)
    monkeypatch.setattr(exporter, "DEFAULT_EXPORT_DIR", out)
    monkeypatch.setattr(exporter, "_source_provenance", lambda: {
        "source_commit_sha": "a" * 40,
        "source_ref": "test",
        "source_worktree_dirty": False,
    })
    exporter.export_project_sources()
    first = {name: (out / name).read_bytes() for name in EXPECTED_FILES}
    exporter.export_project_sources()

    for name in EXPECTED_FILES:
        assert first[name] == (out / name).read_bytes()


def test_export_rejects_destination_outside_allowed_root(monkeypatch, tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    monkeypatch.setattr(exporter, "EXPORT_ROOT", allowed)
    monkeypatch.setattr(exporter, "DEFAULT_EXPORT_DIR", outside)

    try:
        exporter.export_project_sources()
    except ValueError as exc:
        assert "allowed root" in str(exc).lower()
    else:
        raise AssertionError("export must reject destinations outside the allowed root")


def test_export_rejects_symlink_target_escape(monkeypatch, tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    out = allowed / "sources"
    out.mkdir(parents=True)
    escaped = tmp_path / "escaped.md"
    (out / "00-WFH-CHATGPT-ROUTER-v2.md").symlink_to(escaped)
    monkeypatch.setattr(exporter, "EXPORT_ROOT", allowed)
    monkeypatch.setattr(exporter, "DEFAULT_EXPORT_DIR", out)

    try:
        exporter.export_project_sources()
    except ValueError as exc:
        message = str(exc).lower()
        assert "symlink" in message or "escape" in message or "allowed" in message
    else:
        raise AssertionError("export must reject symlink targets escaping the destination")


def test_cli_rejects_user_controlled_destination_argument() -> None:
    try:
        exporter.main(["../../escape"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("CLI must not accept a user-controlled filesystem destination")


def test_export_api_does_not_accept_destination_paths() -> None:
    signature = inspect.signature(exporter.export_project_sources)
    assert list(signature.parameters) == []


def test_export_fails_closed_on_unexpected_stale_content(monkeypatch, tmp_path: Path) -> None:
    export_root = tmp_path / "exports"
    out = export_root / "sources"
    out.mkdir(parents=True)
    (out / "obsolete-v1.md").write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(exporter, "EXPORT_ROOT", export_root)
    monkeypatch.setattr(exporter, "DEFAULT_EXPORT_DIR", out)
    try:
        exporter.export_project_sources()
    except ValueError as exc:
        assert "unexpected" in str(exc).lower() or "stale" in str(exc).lower()
    else:
        raise AssertionError("export must fail closed when stale files are present")


def test_export_manifest_records_source_git_provenance(monkeypatch, tmp_path: Path) -> None:
    export_root = tmp_path / "exports"
    out = export_root / "sources"
    monkeypatch.setattr(exporter, "EXPORT_ROOT", export_root)
    monkeypatch.setattr(exporter, "DEFAULT_EXPORT_DIR", out)
    monkeypatch.setattr(
        exporter,
        "_source_provenance",
        lambda: {
            "source_commit_sha": "a" * 40,
            "source_ref": "feat/test",
            "source_worktree_dirty": False,
        },
        raising=False,
    )
    manifest_path = exporter.export_project_sources()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_commit_sha"] == "a" * 40
    assert manifest["source_ref"] == "feat/test"
    assert manifest["source_worktree_dirty"] is False


def test_checked_in_manifest_hashes_match_generated_overlay(monkeypatch, tmp_path: Path) -> None:
    export_root = tmp_path / "exports"
    out = export_root / "sources"
    monkeypatch.setattr(exporter, "EXPORT_ROOT", export_root)
    monkeypatch.setattr(exporter, "DEFAULT_EXPORT_DIR", out)
    monkeypatch.setattr(
        exporter,
        "_source_provenance",
        lambda: {
            "source_commit_sha": "b" * 40,
            "source_ref": "test",
            "source_worktree_dirty": False,
        },
        raising=False,
    )
    generated = json.loads(exporter.export_project_sources().read_text(encoding="utf-8"))
    tracked = json.loads(
        (exporter.SOURCE_DIR / "PROJECT-SOURCE-MANIFEST.json").read_text(encoding="utf-8")
    )
    for key in (
        "contract_version",
        "canonical_repository",
        "canonical_ref_policy",
        "canonical_skill_root",
        "council_contract",
        "skills",
        "overlay_files",
        "sha256",
    ):
        assert tracked[key] == generated[key]


def test_project_sources_freeze_one_canonical_sha_per_audit() -> None:
    router = (exporter.SOURCE_DIR / "00-WFH-CHATGPT-ROUTER-v2.md").read_text(encoding="utf-8")
    install = (exporter.SOURCE_DIR / "INSTALL-FA-v2.md").read_text(encoding="utf-8")
    assert "source_commit_sha" in router
    assert "source_worktree_dirty" in router
    assert "source_commit_sha" in install
    assert "source_worktree_dirty" in install
    assert "همان SHA" in install or "same SHA" in install


def test_v2_spec_names_the_exact_export_artifacts() -> None:
    spec = (exporter.REPO_ROOT / "docs/superpowers/specs/2026-09-02-senior-agent-council-v2.md").read_text(encoding="utf-8")
    for name in EXPECTED_FILES:
        assert name in spec


def test_install_guide_names_all_bundle_files() -> None:
    install = (exporter.SOURCE_DIR / "INSTALL-FA-v2.md").read_text(encoding="utf-8")
    for name in EXPECTED_FILES:
        assert name in install


def test_export_rejects_in_root_symlink_alias(monkeypatch, tmp_path: Path) -> None:
    export_root = tmp_path / "exports"
    out = export_root / "sources"
    out.mkdir(parents=True)
    catalog = out / "01-WFH-SKILL-CATALOG-v2.md"
    catalog.write_text("sentinel\n", encoding="utf-8")
    (out / "00-WFH-CHATGPT-ROUTER-v2.md").symlink_to(catalog.name)
    monkeypatch.setattr(exporter, "EXPORT_ROOT", export_root)
    monkeypatch.setattr(exporter, "DEFAULT_EXPORT_DIR", out)
    try:
        exporter.export_project_sources()
    except ValueError as exc:
        assert "symlink" in str(exc).lower()
    else:
        raise AssertionError("export must reject existing symlink aliases inside the destination")
    assert catalog.read_text(encoding="utf-8") == "sentinel\n"


def test_install_guide_documents_cold_resume_phrase() -> None:
    install = (exporter.SOURCE_DIR / "INSTALL-FA-v2.md").read_text(encoding="utf-8")
    assert "ادامه کار گروهی" in install
    assert "TWFH-RESUME.md" in install
    assert "RESUME_READY" in install
    assert "cold" in install.lower() or "چت جدید" in install


def test_exporter_uses_exact_static_overlay_allowlist() -> None:
    assert exporter.OVERLAY_FILES == (
        exporter.ROUTER_FILE,
        exporter.CATALOG_FILE,
        exporter.CAPABILITY_FILE,
        exporter.AUDIT_FILE,
        exporter.INSTRUCTIONS_FILE,
        exporter.INSTALL_FILE,
        exporter.RESUME_FILE,
    )
    assert exporter.RESUME_FILE == "TWFH-RESUME.md"


def test_exporter_does_not_route_file_destinations_through_generic_path_helper(
    monkeypatch, tmp_path: Path
) -> None:
    export_root = tmp_path / "exports"
    out = export_root / "sources"
    monkeypatch.setattr(exporter, "EXPORT_ROOT", export_root)
    monkeypatch.setattr(exporter, "DEFAULT_EXPORT_DIR", out)
    monkeypatch.setattr(
        exporter,
        "_source_provenance",
        lambda: {
            "source_commit_sha": "b" * 40,
            "source_ref": "test",
            "source_worktree_dirty": False,
        },
    )
    original = exporter._confined_path

    def guarded(path: Path, allowed_root: Path, *, label: str, strict: bool = False) -> Path:
        assert label != "export target"
        return original(path, allowed_root, label=label, strict=strict)

    monkeypatch.setattr(exporter, "_confined_path", guarded)
    manifest = exporter.export_project_sources()

    assert manifest == out / "PROJECT-SOURCE-MANIFEST.json"
    assert {path.name for path in out.iterdir()} == exporter.EXPECTED_EXPORT_FILES


def _mock_export_provenance(monkeypatch) -> None:
    monkeypatch.setattr(
        exporter,
        "_source_provenance",
        lambda: {
            "source_commit_sha": "b" * 40,
            "source_ref": "test",
            "source_worktree_dirty": False,
        },
    )


def test_exporter_fails_closed_without_o_nofollow(monkeypatch, tmp_path: Path) -> None:
    export_root = tmp_path / "exports"
    out = export_root / "sources"
    monkeypatch.setattr(exporter, "EXPORT_ROOT", export_root)
    monkeypatch.setattr(exporter, "DEFAULT_EXPORT_DIR", out)
    _mock_export_provenance(monkeypatch)
    monkeypatch.delattr(exporter.os, "O_NOFOLLOW", raising=False)

    with pytest.raises(RuntimeError, match="O_NOFOLLOW"):
        exporter.export_project_sources()


def test_exporter_opens_export_directory_with_o_nofollow(monkeypatch, tmp_path: Path) -> None:
    export_root = tmp_path / "exports"
    out = export_root / "sources"
    monkeypatch.setattr(exporter, "EXPORT_ROOT", export_root)
    monkeypatch.setattr(exporter, "DEFAULT_EXPORT_DIR", out)
    _mock_export_provenance(monkeypatch)
    original_open = exporter.os.open
    directory_flags: list[int] = []

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        if flags & exporter.os.O_DIRECTORY:
            directory_flags.append(flags)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(exporter.os, "open", tracked_open)
    exporter.export_project_sources()

    assert directory_flags
    assert all(flags & exporter.os.O_NOFOLLOW for flags in directory_flags)


def test_exporter_rejects_symlinked_export_root_component(monkeypatch, tmp_path: Path) -> None:
    trusted_parent = tmp_path / "repo"
    trusted_parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    export_root = trusted_parent / ".work"
    export_root.symlink_to(outside, target_is_directory=True)
    out = export_root / "chatgpt-project-sources-v2"
    monkeypatch.setattr(exporter, "EXPORT_ROOT", export_root)
    monkeypatch.setattr(exporter, "DEFAULT_EXPORT_DIR", out)
    _mock_export_provenance(monkeypatch)

    with pytest.raises((OSError, RuntimeError, ValueError)):
        exporter.export_project_sources()

    assert not (outside / "chatgpt-project-sources-v2").exists()
