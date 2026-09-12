import asyncio
import threading

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)

from waterfallhunter.core.lbank_execution_stats import (
    LBankExecutionStats,
)
from waterfallhunter.core.lbank_execution_suitability_report import (
    LBankExecutionSuitabilityReport,
)


_ALLOWED_QUERY_PARAMETERS = frozenset(
    {
        "examples_per_status",
    }
)


def build_execution_suitability_router(
    db_path: str,
) -> APIRouter:
    """
    Build the read-only execution-suitability API router.

    The router is intentionally observational only.

    It must never:
    - mutate scan_eligible
    - mutate catalogue state
    - mutate hunter states
    - mutate scores
    - send alerts
    - place orders
    - accept threshold overrides from API callers
    """
    router = APIRouter()

    stats = LBankExecutionStats(
        db_path=db_path
    )

    report_builder = (
        LBankExecutionSuitabilityReport(
            stats
        )
    )

    inflight_reports: dict[
        tuple[
            asyncio.AbstractEventLoop,
            int,
        ],
        asyncio.Task,
    ] = {}
    inflight_lock = threading.Lock()

    async def build_report_singleflight(
        *,
        examples_per_status: int,
    ) -> dict:
        loop = asyncio.get_running_loop()
        key = (
            loop,
            examples_per_status,
        )

        with inflight_lock:
            task = inflight_reports.get(
                key
            )

            if (
                task is None
                or task.done()
            ):
                task = asyncio.create_task(
                    asyncio.to_thread(
                        report_builder.build_report,
                        examples_per_status=(
                            examples_per_status
                        ),
                    )
                )
                inflight_reports[key] = task

                def cleanup(
                    completed: asyncio.Task,
                    *,
                    cleanup_key=key,
                    cleanup_task=task,
                ) -> None:
                    if not completed.cancelled():
                        completed.exception()

                    with inflight_lock:
                        if (
                            inflight_reports.get(
                                cleanup_key
                            )
                            is cleanup_task
                        ):
                            inflight_reports.pop(
                                cleanup_key,
                                None,
                            )

                task.add_done_callback(
                    cleanup
                )

        return await asyncio.shield(
            task
        )

    @router.get(
        "/api/execution-suitability"
    )
    async def execution_suitability_report(
        request: Request,
        examples_per_status: int = Query(
            default=20,
            ge=0,
            le=100,
        ),
    ):
        unknown_parameters = sorted(
            set(
                request.query_params.keys()
            )
            - _ALLOWED_QUERY_PARAMETERS
        )

        if unknown_parameters:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": (
                        "unsupported query parameter"
                    ),
                    "unsupported_parameters": (
                        unknown_parameters
                    ),
                    "allowed_parameters": sorted(
                        _ALLOWED_QUERY_PARAMETERS
                    ),
                },
            )

        return await build_report_singleflight(
            examples_per_status=(
                examples_per_status
            ),
        )

    return router
