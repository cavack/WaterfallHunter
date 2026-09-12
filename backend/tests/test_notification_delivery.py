from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from schema_test_support import migrate_test_database
from signal_metadata_test_support import strict_signal_metadata
from waterfallhunter.core.db import DBAdapter
from waterfallhunter.core.lbank_signal_ledger import LBankSignalLedger
from waterfallhunter.core.notification_delivery import (
    DeliveryDisposition,
    DeliveryResult,
    DurableNotificationWorker,
    NotificationDeliveryError,
    notification_delivery_health,
)


class FakeTransport:
    def __init__(self, *results: DeliveryResult | Exception):
        self.results = list(results)
        self.calls: list[dict] = []

    async def deliver(self, event: dict) -> DeliveryResult:
        self.calls.append(event)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class HangingTransport:
    async def deliver(self, event: dict) -> DeliveryResult:
        del event
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def _database(path: Path) -> str:
    db_path = str(migrate_test_database(path))
    db = DBAdapter(db_path=db_path)
    symbol = "NOTICE/USDT:USDT"
    db.update_candidates(
        {
            symbol: {
                "last_price": 1.0,
                "quote_volume": 3_000_000.0,
                "is_meme": False,
                "scan_eligible": True,
            }
        }
    )
    assert db.update_candidate_state(symbol, "ARMED")
    signal_id = LBankSignalLedger(db_path).persist_trigger(
        symbol,
        "ARMED",
        score=90.0,
        trigger_metrics={
            "position_setup": {
                "entry_price": 1.0,
                "stop_loss": 1.05,
                "take_profit_1": 0.95,
                "take_profit_2": 0.90,
            }
        },
        execution_suitability={"status": "SUITABLE", "failed_checks": []},
        metadata=strict_signal_metadata(
            analysis_observed_at=90,
            reference_observed_at=90,
        ),
        triggered_at=100,
        metadata_created_at=100,
    )
    assert signal_id == 1
    return db_path


def _state(db_path: str) -> tuple:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT status, attempt_count, available_at, lease_owner, "
            "lease_expires_at, last_error_code FROM domain_outbox_events"
        ).fetchone()


def test_success_is_delivered_once_with_material_idempotency_key(tmp_path: Path) -> None:
    db_path = _database(tmp_path / "delivered.db")
    transport = FakeTransport(DeliveryResult(DeliveryDisposition.DELIVERED))
    worker = DurableNotificationWorker(
        db_path,
        transport,
        worker_id="worker-a",
        jitter=lambda: 0.0,
    )

    outcome = asyncio.run(worker.dispatch_once(now=200))

    assert outcome is not None
    assert outcome.state == "DELIVERED"
    assert _state(db_path) == ("DELIVERED", 1, 200, None, None, None)
    assert transport.calls[0]["event_id"] == transport.calls[0]["idempotency_key"]
    assert asyncio.run(worker.dispatch_once(now=201)) is None


def test_rate_limit_retries_then_ambiguous_timeout_becomes_uncertain(tmp_path: Path) -> None:
    db_path = _database(tmp_path / "retry.db")
    transport = FakeTransport(
        DeliveryResult(
            DeliveryDisposition.RATE_LIMITED,
            error_code="HTTP 429",
            retry_after_seconds=7,
        ),
        TimeoutError(),
    )
    worker = DurableNotificationWorker(
        db_path,
        transport,
        worker_id="worker-a",
        max_attempts=2,
        jitter=lambda: 0.0,
    )

    first = asyncio.run(worker.dispatch_once(now=200))
    assert first is not None
    assert first.state == "RETRY_WAIT"
    assert first.next_available_at == 207
    assert _state(db_path)[5] == "HTTP_429"
    assert asyncio.run(worker.dispatch_once(now=206)) is None

    second = asyncio.run(worker.dispatch_once(now=207))
    assert second is not None
    assert second.state == "DELIVERY_UNCERTAIN"
    assert second.error_code == "TRANSPORT_TIMEOUT_AFTER_SEND_MAY_HAVE_STARTED"
    assert _state(db_path)[0:2] == ("DELIVERY_UNCERTAIN", 2)
    assert asyncio.run(worker.dispatch_once(now=500)) is None
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lbank_signal_ledger").fetchone() == (1,)


