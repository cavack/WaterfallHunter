from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "scripts/install_wfh_council_hooks.sh"
PRE_COMMIT = REPO / ".githooks/pre-commit"
PRE_PUSH = REPO / ".githooks/pre-push"


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".githooks").mkdir()
    (repo / "scripts").mkdir()
    shutil.copy2(PRE_COMMIT, repo / ".githooks/pre-commit")
    shutil.copy2(PRE_PUSH, repo / ".githooks/pre-push")
    shutil.copy2(INSTALLER, repo / "scripts/install_wfh_council_hooks.sh")
    return repo


@pytest.mark.skipif(shutil.which("git") is None, reason="git hook integration requires git")
def test_installer_sets_repo_local_hooks_path_and_is_idempotent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    installer = repo / "scripts/install_wfh_council_hooks.sh"

    subprocess.run(["bash", str(installer)], cwd=repo, check=True)
    assert _git(repo, "config", "--worktree", "--get", "core.hooksPath") == ".githooks"

    subprocess.run(["bash", str(installer)], cwd=repo, check=True)
    assert _git(repo, "config", "--worktree", "--get", "core.hooksPath") == ".githooks"


def test_hooks_and_installer_are_executable() -> None:
    for path in (INSTALLER, PRE_COMMIT, PRE_PUSH):
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"{path} must be executable"


def test_hooks_are_validation_only_and_never_deploy() -> None:
    forbidden = ("deploy_production", "LIVE_TRADING_ENABLED=true", "docker compose up", "workflow_dispatch")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in (PRE_COMMIT, PRE_PUSH, INSTALLER))
    for marker in forbidden:
        assert marker not in combined


@pytest.mark.skipif(shutil.which("git") is None, reason="git worktree integration requires git")
def test_linked_worktree_install_does_not_set_shared_hooks_path(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "council@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Council Test"], cwd=root, check=True)
    (root / "seed").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)

    linked = tmp_path / "linked"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "council-test", str(linked)], cwd=root, check=True)
    (linked / ".githooks").mkdir()
    (linked / "scripts").mkdir()
    for source, dest in (
        (PRE_COMMIT, linked / ".githooks/pre-commit"),
        (PRE_PUSH, linked / ".githooks/pre-push"),
        (INSTALLER, linked / "scripts/install_wfh_council_hooks.sh"),
    ):
        shutil.copy2(source, dest)

    subprocess.run(["bash", str(linked / "scripts/install_wfh_council_hooks.sh")], cwd=linked, check=True)
    assert _git(linked, "config", "--worktree", "--get", "core.hooksPath") == ".githooks"
    shared = subprocess.run(
        ["git", "config", "--local", "--get", "core.hooksPath"], cwd=root, text=True, capture_output=True
    )
    assert shared.returncode == 1


