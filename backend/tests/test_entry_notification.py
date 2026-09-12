from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import threading

import httpx

from schema_test_support import migrate_test_database
from waterfallhunter.core.entry_decision_store import EntryDecisionStore
from waterfallhunter.core.notification_delivery import (
    DeliveryDisposition,
    DeliveryResult,
    DurableNotificationWorker,
)
from waterfallhunter.core.notifier import TelegramNotifier, TelegramSignalTransport


def entry_packet(decision: str = "ENTRY_READY") -> dict:
    return {
        "contract_version": "entry_decision_v1",
        "policy_version": "entry_policy_v1",
        "evaluated_at": 100,
        "decision": decision,
        "lifecycle_state": "PRE-TRIGGER",
        "entry_readiness": 84.5,
        "evidence_coverage_pct": 91.0,
        "hard_blocked": False,
        "block_reasons": [],
        "reason_codes": ["SELL_PRESSURE_CONFIRMED"],
        "components": {"cascade": {"points": 8.5, "maximum": 10.0}},
        "trade_plan": {
            "entry_price": 0.1,
            "stop_loss": 0.103,
            "take_profit_1": 0.097,
            "take_profit_2": 0.094,
            "take_profit_3": 0.091,
            "leverage": 3.0,
        },
        "policy": {},
    }


class FakeTransport:
    def __init__(self):
        self.calls: list[dict] = []

    async def deliver(self, event: dict) -> DeliveryResult:
        self.calls.append(event)
        return DeliveryResult(DeliveryDisposition.DELIVERED)


def test_entry_ready_transition_creates_durable_notification(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "entry-notify.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed("SXT/USDT:USDT", entry_packet())
    assert event_id == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT event_id,decision_event_id,event_type,status,payload_json "
            "FROM entry_notification_outbox"
        ).fetchone()
    assert row[:4] == ("entry:1:ready", 1, "ENTRY_READY", "PENDING")
    payload = json.loads(row[4])
    assert payload["symbol"] == "SXT/USDT:USDT"
    assert payload["decision_packet"]["entry_readiness"] == 84.5


def test_entry_ready_outbox_is_delivered_by_generic_worker(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "entry-delivery.db")
    EntryDecisionStore(db_path).append_if_changed("SXT/USDT:USDT", entry_packet())
    transport = FakeTransport()
    worker = DurableNotificationWorker(
        db_path,
        transport,
        worker_id="entry-worker",
        outbox_table="entry_notification_outbox",
    )
    result = asyncio.run(worker.dispatch_once(now=int(time.time()) + 1))
    assert result is not None and result.state == "DELIVERED"
    assert transport.calls[0]["event_type"] == "ENTRY_READY"


def test_telegram_transport_classifies_success_and_errors() -> None:
    responses = [
        httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
        httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 7}}),
        httpx.Response(403, json={"ok": False, "description": "Forbidden"}),
    ]

    async def run() -> list[DeliveryResult]:
        queue = list(responses)

        async def handler(request: httpx.Request) -> httpx.Response:
            return queue.pop(0)

        transport = TelegramSignalTransport(
            "token",
            "123",
            max_entry_age_seconds=10_000_000_000,
            http_transport=httpx.MockTransport(handler),
        )
        event = {
            "event_id": "entry:1:ready",
            "payload_json": json.dumps({
                "contract_version": "entry_ready_notification_v1",
                "symbol": "SXT/USDT:USDT",
                "decision_packet": entry_packet(),
            }),
        }
        return [await transport.deliver(event) for _ in range(3)]

    delivered, limited, forbidden = asyncio.run(run())
    assert delivered.disposition is DeliveryDisposition.DELIVERED
    assert limited.disposition is DeliveryDisposition.RATE_LIMITED
    assert limited.retry_after_seconds == 7
    assert forbidden.disposition is DeliveryDisposition.PERMANENT_FAILURE


