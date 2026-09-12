from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from waterfallhunter.core.entry_decision_store import EntryDecisionStore
from waterfallhunter.core.managed_sqlite import ManagedSQLiteError
from waterfallhunter.core.schema_contract import SchemaContractError


def _build_report(db_path: str, limit: int) -> dict[str, Any]:
    events = EntryDecisionStore(db_path).recent_changes(limit=limit)
    return {
        "contract_version": "canonical_decision_history_v1",
        "operational": True,
        "observational_only": True,
        "hard_gating_allowed": False,
        "count": len(events),
        "decisions": events,
    }


def build_recent_signals_router(db_path: str) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/recent-signals",
        responses={
            422: {"description": "Unsupported query parameter"},
            503: {"description": "Recent signal history unavailable"},
        },
    )
    async def recent_signals(
        request: Request,
        limit: int = Query(default=20, ge=1, le=100),
    ):
        unsupported = sorted(set(request.query_params.keys()) - {"limit"})
        if unsupported:
            raise HTTPException(status_code=422, detail={
                "error": "unsupported query parameter",
                "unsupported_parameters": unsupported,
                "allowed_parameters": ["limit"],
            })
        try:
            return await asyncio.to_thread(_build_report, db_path, limit)
        except (ManagedSQLiteError, SchemaContractError, sqlite3.Error) as exc:
            raise HTTPException(
                status_code=503,
                detail="recent signal history is unavailable",
            ) from exc

    return router
