"""Read-only operational endpoints for Lifecycle V2 shadow."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from waterfallhunter.core.lifecycle_feature_registry import (
    lifecycle_feature_registry,
    lifecycle_v2_policy_v1,
)
from waterfallhunter.core.lifecycle_v2_shadow_store import LifecycleV2ShadowStore


def build_lifecycle_v2_shadow_router(store: LifecycleV2ShadowStore) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/lifecycle-v2-shadow",
        responses={422: {"description": "Unsupported query or invalid limit"}},
    )
    async def lifecycle_v2_shadow(request: Request, limit: int = 500):
        unsupported = sorted(set(request.query_params.keys()) - {"limit"})
        if unsupported:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unsupported query parameter",
                    "unsupported_parameters": unsupported,
                    "allowed_parameters": ["limit"],
                },
            )
        if not 1 <= limit <= 5_000:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 5000")
        return await asyncio.to_thread(store.report, limit=limit)

    @router.get(
        "/api/lifecycle-v2-contract",
        responses={422: {"description": "Unsupported query parameter"}},
    )
    async def lifecycle_v2_contract(request: Request):
        unsupported = sorted(request.query_params.keys())
        if unsupported:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unsupported query parameter",
                    "unsupported_parameters": unsupported,
                    "allowed_parameters": [],
                },
            )
        return {
            "feature_registry": lifecycle_feature_registry(),
            "policy": lifecycle_v2_policy_v1().model_dump(mode="json"),
            "shadow_only": True,
            "promotion_allowed": False,
        }

    return router
