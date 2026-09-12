import asyncio

from fastapi import APIRouter, HTTPException, Request

from waterfallhunter.core.lbank_execution_outcome_report import (
    LBankExecutionOutcomeReport,
)


def build_execution_outcome_router(
    db_path: str,
) -> APIRouter:
    """Build a parameter-locked, read-only outcome-validation router."""
    router = APIRouter()
    report = LBankExecutionOutcomeReport(
        db_path=db_path
    )

    @router.get(
        "/api/execution-outcome-validation"
    )
    async def execution_outcome_validation(
        request: Request,
    ):
        unsupported = sorted(
            request.query_params.keys()
        )
        if unsupported:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unsupported query parameter",
                    "unsupported_parameters": unsupported,
                    "allowed_parameters": [],
                },
            )
        return await asyncio.to_thread(
            report.build_report
        )

    return router
