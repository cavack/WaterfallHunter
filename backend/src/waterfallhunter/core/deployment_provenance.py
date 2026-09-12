"""Fail-closed artifact/deployment provenance evaluation."""

from __future__ import annotations

import re
from typing import Any, Mapping

from waterfallhunter.core.canonical_json import canonical_json_bytes
from waterfallhunter.core.runtime_fingerprint import sha256_bytes


PROVENANCE_CONTRACT_VERSION = "deployment_provenance_v1"
DEPLOYMENT_PROVENANCE_VERIFIED = "DEPLOYMENT_PROVENANCE_VERIFIED"
DEPLOYMENT_PROVENANCE_PARTIAL = "DEPLOYMENT_PROVENANCE_PARTIAL"

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")

REQUIRED_LINKS = (
    "git_sha",
    "dependency_lock_sha256",
    "dockerfile_sha256",
    "base_image_digest",
    "built_image_digest",
    "tested_image_digest",
    "deployment_manifest_sha256",
    "running_image_digest",
    "runtime_fingerprint_sha256",
)


def _invalid_links(links: Mapping[str, Any]) -> list[str]:
    validators = {
        "git_sha": _GIT_SHA,
        "dependency_lock_sha256": _HEX_SHA256,
        "dockerfile_sha256": _HEX_SHA256,
        "deployment_manifest_sha256": _HEX_SHA256,
        "runtime_fingerprint_sha256": _HEX_SHA256,
        "base_image_digest": _SHA256,
        "built_image_digest": _SHA256,
        "tested_image_digest": _SHA256,
        "running_image_digest": _SHA256,
    }
    return [
        key
        for key, validator in validators.items()
        if links[key] and not validator.fullmatch(str(links[key]))
    ]


def evaluate_deployment_provenance(values: Mapping[str, Any]) -> dict[str, Any]:
    links = {key: values.get(key) for key in REQUIRED_LINKS}
    missing = [key for key, value in links.items() if not value]
    invalid = _invalid_links(links)
    mismatches: list[str] = []
    if links["built_image_digest"] and links["tested_image_digest"] != links["built_image_digest"]:
        mismatches.append("tested_image_digest")
    if links["built_image_digest"] and links["running_image_digest"] != links["built_image_digest"]:
        mismatches.append("running_image_digest")
    status = (
        DEPLOYMENT_PROVENANCE_VERIFIED
        if not missing and not invalid and not mismatches
        else DEPLOYMENT_PROVENANCE_PARTIAL
    )
    payload = {
        "contract_version": PROVENANCE_CONTRACT_VERSION,
        "status": status,
        "links": links,
        "missing_links": missing,
        "invalid_links": invalid,
        "mismatched_links": mismatches,
    }
    return {**payload, "provenance_sha256": sha256_bytes(canonical_json_bytes(payload))}
