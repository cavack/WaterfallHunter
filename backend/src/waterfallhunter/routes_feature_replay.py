import asyncio

from fastapi import APIRouter, HTTPException, Request

from waterfallhunter.core.feature_replay import FeatureReplayStore


def build_feature_replay_router(store: FeatureReplayStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/feature-replay")
    async def feature_replay(request: Request):
        unsupported = sorted(request.query_params.keys())
        if unsupported:
            raise HTTPException(status_code=422, detail={"error": "unsupported query parameter", "unsupported_parameters": unsupported, "allowed_parameters": []})
        return await asyncio.to_thread(store.build_report)

    return router