def test_pre_cutover_entry_ready_is_acknowledged_without_send() -> None:
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    transport = TelegramSignalTransport(
        "token",
        "123",
        cutover_at=200,
        max_entry_age_seconds=10_000_000_000,
        http_transport=httpx.MockTransport(handler),
    )
    event = {
        "event_id": "entry:1:ready",
        "payload_json": json.dumps({
            "contract_version": "entry_ready_notification_v1",
            "symbol": "SXT/USDT:USDT",
            "decision_packet": entry_packet(),
        }),
    }
    result = asyncio.run(transport.deliver(event))
    assert result.disposition is DeliveryDisposition.DELIVERED
    assert calls == []


def test_telegram_transport_requires_ok_true_on_http_200() -> None:
    responses = [
        httpx.Response(200, json={"ok": False, "description": "logical failure"}),
        httpx.Response(200, text="not-json"),
    ]

    async def run() -> list[DeliveryResult]:
        queue = list(responses)

        async def handler(request: httpx.Request) -> httpx.Response:
            del request
            return queue.pop(0)

        transport = TelegramSignalTransport(
            "token",
            "123",
            max_entry_age_seconds=10_000_000_000,
            http_transport=httpx.MockTransport(handler),
        )
        event = {
            "event_id": "entry:1:ready",
            "payload_json": json.dumps({
                "contract_version": "entry_ready_notification_v1",
                "symbol": "SXT/USDT:USDT",
                "decision_packet": entry_packet(),
            }),
        }
        return [await transport.deliver(event), await transport.deliver(event)]

    logical_failure, invalid_json = asyncio.run(run())
    assert logical_failure.disposition is DeliveryDisposition.TRANSIENT_FAILURE
    assert logical_failure.error_code == "INVALID_TELEGRAM_RESPONSE"
    assert invalid_json.disposition is DeliveryDisposition.TRANSIENT_FAILURE
    assert invalid_json.error_code == "INVALID_TELEGRAM_RESPONSE"


def test_telegram_probe_requires_valid_bot_and_chat() -> None:
    async def run(responses):
        queue = list(responses)
        seen = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, str(request.url)))
            return queue.pop(0)

        transport = TelegramSignalTransport(
            "token",
            "123",
            max_entry_age_seconds=10_000_000_000,
            http_transport=httpx.MockTransport(handler),
        )
        return await transport.probe(), seen

    success, success_seen = asyncio.run(run([
        httpx.Response(200, json={"ok": True, "result": {"id": 1}}),
        httpx.Response(200, json={"ok": True, "result": {"id": 123}}),
    ]))
    assert success["reachable"] is True
    assert success["chat_reachable"] is True
    assert len(success_seen) == 2
    assert "getMe" in success_seen[0][1]
    assert "getChat" in success_seen[1][1]

    failed, failed_seen = asyncio.run(run([
        httpx.Response(200, json={"ok": True, "result": {"id": 1}}),
        httpx.Response(400, json={"ok": False, "description": "chat not found"}),
    ]))
    assert failed["reachable"] is False
    assert failed["bot_reachable"] is True
    assert failed["chat_reachable"] is False
    assert len(failed_seen) == 2


def test_entry_ready_message_includes_lifecycle_state() -> None:
    payload = {
        "contract_version": "entry_ready_notification_v1",
        "symbol": "SXT/USDT:USDT",
        "decision_packet": entry_packet(),
    }
    message = TelegramNotifier.build_entry_ready_message(payload)
    assert "Lifecycle" in message
    assert "PRE-TRIGGER" in message


