# Wave 0 Foundation Contracts

Verified on 2026-08-19 against RFC 8785 and the current canonical repository.

## Canonical hashing

All provenance and Golden Corpus hashes use RFC 8785 JSON Canonicalization Scheme bytes and SHA-256. NaN, infinity, non-string object keys, unordered sets, and unsupported values are rejected. Volatile-field exclusions are explicit and versioned.

Reference: <https://www.rfc-editor.org/rfc/rfc8785.html>

## Runtime identity

`RuntimeFingerprint` and Git revision are separate identities. A legacy runtime is permanently labelled `LEGACY_RUNTIME_UNVERIFIED_REVISION`; it cannot carry a Git SHA. A verified source fingerprint requires an exact Git SHA. Only allowlisted non-secret configuration participates in hashing.

## Golden Corpora

- `LEGACY_RUNTIME_CORPUS` binds to a RuntimeFingerprint only.
- `CANONICAL_MAIN_REPLAY_CORPUS` binds to a Git SHA only.
- The tracked canonical-main corpus currently contains deterministic fixtures and is explicitly labelled `DETERMINISTIC_FIXTURE`. It is a semantic regression gate, not production or scientific evidence.
- The legacy-runtime evidence corpus remains unavailable without authorized Production evidence capture. Model-affecting changes remain blocked until it exists.
- Pull-request determinism requires at least three identical semantic replays.
- Final certification requires ten identical semantic replays.
- Model-affecting differences require an explicit reviewed change report; unexpected differences block promotion.

## Artifact provenance

Verified provenance requires every link:

`Git SHA → dependency lock/hash → Dockerfile hash → base digest → built digest → tested digest → deployment manifest → running digest`

Missing, invalid, or mismatched links yield `DEPLOYMENT_PROVENANCE_PARTIAL`.

`backend/requirements.lock` and `watchdog/requirements.lock` are resolved, hash-locked Python dependency inputs used by CI and their respective images. Container installs accept wheels only. Base images use digest-only references and GitHub Actions are commit-pinned. Production images carry OCI revision/source/created/version labels.

The dependency audit found vulnerable versions forced by `ccxt==4.5.68`. Wave 0 upgrades to `ccxt==4.5.74`, `aiohttp==3.14.3`, and `cryptography==50.0.0`, removes all four audit exceptions, and preserves the baseline model suite.

## Safety

These tools are source/development tooling. They do not read secrets, write Production data, send Telegram messages, place orders, migrate databases, restart services, deploy, or merge.
