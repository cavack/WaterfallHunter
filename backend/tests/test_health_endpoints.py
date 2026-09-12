import asyncio
import time

import pytest
from fastapi import HTTPException


def test_standard_health_routes_are_registered():
    import waterfallhunter.main as main

    paths = {
        route.path
        for route in main.app.routes
        if hasattr(route, "path")
    }

    assert "/api/health" in paths
    assert "/livez" in paths
    assert "/readyz" in paths
    assert "/healthz" in paths


def test_livez_is_independent_of_catalogue_and_hunter_freshness(
    monkeypatch,
):
    import waterfallhunter.main as main

    monkeypatch.setattr(
        main.scanner,
        "last_successful_refresh_at",
        None,
    )

    monkeypatch.setattr(
        main,
        "_hunter_last_progress_at",
        None,
    )

    result = asyncio.run(
        main.liveness_check()
    )

    assert result == {
        "status": "alive",
    }


def test_readyz_healthz_and_api_health_share_healthy_readiness_semantics(
    monkeypatch,
):
    import waterfallhunter.main as main

    now = time.time()

    monkeypatch.setattr(
        main.scanner,
        "last_successful_refresh_at",
        now,
    )

    monkeypatch.setattr(
        main,
        "_hunter_last_progress_at",
        now,
    )

    api_health = asyncio.run(
        main.health_check()
    )

    readyz = asyncio.run(
        main.readiness_check()
    )

    healthz = asyncio.run(
        main.healthz_check()
    )

    assert api_health[
        "status"
    ] == "healthy"

    assert readyz == api_health
    assert healthz == api_health

    assert (
        "lbank_execution_shadow"
        in api_health
    )


def test_readyz_and_healthz_reject_stale_runtime_while_livez_remains_alive(
    monkeypatch,
):
    import waterfallhunter.main as main

    now = time.time()

    monkeypatch.setattr(
        main.scanner,
        "last_successful_refresh_at",
        now - 30_000,
    )

    monkeypatch.setattr(
        main,
        "_hunter_last_progress_at",
        now - 1_000,
    )

    assert asyncio.run(
        main.liveness_check()
    ) == {
        "status": "alive",
    }

    with pytest.raises(
        HTTPException
    ) as ready_error:
        asyncio.run(
            main.readiness_check()
        )

    assert (
        ready_error.value.status_code
        == 503
    )

    assert (
        ready_error.value.detail[
            "status"
        ]
        == "degraded"
    )

    with pytest.raises(
        HTTPException
    ) as health_error:
        asyncio.run(
            main.healthz_check()
        )

    assert (
        health_error.value.status_code
        == 503
    )
