from pathlib import Path


def test_websocket_orphan_subscription_alert_accounts_for_shared_fanout() -> None:
    alerts = (
        Path(__file__).resolve().parents[2] / "deploy" / "prometheus" / "alerts.yml"
    ).read_text(encoding="utf-8")

    assert (
        "waterfall_websocket_ccxt_subscriptions > "
        "waterfall_websocket_active_tasks + "
        "3 * waterfall_websocket_shared_evidence_active_subscribers + 8"
    ) in alerts


def test_shared_websocket_blocked_retirement_alert_is_current_state_based() -> None:
    alerts = (
        Path(__file__).resolve().parents[2] / "deploy" / "prometheus" / "alerts.yml"
    ).read_text(encoding="utf-8")

    assert "alert: WaterfallSharedWebSocketRetirementBlocked" in alerts
    assert "waterfall_websocket_shared_evidence_blocked_exchanges > 0" in alerts
    assert "waterfall_websocket_shared_evidence_retirement_failures > 0" not in alerts


def test_websocket_retirement_backlog_counts_shared_reconcile_work() -> None:
    alerts = (
        Path(__file__).resolve().parents[2] / "deploy" / "prometheus" / "alerts.yml"
    ).read_text(encoding="utf-8")

    assert (
        "waterfall_websocket_direct_exchange_retire_tasks + "
        "waterfall_websocket_liquidation_exchange_retire_tasks + "
        "waterfall_websocket_shared_evidence_reconcile_tasks > 8"
    ) in alerts


def test_direct_idle_client_alert_targets_ownerless_direct_clients() -> None:
    alerts = (
        Path(__file__).resolve().parents[2] / "deploy" / "prometheus" / "alerts.yml"
    ).read_text(encoding="utf-8")

    assert "alert: WaterfallWebSocketDirectIdleClients" in alerts
    assert "waterfall_websocket_direct_idle_ccxt_clients > 0" in alerts


def test_persistent_exchange_close_finalizer_alerts_on_single_stuck_close() -> None:
    alerts = (
        Path(__file__).resolve().parents[2] / "deploy" / "prometheus" / "alerts.yml"
    ).read_text(encoding="utf-8")

    assert "alert: WaterfallWebSocketExchangeCloseFinalizerStuck" in alerts
    assert "waterfall_websocket_exchange_close_finalizer_tasks > 0" in alerts
    assert "for: 2m" in alerts