def test_telegram_transport_suppresses_overage_entry_ready_before_send(tmp_path, monkeypatch) -> None:
    import waterfallhunter.core.notifier as notifier_module

    db_path = migrate_test_database(tmp_path / "stale-entry-delivery.db")
    store = EntryDecisionStore(db_path)
    packet = entry_packet()
    packet["evaluated_at"] = 100
    decision_event_id = store.append_if_changed("SXT/USDT:USDT", packet)
    assert decision_event_id == 1
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr(notifier_module.time, "time", lambda: 281.0)
    transport = TelegramSignalTransport(
        "token",
        "123",
        decision_db_path=db_path,
        max_entry_age_seconds=180,
        http_transport=httpx.MockTransport(handler),
    )
    event = {
        "event_id": "entry:1:ready",
        "payload_json": json.dumps({
            "contract_version": "entry_ready_notification_v1",
            "event_id": "entry:1:ready",
            "event_type": "ENTRY_READY",
            "decision_event_id": 1,
            "symbol": "SXT/USDT:USDT",
            "decision_packet": packet,
        }),
    }

    result = asyncio.run(transport.deliver(event))
    assert result.disposition is DeliveryDisposition.DELIVERED
    assert calls == []


def test_telegram_transport_suppresses_future_dated_entry_ready(monkeypatch) -> None:
    import waterfallhunter.core.notifier as notifier_module

    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    packet = entry_packet()
    packet["evaluated_at"] = 301
    monkeypatch.setattr(notifier_module.time, "time", lambda: 300.0)
    transport = TelegramSignalTransport(
        "token", "123", max_entry_age_seconds=180,
        http_transport=httpx.MockTransport(handler),
    )
    event = {
        "event_id": "entry:1:ready",
        "payload_json": json.dumps({
            "contract_version": "entry_ready_notification_v1",
            "event_id": "entry:1:ready",
            "event_type": "ENTRY_READY",
            "decision_event_id": 1,
            "symbol": "SXT/USDT:USDT",
            "decision_packet": packet,
        }),
    }

    result = asyncio.run(transport.deliver(event))
    assert result.disposition is DeliveryDisposition.DELIVERED
    assert calls == []


def test_telegram_transport_suppresses_superseded_entry_ready_before_send(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "superseded-entry-delivery.db")
    store = EntryDecisionStore(db_path)
    packet = entry_packet()
    packet["evaluated_at"] = int(time.time())
    decision_event_id = store.append_if_changed("SXT/USDT:USDT", packet)
    assert decision_event_id == 1
    invalidated = {**packet, "decision": "INVALIDATED", "evaluated_at": packet["evaluated_at"] + 1}
    assert store.append_if_changed("SXT/USDT:USDT", invalidated) == 2
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    transport = TelegramSignalTransport(
        "token",
        "123",
        decision_db_path=db_path,
        max_entry_age_seconds=180,
        http_transport=httpx.MockTransport(handler),
    )
    event = {
        "event_id": "entry:1:ready",
        "payload_json": json.dumps({
            "contract_version": "entry_ready_notification_v1",
            "event_id": "entry:1:ready",
            "event_type": "ENTRY_READY",
            "decision_event_id": 1,
            "symbol": "SXT/USDT:USDT",
            "decision_packet": packet,
        }),
    }

    result = asyncio.run(transport.deliver(event))
    assert result.disposition is DeliveryDisposition.DELIVERED
    assert calls == []