def test_lease_prevents_double_claim_and_expiry_becomes_delivery_uncertain(tmp_path: Path) -> None:
    db_path = _database(tmp_path / "uncertain.db")
    transport = FakeTransport(DeliveryResult(DeliveryDisposition.DELIVERED))
    first = DurableNotificationWorker(
        db_path,
        transport,
        worker_id="worker-a",
        lease_seconds=10,
    )
    second = DurableNotificationWorker(
        db_path,
        transport,
        worker_id="worker-b",
        lease_seconds=10,
    )

    claimed = first.claim_next(now=200)
    assert claimed is not None
    assert second.claim_next(now=201) is None
    assert second.recover_expired_leases(now=210) == 1
    assert _state(db_path)[0] == "DELIVERY_UNCERTAIN"

    health = notification_delivery_health(db_path, now=500)
    assert health["healthy"] is False
    assert "NOTIFICATION_DELIVERY_UNCERTAIN_PRESENT" in health["alerts"]
    assert second.claim_next(now=500) is None


def test_pending_queue_lag_has_an_operator_alert(tmp_path: Path) -> None:
    db_path = _database(tmp_path / "lag.db")

    health = notification_delivery_health(db_path, now=401)

    assert health["oldest_pending_age_seconds"] == 301
    assert health["alerts"] == ["NOTIFICATION_QUEUE_LAG_HIGH"]


def test_hanging_transport_timeout_is_bounded_and_marked_uncertain(tmp_path: Path) -> None:
    db_path = _database(tmp_path / "timeout.db")
    worker = DurableNotificationWorker(
        db_path,
        HangingTransport(),
        worker_id="worker-timeout",
        transport_timeout_seconds=0.01,
        max_attempts=1,
    )

    outcome = asyncio.run(worker.dispatch_once(now=200))

    assert outcome is not None
    assert outcome.state == "DELIVERY_UNCERTAIN"
    assert outcome.error_code == "TRANSPORT_TIMEOUT_AFTER_SEND_MAY_HAVE_STARTED"
    assert asyncio.run(worker.dispatch_once(now=300)) is None


def test_completion_after_lease_recovery_fails_cas_and_preserves_uncertainty(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path / "lease-cas.db")
    transport = FakeTransport(DeliveryResult(DeliveryDisposition.DELIVERED))
    first = DurableNotificationWorker(db_path, transport, worker_id="worker-a", lease_seconds=10)
    second = DurableNotificationWorker(db_path, transport, worker_id="worker-b", lease_seconds=10)
    claimed = first.claim_next(now=200)
    assert claimed is not None
    assert second.recover_expired_leases(now=210) == 1

    delivered = DeliveryResult(DeliveryDisposition.DELIVERED)
    with pytest.raises(NotificationDeliveryError, match="DELIVERY_COMPLETION_CAS_FAILED"):
        first._complete(
            claimed,
            result=delivered,
            now=211,
        )

    assert _state(db_path)[0] == "DELIVERY_UNCERTAIN"


def test_permanent_transport_failure_dead_letters_without_retry(tmp_path: Path) -> None:
    db_path = _database(tmp_path / "permanent.db")
    worker = DurableNotificationWorker(
        db_path,
        FakeTransport(
            DeliveryResult(
                DeliveryDisposition.PERMANENT_FAILURE,
                error_code="INVALID_DESTINATION",
            )
        ),
        worker_id="worker-a",
    )

    outcome = asyncio.run(worker.dispatch_once(now=200))

    assert outcome is not None
    assert outcome.state == "DEAD_LETTER"
    assert outcome.error_code == "INVALID_DESTINATION"


def test_transport_timeout_becomes_delivery_uncertain_without_retry(tmp_path: Path) -> None:
    db_path = _database(tmp_path / "ambiguous-timeout.db")
    worker = DurableNotificationWorker(
        db_path,
        HangingTransport(),
        worker_id="worker-timeout-uncertain",
        transport_timeout_seconds=0.01,
        max_attempts=6,
    )

    outcome = asyncio.run(worker.dispatch_once(now=200))

    assert outcome is not None
    assert outcome.state == "DELIVERY_UNCERTAIN"
    assert outcome.error_code == "TRANSPORT_TIMEOUT_AFTER_SEND_MAY_HAVE_STARTED"
    assert _state(db_path)[0] == "DELIVERY_UNCERTAIN"
    assert asyncio.run(worker.dispatch_once(now=300)) is None