def _write_fake_validators(repo: Path) -> None:
    scripts = repo / "scripts"
    scripts.mkdir(exist_ok=True)
    validator = (
        "from pathlib import Path\n"
        "import sys\n"
        "sys.exit(0 if Path('marker.txt').read_text().strip() == 'GOOD' else 9)\n"
    )
    (scripts / "wfh_council.py").write_text(validator, encoding="utf-8")
    (scripts / "validate_wfh_skills.py").write_text(validator, encoding="utf-8")
    hygiene = (
        "from pathlib import Path\n"
        "import sys\n"
        "manifest = Path('.wfh-source-manifest')\n"
        "ok = manifest.is_file() and 'marker.txt' in manifest.read_text().splitlines()\n"
        "sys.exit(0 if ok else 8)\n"
    )
    (scripts / "verify_repository_hygiene.py").write_text(hygiene, encoding="utf-8")
    tests = repo / "backend/tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_wfh_council.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (tests / "test_wfh_council_hooks.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (tests / "test_wfh_skill_system_v2.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (tests / "test_chatgpt_project_export.py").write_text("def test_ok(): assert True\n", encoding="utf-8")


def _configure_identity(repo: Path) -> None:
    subprocess.run(["git", "config", "user.email", "council@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Council Test"], cwd=repo, check=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git staged-index integration requires git")
def test_pre_commit_validates_staged_index_not_mutable_worktree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _configure_identity(repo)
    _write_fake_validators(repo)
    marker = repo / "marker.txt"
    marker.write_text("GOOD\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    marker.write_text("BAD\n", encoding="utf-8")

    result = subprocess.run(["bash", str(repo / ".githooks/pre-commit")], cwd=repo)

    assert result.returncode == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git staged-index integration requires git")
def test_pre_commit_rejects_invalid_staged_index_even_if_worktree_is_fixed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _configure_identity(repo)
    _write_fake_validators(repo)
    marker = repo / "marker.txt"
    marker.write_text("BAD\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    marker.write_text("GOOD\n", encoding="utf-8")

    result = subprocess.run(["bash", str(repo / ".githooks/pre-commit")], cwd=repo)

    assert result.returncode != 0


ZERO_SHA = "0" * 40


def _python3_shim(directory: Path) -> Path:
    """Expose the interpreter running the suite as ``python3`` for hook runs."""
    shim = directory / "python3"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    return shim


def _interpreter_env(root: Path) -> dict[str, str]:
    bin_dir = root / "interpreter"
    bin_dir.mkdir()
    _python3_shim(bin_dir)
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
    return env


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.skipif(shutil.which("git") is None, reason="git pre-push integration requires git")
def test_pre_push_validates_exact_local_sha_not_mutable_worktree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _configure_identity(repo)
    _write_fake_validators(repo)
    marker = repo / "marker.txt"
    marker.write_text("GOOD\n", encoding="utf-8")
    local_sha = _commit_all(repo, "good")
    marker.write_text("BAD\n", encoding="utf-8")
    hook_input = f"refs/heads/main {local_sha} refs/heads/main {ZERO_SHA}\n"

    result = subprocess.run(
        ["bash", str(repo / ".githooks/pre-push"), "origin", "example.invalid/repo"],
        cwd=repo, input=hook_input, text=True, env=_interpreter_env(tmp_path),
    )

    assert result.returncode == 0


def _declared_external_allowlist(path: Path) -> set[str]:
    prefix = "# WFH_EXTERNAL_COMMAND_ALLOWLIST:"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return set(line.removeprefix(prefix).split())
    raise AssertionError(f"{path} must declare its external command allowlist")


def test_hook_sources_declare_closed_external_command_allowlists() -> None:
    expected = {
        PRE_COMMIT: {"git", "mktemp", "rm", "python3"},
        PRE_PUSH: {"git", "mktemp", "rm", "mkdir", "tar", "python3"},
        INSTALLER: {"git", "chmod"},
    }
    for path, allowed in expected.items():
        assert _declared_external_allowlist(path) == allowed
        text = path.read_text(encoding="utf-8")
        assert "eval " not in text
        assert "sh -c" not in text
        assert "bash -c" not in text
        assert "source " not in text
        assert "/usr/bin/" not in text
        assert "/bin/" not in "\n".join(text.splitlines()[1:])


def _closed_allowlist_env(root: Path, source: Path) -> dict[str, str]:
    bin_dir = root / f"allow-{source.name}"
    bin_dir.mkdir()
    for command in _declared_external_allowlist(source):
        if command == "python3":
            _python3_shim(bin_dir)
            continue
        target = shutil.which(command)
        assert target is not None, f"required test command unavailable: {command}"
        os.symlink(target, bin_dir / command)
    env = os.environ.copy()
    env["PATH"] = str(bin_dir)
    return env


@pytest.mark.skipif(shutil.which("git") is None, reason="closed PATH integration requires git")
def test_declared_allowlists_are_sufficient_for_real_hook_execution(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _configure_identity(repo)
    _write_fake_validators(repo)
    (repo / "marker.txt").write_text("GOOD\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)

    commit_env = _closed_allowlist_env(tmp_path, PRE_COMMIT)
    result = subprocess.run(["/bin/sh", str(repo / ".githooks/pre-commit")], cwd=repo, env=commit_env)
    assert result.returncode == 0

    local_sha = _commit_all(repo, "good")
    push_env = _closed_allowlist_env(tmp_path, PRE_PUSH)
    hook_input = f"refs/heads/main {local_sha} refs/heads/main {ZERO_SHA}\n"
    result = subprocess.run(["/bin/sh", str(repo / ".githooks/pre-push"), "origin", "unused"], cwd=repo, input=hook_input, text=True, env=push_env)
    assert result.returncode == 0


def test_pre_push_runs_v2_skill_system_regression() -> None:
    text = PRE_PUSH.read_text(encoding="utf-8")
    assert "backend/tests/test_wfh_skill_system_v2.py" in text
    assert "backend/tests/test_chatgpt_project_export.py" in text
