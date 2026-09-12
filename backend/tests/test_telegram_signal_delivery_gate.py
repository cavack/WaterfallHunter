from __future__ import annotations

import asyncio
from types import SimpleNamespace

from waterfallhunter.config import settings
from waterfallhunter.core.notifier import TelegramNotifier


class _CountingWorker:
    def __init__(self) -> None:
        self.calls = 0

    async def dispatch_once(self, *, now: int):
        del now
        self.calls += 1
        return None


class _RateLimitedWorker:
    def __init__(self) -> None:
        self.calls = 0

    async def dispatch_once(self, *, now: int):
        del now
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(state="RETRY_WAIT", error_code="HTTP_429")
        return None


def test_credentials_alone_do_not_enable_signal_delivery(monkeypatch) -> None:
    monkeypatch.setattr(settings, "telegram_token", "test-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "123")
    monkeypatch.setattr(
        settings,
        "telegram_signal_delivery_enabled",
        False,
        raising=False,
    )

    notifier = TelegramNotifier()

    assert notifier.enabled is True
    assert getattr(notifier, "signal_delivery_enabled", None) is False


def test_delivery_loop_returns_without_claiming_when_signal_delivery_disabled() -> None:
    notifier = TelegramNotifier()
    notifier.enabled = True
    notifier.signal_delivery_enabled = False
    worker = _CountingWorker()
    notifier.delivery_worker = worker

    asyncio.run(
        asyncio.wait_for(
            notifier._delivery_loop(),
            timeout=0.1,
        )
    )

    assert worker.calls == 0


def test_delivery_loop_stops_claiming_after_telegram_rate_limit() -> None:
    async def scenario() -> None:
        notifier = TelegramNotifier()
        notifier.enabled = True
        notifier.signal_delivery_enabled = True
        worker = _RateLimitedWorker()
        notifier.delivery_worker = worker

        task = asyncio.create_task(notifier._delivery_loop())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert worker.calls == 1

    asyncio.run(scenario())


def test_interactive_bot_recreates_delivery_wakeup_for_each_lifespan(monkeypatch) -> None:
    notifier = TelegramNotifier()
    notifier.enabled = True
    notifier.signal_delivery_enabled = False
    prior_wakeup = notifier.delivery_wakeup

    async def bind_prior_event() -> None:
        waiter = asyncio.create_task(prior_wakeup.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)

    asyncio.run(bind_prior_event())

    class AbortClient:
        async def __aenter__(self):
            raise asyncio.CancelledError()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "waterfallhunter.core.notifier.httpx.AsyncClient",
        lambda *args, **kwargs: AbortClient(),
    )

    async def start_second_lifespan() -> None:
        try:
            await notifier.start_interactive_bot()
        except asyncio.CancelledError:
            pass

    asyncio.run(start_second_lifespan())

    assert notifier.delivery_wakeup is not prior_wakeup


def test_interactive_bot_never_starts_legacy_signal_delivery_loop(monkeypatch) -> None:
    notifier = TelegramNotifier()
    notifier.enabled = True
    notifier.signal_delivery_enabled = True
    legacy_started = False

    async def legacy_loop():
        nonlocal legacy_started
        legacy_started = True

    notifier._delivery_loop = legacy_loop

    class AbortClient:
        async def __aenter__(self):
            raise asyncio.CancelledError()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "waterfallhunter.core.notifier.httpx.AsyncClient",
        lambda *args, **kwargs: AbortClient(),
    )

    async def run():
        try:
            await notifier.start_interactive_bot()
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert legacy_started is False
