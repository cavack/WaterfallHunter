"""Best-effort process heap reclamation for long-lived Linux workers."""

from __future__ import annotations

import ctypes
import gc
import sys
from typing import Any, Callable


_MALLOC_TRIM: Callable[[int], Any] | None = None
_MALLOC_TRIM_RESOLVED = False


def _resolve_malloc_trim() -> Callable[[int], Any] | None:
    global _MALLOC_TRIM, _MALLOC_TRIM_RESOLVED
    if _MALLOC_TRIM_RESOLVED:
        return _MALLOC_TRIM
    _MALLOC_TRIM_RESOLVED = True
    if not sys.platform.startswith("linux"):
        return None
    try:
        trim = ctypes.CDLL(None).malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
    except (AttributeError, OSError):
        return None
    _MALLOC_TRIM = trim
    return trim


def trim_process_heap() -> dict[str, int | bool]:
    """Collect cycles and ask glibc to release free heap pages, fail-safe."""
    collected = int(gc.collect())
    trim = _resolve_malloc_trim()
    if trim is None:
        return {
            "gc_collected": collected,
            "malloc_trim_available": False,
            "malloc_trim_released": False,
        }
    try:
        released = bool(trim(0))
    except Exception:
        released = False
    return {
        "gc_collected": collected,
        "malloc_trim_available": True,
        "malloc_trim_released": released,
    }
