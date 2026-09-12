import asyncio
import threading
from collections.abc import Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI

from waterfallhunter.core.lbank_execution_suitability_report import (
    LBankExecutionSuitabilityReport,
)
from waterfallhunter.routes_execution_suitability import (
    build_execution_suitability_router,
)


class _BlockingBuild:
    def __init__(self, *, starts_before_ready: int = 1) -> None:
        self.calls: list[int] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self._starts_before_ready = starts_before_ready
        self._lock = threading.Lock()

    def __call__(
        self,
        *,
        symbol_limit: int = 10_000,
        examples_per_status: int = 20,
    ) -> dict:
        del symbol_limit
        with self._lock:
            self.calls.append(examples_per_status)
            if len(self.calls) >= self._starts_before_ready:
                self.started.set()
        assert self.release.wait(timeout=5.0)
        return _report_payload(examples_per_status)


def _report_payload(examples_per_status: int) -> dict:
    return {
        "schema_version": "singleflight-test-v1",
        "examples_per_status": examples_per_status,
    }


def _build_test_app(tmp_path) -> FastAPI:
    app = FastAPI()
    app.include_router(
        build_execution_suitability_router(str(tmp_path / "unused.db"))
    )
    return app


def _install_build(monkeypatch, replacement) -> None:
    monkeypatch.setattr(
        LBankExecutionSuitabilityReport,
        "build_report",
        replacement,
    )


async def _get(client: httpx.AsyncClient, examples_per_status: int):
    return await client.get(
        "/api/execution-suitability",
        params={"examples_per_status": examples_per_status},
    )


async def _with_client(
    app: FastAPI,
    scenario: Callable[[httpx.AsyncClient], Awaitable[object]],
):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await scenario(client)


def _assert_response(response: httpx.Response, examples_per_status: int) -> None:
    assert response.status_code == 200
    assert response.json()["examples_per_status"] == examples_per_status


def test_concurrent_execution_suitability_requests_share_one_build(
    tmp_path,
    monkeypatch,
):
    app = _build_test_app(tmp_path)
    build = _BlockingBuild()
    _install_build(monkeypatch, build)

    async def scenario(client):
        first = asyncio.create_task(_get(client, 0))
        assert await asyncio.to_thread(build.started.wait, 2.0)
        second = asyncio.create_task(_get(client, 0))
        await asyncio.sleep(0.1)
        build.release.set()
        return await asyncio.gather(first, second)

    first, second = asyncio.run(_with_client(app, scenario))
    _assert_response(first, 0)
    _assert_response(second, 0)
    assert first.json() == second.json()
    assert build.calls == [0]


def test_cancelled_waiter_does_not_cancel_shared_report_build(
    tmp_path,
    monkeypatch,
):
    app = _build_test_app(tmp_path)
    build = _BlockingBuild()
    _install_build(monkeypatch, build)

    async def scenario(client):
        cancelled = asyncio.create_task(_get(client, 0))
        assert await asyncio.to_thread(build.started.wait, 2.0)
        survivor = asyncio.create_task(_get(client, 0))
        await asyncio.sleep(0.1)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        build.release.set()
        return await survivor

    response = asyncio.run(_with_client(app, scenario))
    _assert_response(response, 0)
    assert build.calls == [0]


def test_failed_report_build_is_not_reused_by_next_request(
    tmp_path,
    monkeypatch,
):
    app = _build_test_app(tmp_path)
    calls = 0

    def flaky_build(
        _report,
        *,
        symbol_limit=10_000,
        examples_per_status=20,
    ):
        nonlocal calls
        del symbol_limit
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic report failure")
        return _report_payload(examples_per_status)

    _install_build(monkeypatch, flaky_build)

    async def scenario(client):
        with pytest.raises(RuntimeError, match="synthetic report failure"):
            await _get(client, 0)
        await asyncio.sleep(0)
        return await _get(client, 0)

    response = asyncio.run(_with_client(app, scenario))
    _assert_response(response, 0)
    assert calls == 2


def test_different_execution_suitability_request_keys_do_not_share_builds(
    tmp_path,
    monkeypatch,
):
    app = _build_test_app(tmp_path)
    build = _BlockingBuild(starts_before_ready=2)
    _install_build(monkeypatch, build)

    async def scenario(client):
        requests = [
            asyncio.create_task(_get(client, examples))
            for examples in (0, 1)
        ]
        assert await asyncio.to_thread(build.started.wait, 2.0)
        build.release.set()
        return await asyncio.gather(*requests)

    first, second = asyncio.run(_with_client(app, scenario))
    _assert_response(first, 0)
    _assert_response(second, 1)
    assert sorted(build.calls) == [0, 1]
