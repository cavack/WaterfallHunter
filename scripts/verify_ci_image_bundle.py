#!/usr/bin/env python3
"""Verify portable config digests and revision labels in a docker-save bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path
from typing import Any

DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


def _parse_image(value: str) -> tuple[str, str]:
    """Parse a NAME=sha256:<digest> CLI argument."""
    name, separator, digest = value.partition("=")
    if not separator or not name or DIGEST_RE.fullmatch(digest) is None:
        raise argparse.ArgumentTypeError("image must be NAME=sha256:<64 lowercase hex>")
    return name, digest


def _validated_bundle_path(bundle: Path, allowed_root: Path) -> Path:
    """Resolve a regular bundle file and prove it stays inside the allowed root."""
    try:
        root = allowed_root.resolve(strict=True)
        candidate = bundle.resolve(strict=True)
    except OSError as error:
        raise ValueError("bundle or allowed root is missing") from error
    if not root.is_dir():
        raise ValueError("allowed root is not a directory")
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("bundle is outside allowed root") from error
    if bundle.is_symlink() or not candidate.is_file():
        raise ValueError("bundle is missing, symlinked, or not a regular file")
    return candidate


def _load_manifest(archive: tarfile.TarFile) -> list[dict[str, Any]]:
    """Load and type-check docker-save manifest.json without extracting files."""
    manifest_file = archive.extractfile("manifest.json")
    if manifest_file is None:
        raise ValueError("bundle manifest.json missing")
    manifest = json.load(manifest_file)
    if not isinstance(manifest, list) or any(not isinstance(item, dict) for item in manifest):
        raise ValueError("bundle manifest is invalid")
    return manifest


def _index_manifest(manifest: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a unique repository-tag index for manifest entries."""
    by_tag: dict[str, dict[str, Any]] = {}
    for item in manifest:
        tags = item.get("RepoTags") or []
        if not isinstance(tags, list):
            raise ValueError("bundle repository tags are invalid")
        for tag in tags:
            if not isinstance(tag, str) or tag in by_tag:
                raise ValueError("bundle repository tags are invalid or duplicated")
            by_tag[tag] = item
    return by_tag


def _normalized_tag(image_name: str) -> str:
    """Normalize an image reference to the latest tag used by docker save."""
    return image_name if ":" in image_name.rsplit("/", 1)[-1] else f"{image_name}:latest"


def _expected_digest_hex(expected_digest: str) -> str:
    """Return the validated lowercase hexadecimal digest payload."""
    match = DIGEST_RE.fullmatch(expected_digest)
    if match is None:
        raise ValueError("tested image digest invalid")
    return match.group(1)


def _load_config_bytes(archive: tarfile.TarFile, config_name: str, tag: str) -> bytes:
    """Read a config member from the archive without filesystem extraction."""
    config_file = archive.extractfile(config_name)
    if config_file is None:
        raise ValueError(f"bundle config missing: {tag}")
    return config_file.read()


def _verify_image(
    archive: tarfile.TarFile,
    by_tag: dict[str, dict[str, Any]],
    image_name: str,
    expected_digest: str,
    revision: str,
) -> None:
    """Verify one image config digest, content hash, and OCI revision label."""
    tag = _normalized_tag(image_name)
    item = by_tag.get(tag)
    if item is None:
        raise ValueError(f"bundle image missing: {tag}")
    expected_hex = _expected_digest_hex(expected_digest)
    config_name = str(item.get("Config") or "")
    allowed_names = {f"blobs/sha256/{expected_hex}", f"{expected_hex}.json"}
    if config_name not in allowed_names:
        raise ValueError(f"bundle config digest mismatch: {tag}")
    config_bytes = _load_config_bytes(archive, config_name, tag)
    if hashlib.sha256(config_bytes).hexdigest() != expected_hex:
        raise ValueError(f"bundle config content hash mismatch: {tag}")
    config = json.loads(config_bytes)
    if not isinstance(config, dict):
        raise ValueError(f"bundle config is invalid: {tag}")
    config_section = config.get("config") or {}
    if not isinstance(config_section, dict):
        raise ValueError(f"bundle config is invalid: {tag}")
    labels = config_section.get("Labels") or {}
    if not isinstance(labels, dict) or labels.get("org.opencontainers.image.revision") != revision:
        raise ValueError(f"bundle revision mismatch: {tag}")


def verify_bundle(
    bundle: Path,
    allowed_root: Path,
    revision: str,
    expected: list[tuple[str, str]],
) -> None:
    """Verify all expected images in an authorized docker-save bundle."""
    if REVISION_RE.fullmatch(revision) is None:
        raise ValueError("revision must be a 40-character lowercase Git SHA")
    bundle_path = _validated_bundle_path(bundle, allowed_root)
    with tarfile.open(bundle_path, "r") as archive:
        by_tag = _index_manifest(_load_manifest(archive))
        for image_name, expected_digest in expected:
            _verify_image(archive, by_tag, image_name, expected_digest, revision)


def main() -> int:
    """Run the command-line verifier and return a stable process exit code."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--allowed-root", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--image", action="append", required=True, type=_parse_image)
    args = parser.parse_args()
    try:
        verify_bundle(args.bundle, args.allowed_root, args.revision, args.image)
    except (OSError, tarfile.TarError, ValueError) as error:
        print(f"bundle_verification=FAIL reason={error}")
        return 2
    print(f"bundle_verification=PASS images={len(args.image)} revision={args.revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
