import asyncio

from fastapi import APIRouter, HTTPException, Request

from waterfallhunter.core.production_evidence import ProductionEvidenceRecorder


def build_production_evidence_router(recorder: ProductionEvidenceRecorder) -> APIRouter:
    router = APIRouter()

    @router.get("/api/production-evidence")
    async def production_evidence(request: Request):
        unsupported = sorted(request.query_params.keys())
        if unsupported:
            raise HTTPException(
                status_code=422,
                detail={"error": "unsupported query parameter", "unsupported_parameters": unsupported, "allowed_parameters": []},
            )
        return await asyncio.to_thread(recorder.build_report)

    return router
