"""Streaming compression/encryption bundle for off-host SQLite backups."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import zlib
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.exceptions import InvalidTag


class RemoteBackupBundleError(RuntimeError):
    """Raised when a backup bundle cannot be produced or restored safely."""


_BUFFER_BYTES = 4 * 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_BUFFER_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _ChunkWriter:
    def __init__(self, directory: Path, prefix: str, max_chunk_bytes: int) -> None:
        self.directory = directory
        self.prefix = prefix
        self.max_chunk_bytes = max_chunk_bytes
        self.index = 0
        self.handle = None
        self.current_path: Path | None = None
        self.current_size = 0
        self.current_hash = hashlib.sha256()
        self.chunks: list[dict[str, Any]] = []

    def _open(self) -> None:
        name = f"{self.prefix}.part-{self.index:03d}.enc"
        path = self.directory / name
        if path.exists() or path.is_symlink():
            raise RemoteBackupBundleError("BUNDLE_CHUNK_TARGET_EXISTS")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        self.handle = os.fdopen(fd, "wb")
        self.current_path = path
        self.current_size = 0
        self.current_hash = hashlib.sha256()

    def _close(self) -> None:
        if self.handle is None or self.current_path is None:
            return
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        self.chunks.append({
            "name": self.current_path.name,
            "index": self.index,
            "size_bytes": self.current_size,
            "sha256": self.current_hash.hexdigest(),
        })
        self.index += 1
        self.handle = None
        self.current_path = None
        self.current_size = 0

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            if self.handle is None:
                self._open()
            remaining = self.max_chunk_bytes - self.current_size
            piece = view[:remaining]
            assert self.handle is not None
            self.handle.write(piece)
            self.current_hash.update(piece)
            self.current_size += len(piece)
            view = view[len(piece):]
            if self.current_size == self.max_chunk_bytes:
                self._close()

    def finish(self) -> list[dict[str, Any]]:
        self._close()
        if not self.chunks:
            raise RemoteBackupBundleError("BUNDLE_EMPTY")
        directory_fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return self.chunks


def _validate_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != 32:
        raise RemoteBackupBundleError("BUNDLE_KEY_INVALID")

def encrypt_sqlite_backup_bundle(
    *,
    source: Path,
    output_dir: Path,
    prefix: str,
    key: bytes,
    max_chunk_bytes: int = 1_500_000_000,
) -> dict[str, Any]:
    _validate_key(key)
    if not source.is_absolute() or source.is_symlink() or not source.is_file():
        raise RemoteBackupBundleError("BUNDLE_SOURCE_INVALID")
    if not output_dir.is_absolute() or output_dir.is_symlink() or not output_dir.is_dir():
        raise RemoteBackupBundleError("BUNDLE_OUTPUT_DIR_INVALID")
    if not prefix or "/" in prefix or "\\" in prefix:
        raise RemoteBackupBundleError("BUNDLE_PREFIX_INVALID")
    if not isinstance(max_chunk_bytes, int) or max_chunk_bytes < 256:
        raise RemoteBackupBundleError("BUNDLE_CHUNK_SIZE_INVALID")

    manifest_path = output_dir / f"{prefix}.manifest.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        raise RemoteBackupBundleError("BUNDLE_MANIFEST_TARGET_EXISTS")

    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    compressor = zlib.compressobj(level=6)
    writer = _ChunkWriter(output_dir, prefix, max_chunk_bytes)
    plaintext_hash = hashlib.sha256()
    plaintext_size = 0
    ciphertext_hash = hashlib.sha256()

    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(_BUFFER_BYTES), b""):
            plaintext_hash.update(block)
            plaintext_size += len(block)
            compressed = compressor.compress(block)
            if compressed:
                encrypted = encryptor.update(compressed)
                ciphertext_hash.update(encrypted)
                writer.write(encrypted)
    tail = compressor.flush()
    if tail:
        encrypted = encryptor.update(tail)
        ciphertext_hash.update(encrypted)
        writer.write(encrypted)
    final = encryptor.finalize()
    if final:
        ciphertext_hash.update(final)
        writer.write(final)
    chunks = writer.finish()

    manifest = {
        "contract_version": "wfh_encrypted_backup_bundle_v1",
        "algorithm": "AES-256-GCM",
        "compression": "zlib",
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "tag_b64": base64.b64encode(encryptor.tag).decode("ascii"),
        "plaintext_size_bytes": plaintext_size,
        "plaintext_sha256": plaintext_hash.hexdigest(),
        "ciphertext_sha256": ciphertext_hash.hexdigest(),
        "max_chunk_bytes": max_chunk_bytes,
        "chunks": chunks,
    }
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    fd = os.open(manifest_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return manifest


def _decompress_checked(decompressor: zlib.decompressobj, data: bytes) -> bytes:
    try:
        return decompressor.decompress(data)
    except zlib.error as error:
        raise RemoteBackupBundleError("BUNDLE_DECOMPRESSION_FAILED") from error


def _flush_decompressor_checked(decompressor: zlib.decompressobj) -> bytes:
    try:
        return decompressor.flush()
    except zlib.error as error:
        raise RemoteBackupBundleError("BUNDLE_DECOMPRESSION_FAILED") from error


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise RemoteBackupBundleError("BUNDLE_MANIFEST_INVALID")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as error:
        raise RemoteBackupBundleError("BUNDLE_MANIFEST_INVALID") from error
    if not isinstance(value, dict):
        raise RemoteBackupBundleError("BUNDLE_MANIFEST_INVALID")
    if (
        value.get("contract_version") != "wfh_encrypted_backup_bundle_v1"
        or value.get("algorithm") != "AES-256-GCM"
        or value.get("compression") != "zlib"
        or not isinstance(value.get("chunks"), list)
        or not value["chunks"]
    ):
        raise RemoteBackupBundleError("BUNDLE_MANIFEST_INVALID")
    return value


def _crypto_metadata(manifest: dict[str, Any]) -> tuple[bytes, bytes]:
    try:
        nonce = base64.b64decode(manifest["nonce_b64"], validate=True)
        tag = base64.b64decode(manifest["tag_b64"], validate=True)
    except (KeyError, ValueError) as error:
        raise RemoteBackupBundleError("BUNDLE_CRYPTO_METADATA_INVALID") from error
    if len(nonce) != 12 or len(tag) != 16:
        raise RemoteBackupBundleError("BUNDLE_CRYPTO_METADATA_INVALID")
    return nonce, tag


def _validated_chunk_path(
    *, bundle_dir: Path, item: Any, expected_index: int
) -> Path:
    if not isinstance(item, dict) or item.get("index") != expected_index:
        raise RemoteBackupBundleError("BUNDLE_CHUNK_ORDER_INVALID")
    name = item.get("name")
    if not isinstance(name, str) or Path(name).name != name:
        raise RemoteBackupBundleError("BUNDLE_CHUNK_INVALID")
    chunk_path = bundle_dir / name
    if chunk_path.is_symlink() or not chunk_path.is_file():
        raise RemoteBackupBundleError("BUNDLE_CHUNK_MISSING")
    if (
        chunk_path.stat().st_size != item.get("size_bytes")
        or _sha256_file(chunk_path) != item.get("sha256")
    ):
        raise RemoteBackupBundleError("BUNDLE_CHUNK_MISMATCH")
    return chunk_path


def _write_plaintext(
    *, output: Any, data: bytes, plaintext_hash: Any, plaintext_size: int
) -> int:
    if not data:
        return plaintext_size
    output.write(data)
    plaintext_hash.update(data)
    return plaintext_size + len(data)


def _restore_encrypted_chunks(
    *,
    manifest: dict[str, Any],
    bundle_dir: Path,
    output: Any,
    decryptor: Any,
    decompressor: Any,
    plaintext_hash: Any,
    ciphertext_hash: Any,
) -> int:
    plaintext_size = 0
    for expected_index, item in enumerate(manifest["chunks"]):
        chunk_path = _validated_chunk_path(
            bundle_dir=bundle_dir, item=item, expected_index=expected_index
        )
        with chunk_path.open("rb") as source:
            for block in iter(lambda: source.read(_BUFFER_BYTES), b""):
                ciphertext_hash.update(block)
                decrypted = decryptor.update(block)
                plaintext_size = _write_plaintext(
                    output=output,
                    data=_decompress_checked(decompressor, decrypted) if decrypted else b"",
                    plaintext_hash=plaintext_hash,
                    plaintext_size=plaintext_size,
                )
    return plaintext_size


def _finalize_restore_stream(
    *, output: Any, decryptor: Any, decompressor: Any, plaintext_hash: Any, plaintext_size: int
) -> int:
    try:
        final = decryptor.finalize()
    except InvalidTag as error:
        raise RemoteBackupBundleError("BUNDLE_AUTHENTICATION_FAILED") from error
    if final:
        plaintext_size = _write_plaintext(
            output=output,
            data=_decompress_checked(decompressor, final),
            plaintext_hash=plaintext_hash,
            plaintext_size=plaintext_size,
        )
    return _write_plaintext(
        output=output,
        data=_flush_decompressor_checked(decompressor),
        plaintext_hash=plaintext_hash,
        plaintext_size=plaintext_size,
    )


def _verify_restored_stream(
    *, manifest: dict[str, Any], plaintext_hash: Any, plaintext_size: int, ciphertext_hash: Any
) -> None:
    if ciphertext_hash.hexdigest() != manifest.get("ciphertext_sha256"):
        raise RemoteBackupBundleError("BUNDLE_CIPHERTEXT_DIGEST_MISMATCH")
    if plaintext_hash.hexdigest() != manifest.get("plaintext_sha256"):
        raise RemoteBackupBundleError("BUNDLE_PLAINTEXT_DIGEST_MISMATCH")
    if plaintext_size != manifest.get("plaintext_size_bytes"):
        raise RemoteBackupBundleError("BUNDLE_PLAINTEXT_SIZE_MISMATCH")


def _publish_restored_file(*, partial: Path, target: Path) -> None:
    os.link(partial, target)
    partial.unlink()
    directory_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def restore_sqlite_backup_bundle(
    *,
    manifest_path: Path,
    bundle_dir: Path,
    target: Path,
    key: bytes,
) -> dict[str, Any]:
    """Restore and authenticate one encrypted SQLite backup bundle."""
    _validate_key(key)
    if not bundle_dir.is_absolute() or bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise RemoteBackupBundleError("BUNDLE_DIR_INVALID")
    if not target.is_absolute() or target.is_symlink() or target.exists():
        raise RemoteBackupBundleError("BUNDLE_RESTORE_TARGET_INVALID")
    if target.parent.is_symlink() or not target.parent.is_dir():
        raise RemoteBackupBundleError("BUNDLE_RESTORE_PARENT_INVALID")

    manifest = _load_manifest(manifest_path)
    nonce, tag = _crypto_metadata(manifest)
    decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    decompressor = zlib.decompressobj()
    plaintext_hash = hashlib.sha256()
    ciphertext_hash = hashlib.sha256()
    partial = target.with_name(f".{target.name}.partial")
    if partial.exists() or partial.is_symlink():
        raise RemoteBackupBundleError("BUNDLE_RESTORE_PARTIAL_EXISTS")

    fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            plaintext_size = _restore_encrypted_chunks(
                manifest=manifest,
                bundle_dir=bundle_dir,
                output=output,
                decryptor=decryptor,
                decompressor=decompressor,
                plaintext_hash=plaintext_hash,
                ciphertext_hash=ciphertext_hash,
            )
            plaintext_size = _finalize_restore_stream(
                output=output,
                decryptor=decryptor,
                decompressor=decompressor,
                plaintext_hash=plaintext_hash,
                plaintext_size=plaintext_size,
            )
            output.flush()
            os.fsync(output.fileno())
        _verify_restored_stream(
            manifest=manifest,
            plaintext_hash=plaintext_hash,
            plaintext_size=plaintext_size,
            ciphertext_hash=ciphertext_hash,
        )
        _publish_restored_file(partial=partial, target=target)
        return {
            "plaintext_sha256": plaintext_hash.hexdigest(),
            "plaintext_size_bytes": plaintext_size,
            "target": str(target),
        }
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
