from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from schema_test_support import migrate_test_database
from signal_metadata_test_support import strict_signal_metadata
from waterfallhunter.core.db import DBAdapter
from waterfallhunter.core.lbank_signal_ledger import LBankSignalLedger
from waterfallhunter.core.notification_delivery import (
    DeliveryDisposition,
    DeliveryResult,
)
from waterfallhunter.core.notifier import TelegramNotifier
from waterfallhunter.core.signal_metadata import canonical_sha256


class CapturingNotifier(TelegramNotifier):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent: list[str] = []
        self.enabled = True
        self.token = "test-token"
        self.chat_id = "123"
        # This harness intentionally exercises the active delivery path.
        self.signal_delivery_cutover_at = 1
        self.signal_delivery_enabled = True

    async def _send_text_result(self, text: str) -> DeliveryResult:
        self.sent.append(text)
        return DeliveryResult(DeliveryDisposition.DELIVERED)


def _database(path: Path) -> DBAdapter:
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
        score=91.5,
        trigger_metrics={
            "position_setup": {
                "entry_price": 1.0,
                "stop_loss": 1.05,
                "take_profit_1": 0.95,
                "take_profit_2": 0.90,
            },
            "applied_leverage": 4,
            "ai_advisory": {
                "ai_provider": "none",
                "ai_advice": "UNKNOWN",
            },
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
    return db


def test_durable_worker_rebuilds_strict_signal_message_from_immutable_ledger(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "telegram-outbox.db")
    notifier = CapturingNotifier(db_adapter=db)

    outcome = asyncio.run(notifier.delivery_worker.dispatch_once(now=200))

    assert outcome is not None
    assert outcome.state == "DELIVERED"
    assert len(notifier.sent) == 1
    assert "NOTICE" in notifier.sent[0]
    assert "91.50/100" in notifier.sent[0]
    assert "No live order is placed" in notifier.sent[0]


def test_send_signal_alert_only_wakes_durable_delivery_and_never_posts_directly(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "telegram-wakeup.db")
    notifier = CapturingNotifier(db_adapter=db)

    asyncio.run(notifier.send_signal_alert("NOTICE/USDT:USDT", {"score": 91.5}))

    assert notifier.sent == []
    assert notifier.delivery_wakeup.is_set() is True


def test_experimental_event_is_acknowledged_without_telegram_send(tmp_path: Path) -> None:
    db = _database(tmp_path / "telegram-experimental.db")
    notifier = CapturingNotifier(db_adapter=db)
    payload = {
        "contract_version": "signal_confirmed_event_v1",
        "signal_id": 1,
        "symbol": "NOTICE/USDT:USDT",
        "signal_class": "EXPERIMENTAL",
        "strategy_profile": "experimental_pretrigger_v1",
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

    assert result.disposition is DeliveryDisposition.DELIVERED
    assert notifier.sent == []


def test_telegram_429_is_mapped_to_durable_rate_limit_retry() -> None:
    request = httpx.Request("POST", "https://api.telegram.org/bottest/sendMessage")
    response = httpx.Response(
        429,
        request=request,
        json={
            "ok": False,
            "error_code": 429,
            "parameters": {"retry_after": 7},
        },
    )

    result = TelegramNotifier._delivery_result_from_response(response)

    assert result.disposition is DeliveryDisposition.RATE_LIMITED
    assert result.retry_after_seconds == 7
    assert result.error_code == "HTTP_429"
