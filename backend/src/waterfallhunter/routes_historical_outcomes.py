import asyncio

from fastapi import APIRouter, HTTPException, Request

from waterfallhunter.core.historical_outcome_store import HistoricalOutcomeStore


def build_historical_outcome_router(store: HistoricalOutcomeStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/historical-outcomes")
    async def historical_outcomes(request: Request):
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
        return await asyncio.to_thread(store.build_report)

    return router