def test_telegram_transport_rejects_payload_hash_mismatch_before_send() -> None:
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    packet = entry_packet()
    packet["evaluated_at"] = int(time.time())
    payload = {
        "contract_version": "entry_ready_notification_v1",
        "event_id": "entry:1:ready",
        "event_type": "ENTRY_READY",
        "decision_event_id": 1,
        "symbol": "SXT/USDT:USDT",
        "decision_packet": packet,
    }
    transport = TelegramSignalTransport(
        "token", "123", http_transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(transport.deliver({
        "event_id": "entry:1:ready",
        "payload_json": json.dumps(payload, sort_keys=True, separators=(",", ":")),
        "payload_hash": "0" * 64,
    }))
    assert result.disposition is DeliveryDisposition.PERMANENT_FAILURE
    assert result.error_code == "PAYLOAD_HASH_MISMATCH"
    assert calls == []


def test_telegram_transport_hydrates_available_ai_advisory_from_decision_event(tmp_path, monkeypatch) -> None:
    import waterfallhunter.core.notifier as notifier_module

    db_path = migrate_test_database(tmp_path / "entry-advisory-delivery.db")
    store = EntryDecisionStore(db_path)
    packet = entry_packet()
    packet["evaluated_at"] = 100
    decision_event_id = store.append_if_changed("SXT/USDT:USDT", packet)
    assert decision_event_id == 1
    store.append_advisory(
        decision_event_id,
        {
            "observational_only": True,
            "decision_mutated": False,
            "ai_advice": "SHORT",
            "ai_confidence": 88,
            "ai_reasoning": "sell pressure aligns",
            "ai_provider": "gemini",
            "ai_model": "gemini-test",
            "ai_status": "AVAILABLE",
        },
        advisory_at=100,
    )
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT event_id,payload_json,payload_hash FROM entry_notification_outbox"
        ).fetchone()
    assert row is not None
    sent = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr(notifier_module.time, "time", lambda: 101.0)
    transport = TelegramSignalTransport(
        "token", "123", decision_db_path=db_path,
        max_entry_age_seconds=180, http_transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(transport.deliver({
        "event_id": row[0],
        "payload_json": row[1],
        "payload_hash": row[2],
    }))
    assert result.disposition is DeliveryDisposition.DELIVERED
    assert len(sent) == 1
    text = sent[0]["text"]
    assert "AI" in text
    assert "SHORT" in text
    assert "88" in text


def test_entry_outbox_waits_for_advisory_without_consuming_attempts(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "advisory-gate.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed("SXT/USDT:USDT", entry_packet())
    assert event_id == 1
    with sqlite3.connect(db_path) as conn:
        created_at = conn.execute(
            "SELECT created_at FROM entry_notification_outbox"
        ).fetchone()[0]

    transport = FakeTransport()
    worker = DurableNotificationWorker(
        db_path,
        transport,
        worker_id="entry-advisory-gate",
        outbox_table="entry_notification_outbox",
        advisory_wait_seconds=10,
    )
    assert asyncio.run(worker.dispatch_once(now=created_at + 2)) is None
    with sqlite3.connect(db_path) as conn:
        state = conn.execute(
            "SELECT status,attempt_count FROM entry_notification_outbox"
        ).fetchone()
    assert state == ("PENDING", 0)
    assert transport.calls == []

    store.append_advisory(
        event_id,
        {
            "observational_only": True,
            "decision_mutated": False,
            "ai_advice": "UNAVAILABLE",
            "ai_confidence": 0,
            "ai_reasoning": "provider timed out",
            "ai_provider": "none",
            "ai_model": "none",
            "ai_status": "UNAVAILABLE",
        },
        advisory_at=created_at + 3,
    )
    delivered = asyncio.run(worker.dispatch_once(now=created_at + 3))
    assert delivered is not None and delivered.state == "DELIVERED"
    assert len(transport.calls) == 1


def test_entry_outbox_fails_open_to_explicit_unavailable_after_advisory_grace(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "advisory-crash-fallback.db")
    EntryDecisionStore(db_path).append_if_changed("SXT/USDT:USDT", entry_packet())
    with sqlite3.connect(db_path) as conn:
        created_at = conn.execute(
            "SELECT created_at FROM entry_notification_outbox"
        ).fetchone()[0]
    transport = FakeTransport()
    worker = DurableNotificationWorker(
        db_path,
        transport,
        worker_id="entry-advisory-fallback",
        outbox_table="entry_notification_outbox",
        advisory_wait_seconds=10,
    )
    assert asyncio.run(worker.dispatch_once(now=created_at + 9)) is None
    outcome = asyncio.run(worker.dispatch_once(now=created_at + 10))
    assert outcome is not None and outcome.state == "DELIVERED"
    assert len(transport.calls) == 1
    with sqlite3.connect(db_path) as conn:
        advisory = conn.execute(
            "SELECT status,provider,model,advisory_json "
            "FROM entry_decision_advisories WHERE decision_event_id=1"
        ).fetchone()
    assert advisory is not None
    assert advisory[:3] == ("UNAVAILABLE", "none", "none")
    assert json.loads(advisory[3])["ai_status"] == "UNAVAILABLE"


def test_telegram_read_timeout_is_delivery_uncertain_but_connect_timeout_is_retryable() -> None:
    payload = {
        "contract_version": "entry_ready_notification_v1",
        "symbol": "SXT/USDT:USDT",
        "decision_packet": entry_packet(),
    }
    event = {"event_id": "entry:1:ready", "payload_json": json.dumps(payload)}

    async def run(exc):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise exc("timeout", request=request)
        transport = TelegramSignalTransport(
            "token", "123",
            max_entry_age_seconds=10_000_000_000,
            http_transport=httpx.MockTransport(handler),
        )
        return await transport.deliver(event)

    read = asyncio.run(run(httpx.ReadTimeout))
    connect = asyncio.run(run(httpx.ConnectTimeout))
    assert read.disposition is DeliveryDisposition.DELIVERY_UNCERTAIN
    assert read.error_code == "TELEGRAM_READ_TIMEOUT_AFTER_SEND_MAY_HAVE_STARTED"
    assert connect.disposition is DeliveryDisposition.TRANSIENT_FAILURE
    assert connect.error_code == "TELEGRAM_CONNECT_TIMEOUT"


def test_telegram_transport_runs_decision_store_reads_off_event_loop(monkeypatch) -> None:
    main_thread = threading.get_ident()
    worker_threads: list[int] = []
    payload = {
        "contract_version": "entry_ready_notification_v1",
        "symbol": "SXT/USDT:USDT",
        "decision_event_id": 1,
        "decision_packet": entry_packet(),
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    transport = TelegramSignalTransport(
        "token", "123", http_transport=httpx.MockTransport(handler),
        max_entry_age_seconds=10_000_000_000,
    )

    def deliverable(_payload, *, now):
        worker_threads.append(threading.get_ident())
        return True, None

    def advisory(_event_id):
        worker_threads.append(threading.get_ident())
        return None

    monkeypatch.setattr(transport, "_current_entry_ready_is_deliverable", deliverable)
    monkeypatch.setattr(transport, "_load_advisory_for_decision", advisory)
    result = asyncio.run(transport.deliver({
        "event_id": "entry:1:ready",
        "payload_json": json.dumps(payload),
    }))
    assert result.disposition is DeliveryDisposition.DELIVERED
    assert len(worker_threads) == 2
    assert all(thread_id != main_thread for thread_id in worker_threads)


def test_advisory_grace_failure_does_not_block_entry_delivery(tmp_path, monkeypatch) -> None:
    db_path = migrate_test_database(tmp_path / "advisory-grace-nonblocking.db")
    EntryDecisionStore(db_path).append_if_changed("SXT/USDT:USDT", entry_packet())
    with sqlite3.connect(db_path) as conn:
        created_at = conn.execute(
            "SELECT created_at FROM entry_notification_outbox"
        ).fetchone()[0]

    def fail_advisory(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(EntryDecisionStore, "ensure_unavailable_advisory", fail_advisory)
    transport = FakeTransport()
    worker = DurableNotificationWorker(
        db_path, transport, worker_id="entry-advisory-nonblocking",
        outbox_table="entry_notification_outbox", advisory_wait_seconds=10,
    )
    outcome = asyncio.run(worker.dispatch_once(now=created_at + 10))
    assert outcome is not None and outcome.state == "DELIVERED"
    assert len(transport.calls) == 1
