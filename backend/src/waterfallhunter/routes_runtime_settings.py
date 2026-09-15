"""Operator settings API.

Read is open (the dashboard renders current values); every mutation requires
the operator token. The token is compared with ``secrets.compare_digest`` to
avoid leaking its length through timing, and a missing token in the
environment disables mutation entirely rather than defaulting to open.
"""
from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from waterfallhunter.config import settings
from waterfallhunter.core.runtime_settings import RuntimeSettingsStore

router = APIRouter(prefix="/api/settings", tags=["settings"])

_store: RuntimeSettingsStore | None = None


def bind_store(store: RuntimeSettingsStore) -> None:
    global _store
    _store = store


def _require_store() -> RuntimeSettingsStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="settings store unavailable")
    return _store


def _authorize(token: str | None) -> str:
    configured = (settings.operator_token or "").strip()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "settings changes are disabled: set OPERATOR_TOKEN in the "
                "environment to enable them"
            ),
        )
    supplied = (token or "").strip()
    if not supplied or not secrets.compare_digest(supplied, configured):
        raise HTTPException(status_code=401, detail="invalid operator token")
    return "operator"


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: dict[str, Any] = Field(min_length=1)
    note: str = ""


@router.get("")
async def read_settings() -> dict[str, Any]:
    return _require_store().describe()


@router.get("/history")
async def read_history(limit: int = 50) -> dict[str, Any]:
    store = _require_store()
    bounded = max(1, min(int(limit), 200))
    return {
        "contract_version": "runtime_settings_history_v1",
        "generated_at": time.time(),
        "entries": store.history(bounded),
    }


@router.put("")
async def write_settings(
    payload: SettingsUpdate,
    x_operator_token: str | None = Header(default=None),
) -> dict[str, Any]:
    store = _require_store()
    actor = _authorize(x_operator_token)
    try:
        store.apply(payload.changes, actor=actor, note=payload.note)
    except ValueError as exc:
        # Operator-facing validation failure, not a server fault.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return store.describe()


@router.post("/reset")
async def reset_settings(
    x_operator_token: str | None = Header(default=None),
) -> dict[str, Any]:
    store = _require_store()
    actor = _authorize(x_operator_token)
    store.reset(actor=actor)
    return store.describe()
