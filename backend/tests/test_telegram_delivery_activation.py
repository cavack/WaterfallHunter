from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from waterfallhunter.config import settings
from waterfallhunter.core.notification_delivery import DeliveryDisposition, DeliveryResult
from waterfallhunter.core.notifier import TelegramNotifier
from waterfallhunter.core.signal_metadata import canonical_sha256


class CapturingActivationNotifier(TelegramNotifier):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent: list[str] = []

    async def _send_text_result(self, text: str) -> DeliveryResult:
        self.sent.append(text)
        return DeliveryResult(DeliveryDisposition.DELIVERED)


def _configure(
    monkeypatch,
    *,
    delivery_enabled: bool,
    cutover_at: int | None,
) -> None:
    monkeypatch.setattr(settings, "telegram_token", "test-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "123")
    monkeypatch.setattr(
        settings,
        "telegram_signal_delivery_enabled",
        delivery_enabled,
        raising=False,
    )
    monkeypatch.setattr(
        settings,
        "telegram_signal_delivery_cutover_at",
        cutover_at,
        raising=False,
    )


def test_interactive_bot_credentials_do_not_enable_signal_delivery_by_default(
    monkeypatch,
    tmp_path,
) -> None:
    _configure(
        monkeypatch,
        delivery_enabled=False,
        cutover_at=0,
    )
    notifier = TelegramNotifier(
        db_adapter=SimpleNamespace(
            db_path=str(tmp_path / "missing.db")
        )
    )

    assert notifier.enabled is True
    assert notifier.signal_delivery_enabled is False

    async def scenario() -> None:
        await asyncio.wait_for(
            notifier._delivery_loop(),
            timeout=0.1,
        )

    asyncio.run(scenario())


def test_signal_delivery_requires_explicit_cutover_even_when_enabled(
    monkeypatch,
    tmp_path,
) -> None:
    _configure(
        monkeypatch,
        delivery_enabled=True,
        cutover_at=None,
    )
    notifier = TelegramNotifier(
        db_adapter=SimpleNamespace(
            db_path=str(tmp_path / "missing.db")
        )
    )

    assert notifier.enabled is True
    assert notifier.signal_delivery_enabled is False


def test_zero_cutover_remains_fail_closed_even_when_delivery_flag_is_enabled(
    monkeypatch,
) -> None:
    _configure(
        monkeypatch,
        delivery_enabled=True,
        cutover_at=0,
    )

    notifier = TelegramNotifier()

    assert notifier.enabled is True
    assert notifier.signal_delivery_cutover_at is None
    assert notifier.signal_delivery_enabled is False


def test_pre_cutover_strict_event_is_acknowledged_without_network_send(
    monkeypatch,
) -> None:
    _configure(
        monkeypatch,
        delivery_enabled=True,
        cutover_at=200,
    )
    notifier = CapturingActivationNotifier()
    payload = {
        "contract_version": "signal_confirmed_event_v1",
        "signal_id": 1,
        "symbol": "OLD/USDT:USDT",
        "signal_class": "STRICT",
        "strategy_profile": "strict_score_v2",
        "created_at": 100,
    }

    result = asyncio.run(
        notifier.deliver(
            {
                "event_id": "signal:1:confirmed:1",
                "idempotency_key": "signal:1:confirmed:1",
                "event_type": "SIGNAL_CONFIRMED",
                "payload_contract_version": "signal_confirmed_event_v1",
                "payload_json": json.dumps(payload),
                "payload_hash": canonical_sha256(payload),
            }
        )
    )

    assert notifier.signal_delivery_enabled is True
    assert result.disposition is DeliveryDisposition.DELIVERED
    assert notifier.sent == []


def test_post_cutover_strict_event_reaches_transport_after_identity_validation(
    monkeypatch,
) -> None:
    _configure(
        monkeypatch,
        delivery_enabled=True,
        cutover_at=200,
    )
    notifier = CapturingActivationNotifier()
    monkeypatch.setattr(
        notifier,
        "_load_strict_signal_material",
        lambda signal_id: (
            "NEW/USDT:USDT",
            {
                "score": 92.0,
                "metrics": {},
                "signal_class": "STRICT",
                "strategy_profile": "strict_score_v2",
            },
        )
        if signal_id == 2
        else None,
    )
    payload = {
        "contract_version": "signal_confirmed_event_v1",
        "signal_id": 2,
        "symbol": "NEW/USDT:USDT",
        "signal_class": "STRICT",
        "strategy_profile": "strict_score_v2",
        "created_at": 200,
    }

    result = asyncio.run(
        notifier.deliver(
            {
                "event_id": "signal:2:confirmed:1",
                "idempotency_key": "signal:2:confirmed:1",
                "event_type": "SIGNAL_CONFIRMED",
                "payload_contract_version": "signal_confirmed_event_v1",
                "payload_json": json.dumps(payload),
                "payload_hash": canonical_sha256(payload),
            }
        )
    )

    assert notifier.signal_delivery_enabled is True
    assert result.disposition is DeliveryDisposition.DELIVERED
    assert len(notifier.sent) == 1
    assert "NEW" in notifier.sent[0]


def test_strict_event_with_invalid_created_at_fails_closed(
    monkeypatch,
) -> None:
    _configure(
        monkeypatch,
        delivery_enabled=True,
        cutover_at=200,
    )
    notifier = CapturingActivationNotifier()
    payload = {
        "contract_version": "signal_confirmed_event_v1",
        "signal_id": 3,
        "symbol": "BAD/USDT:USDT",
        "signal_class": "STRICT",
        "strategy_profile": "strict_score_v2",
        "created_at": "200",
    }

    result = asyncio.run(
        notifier.deliver(
            {
                "event_id": "signal:3:confirmed:1",
                "idempotency_key": "signal:3:confirmed:1",
                "event_type": "SIGNAL_CONFIRMED",
                "payload_contract_version": "signal_confirmed_event_v1",
                "payload_json": json.dumps(payload),
                "payload_hash": canonical_sha256(payload),
            }
        )
    )

    assert result.disposition is DeliveryDisposition.PERMANENT_FAILURE
    assert result.error_code == "INVALID_EVENT_CREATED_AT"
    assert notifier.sent == []


def test_corrupted_outbox_payload_hash_fails_closed(monkeypatch) -> None:
    _configure(monkeypatch, delivery_enabled=True, cutover_at=1)
    notifier = CapturingActivationNotifier()
    payload = {
        "contract_version": "signal_confirmed_event_v1",
        "signal_id": 9,
        "symbol": "CORRUPT/USDT:USDT",
        "signal_class": "STRICT",
        "strategy_profile": "strict_score_v2",
        "created_at": 10,
    }
    result = asyncio.run(
        notifier.deliver(
            {
                "event_id": "signal:9:confirmed:1",
                "idempotency_key": "signal:9:confirmed:1",
                "event_type": "SIGNAL_CONFIRMED",
                "payload_contract_version": "signal_confirmed_event_v1",
                "payload_json": json.dumps(payload),
                "payload_hash": "0" * 64,
            }
        )
    )
    assert result.disposition is DeliveryDisposition.PERMANENT_FAILURE
    assert result.error_code == "EVENT_PAYLOAD_HASH_MISMATCH"
    assert notifier.sent == []


def test_canonical_entry_worker_requires_signal_delivery_gate(monkeypatch) -> None:
    import waterfallhunter.main as main

    monkeypatch.setattr(main.notifier, "enabled", True)
    monkeypatch.setattr(main.notifier, "signal_delivery_enabled", False)
    monkeypatch.setattr(main.notifier, "signal_delivery_cutover_at", None)
    assert main._build_entry_notification_worker() is None


def test_canonical_entry_worker_carries_release_cutover(monkeypatch) -> None:
    import waterfallhunter.main as main

    monkeypatch.setattr(settings, "telegram_token", "test-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "123")
    monkeypatch.setattr(main.notifier, "enabled", True)
    monkeypatch.setattr(main.notifier, "signal_delivery_enabled", True)
    monkeypatch.setattr(main.notifier, "signal_delivery_cutover_at", 200)
    worker = main._build_entry_notification_worker()
    assert worker is not None
    assert getattr(worker.transport, "cutover_at", None) == 200


def test_canonical_entry_probe_only_disables_worker_for_permanent_rejection() -> None:
    import waterfallhunter.main as main

    assert main._telegram_probe_allows_worker({"reachable": True, "status_code": 200}) is True
    assert main._telegram_probe_allows_worker({"reachable": False, "status_code": None}) is True
    assert main._telegram_probe_allows_worker({"reachable": False, "status_code": 429}) is True
    assert main._telegram_probe_allows_worker({"reachable": False, "status_code": 503}) is True
    assert main._telegram_probe_allows_worker({"reachable": False, "status_code": 400}) is False
    assert main._telegram_probe_allows_worker({"reachable": False, "status_code": 401}) is False
    assert main._telegram_probe_allows_worker({"reachable": False, "status_code": 403}) is False
    assert main._telegram_probe_allows_worker({"reachable": False, "status_code": 404}) is False


def test_canonical_notification_loop_pauses_after_rate_limit(monkeypatch) -> None:
    import asyncio
    from waterfallhunter import main
    from waterfallhunter.core.notification_delivery import DispatchOutcome

    calls = []
    sleeps = []

    class Worker:
        async def dispatch_once(self, *, now: int):
            calls.append(now)
            if len(calls) > 1:
                main._hunter_running = False
                return None
            return DispatchOutcome(
                event_id="entry:1:ready",
                state="RETRY_WAIT",
                attempt_count=1,
                next_available_at=now + 7,
                error_code="HTTP_429",
            )

    async def fake_sleep(delay: float):
        sleeps.append(delay)
        main._hunter_running = False

    monkeypatch.setattr(main, "_entry_notification_worker", Worker())
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    main._hunter_running = True
    try:
        asyncio.run(main._entry_notification_loop(interval_seconds=2.0))
    finally:
        main._hunter_running = False

    assert len(calls) == 1
    assert sleeps and sleeps[0] >= 7
