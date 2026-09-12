from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "scripts/verify_ci_image_bundle.py"


def _config(revision: str) -> bytes:
    """Build a minimal OCI config carrying the requested revision label."""
    return json.dumps(
        {"config": {"Labels": {"org.opencontainers.image.revision": revision}}},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _bundle(path: Path, revision: str) -> tuple[str, str]:
    """Create a minimal docker-save style bundle for verifier tests."""
    config = _config(revision)
    digest = hashlib.sha256(config).hexdigest()
    config_name = f"blobs/sha256/{digest}"
    manifest = [{"Config": config_name, "RepoTags": ["waterfallhunter-waterfall-backend:latest"], "Layers": []}]
    with tarfile.open(path, "w") as archive:
        info = tarfile.TarInfo(config_name)
        info.size = len(config)
        archive.addfile(info, io.BytesIO(config))
        payload = json.dumps(manifest, separators=(",", ":")).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return "waterfallhunter-waterfall-backend", f"sha256:{digest}"


def _run(bundle: Path, revision: str, image: str, digest: str) -> subprocess.CompletedProcess[str]:
    """Run the verifier with the bundle parent as the authorized root."""
    return subprocess.run(
        [
            sys.executable, str(VERIFIER),
            "--allowed-root", str(bundle.parent),
            "--bundle", str(bundle),
            "--revision", revision,
            "--image", f"{image}={digest}",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_bundle_verifier_accepts_portable_config_digest_and_revision(tmp_path: Path) -> None:
    """Accept a bundle whose config digest and revision match CI evidence."""
    revision = "a" * 40
    bundle = tmp_path / "images.tar"
    image, digest = _bundle(bundle, revision)
    result = _run(bundle, revision, image, digest)
    assert result.returncode == 0, result.stderr + result.stdout


def test_bundle_verifier_rejects_digest_or_revision_mismatch(tmp_path: Path) -> None:
    """Reject digest and revision evidence that does not match the bundle."""
    revision = "a" * 40
    bundle = tmp_path / "images.tar"
    image, digest = _bundle(bundle, revision)
    assert _run(bundle, revision, image, "sha256:" + "b" * 64).returncode != 0
    assert _run(bundle, "c" * 40, image, digest).returncode != 0


def test_bundle_verifier_rejects_bundle_outside_allowed_root(tmp_path: Path) -> None:
    """Reject CLI-controlled bundle paths that escape the authorized directory."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    bundle = tmp_path / "outside.tar"
    revision = "a" * 40
    image, digest = _bundle(bundle, revision)
    result = subprocess.run(
        [
            sys.executable, str(VERIFIER),
            "--allowed-root", str(allowed),
            "--bundle", str(bundle),
            "--revision", revision,
            "--image", f"{image}={digest}",
        ],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert "outside allowed root" in result.stdout


def test_bundle_verifier_rejects_non_object_nested_config_without_traceback(tmp_path: Path) -> None:
    """Return the stable failure status for malformed nested config objects."""
    revision = "a" * 40
    bundle = tmp_path / "malformed.tar"
    config = json.dumps({"config": "invalid"}, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(config).hexdigest()
    config_name = f"blobs/sha256/{digest}"
    manifest = [{"Config": config_name, "RepoTags": ["waterfallhunter-waterfall-backend:latest"], "Layers": []}]
    with tarfile.open(bundle, "w") as archive:
        info = tarfile.TarInfo(config_name)
        info.size = len(config)
        archive.addfile(info, io.BytesIO(config))
        payload = json.dumps(manifest, separators=(",", ":")).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    result = _run(bundle, revision, "waterfallhunter-waterfall-backend", f"sha256:{digest}")
    assert result.returncode == 2
    assert "bundle_verification=FAIL" in result.stdout
    assert "Traceback" not in result.stderr
