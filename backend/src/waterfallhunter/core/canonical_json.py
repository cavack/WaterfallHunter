"""Deterministic JSON primitives for provenance and regression artifacts.

The domain contracts intentionally use a JSON subset (objects, arrays, strings,
booleans, null, integers, and finite floats).  Sorting object keys, using UTF-8,
forbidding non-finite numbers, and removing insignificant whitespace gives the
stable byte representation required by the Wave-0 hashing contracts.
"""

from __future__ import annotations

import math
from typing import Any

import rfc8785


def _validate(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite JSON number at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"JSON object key at {path} is not a string")
            _validate(item, f"{path}.{key}")
        return
    raise TypeError(f"unsupported JSON value at {path}: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes and reject unsafe values."""

    _validate(value)
    return rfc8785.dumps(value)
