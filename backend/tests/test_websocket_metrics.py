from __future__ import annotations

import waterfallhunter.main as main


class _GaugeCapture:
    def __init__(self) -> None:
        self.values: list[float] = []

    def set(self, value: float) -> None:
        self.values.append(float(value))


def test_update_websocket_metrics_exports_runtime_fanout(monkeypatch) -> None:
    diagnostics = {
        "active_tasks": 7,
        "liquidation_tasks": 3,
        "shared_liquidation_tasks": 1,
        "shared_liquidation_subscribers": 19,
        "shared_evidence_tasks": 3,
        "shared_evidence_subscribers": 43,
        "shared_evidence_active_subscribers": 37,
        "shared_evidence_exchange_instances": 2,
        "shared_evidence_reconcile_tasks": 1,
        "shared_evidence_retirement_failures": 4,
        "shared_evidence_blocked_exchanges": 1,
        "shared_evidence_direct_fallbacks": 6,
        "shared_evidence_generations": 9,
        "direct_exchange_instances": 2,
        "liquidation_exchange_instances": 1,
        "direct_exchange_retire_tasks": 4,
        "liquidation_exchange_retire_tasks": 1,
        "exchange_close_finalizer_tasks": 2,
        "exchange_close_timeouts": 5,
        "direct_ccxt_clients": 8,
        "direct_ccxt_subscriptions": 6,
        "direct_idle_ccxt_clients": 2,
        "liquidation_ccxt_clients": 2,
        "liquidation_ccxt_subscriptions": 1,
        "shared_evidence_ccxt_clients": 1,
        "shared_evidence_ccxt_subscriptions": 2,
        "ccxt_clients": 11,
        "ccxt_subscriptions": 9,
    }
    monkeypatch.setattr(
        main.validator.ws_manager,
        "runtime_diagnostics",
        lambda: diagnostics,
        raising=False,
    )
    active = _GaugeCapture()
    liquidation = _GaugeCapture()
    shared = _GaugeCapture()
    subscribers = _GaugeCapture()
    shared_evidence_tasks = _GaugeCapture()
    shared_evidence_subscribers = _GaugeCapture()
    shared_evidence_active_subscribers = _GaugeCapture()
    shared_evidence_instances = _GaugeCapture()
    shared_evidence_reconcile = _GaugeCapture()
    shared_evidence_retirement_failures = _GaugeCapture()
    shared_evidence_blocked = _GaugeCapture()
    shared_evidence_direct_fallbacks = _GaugeCapture()
    shared_evidence_generations = _GaugeCapture()
    direct_instances = _GaugeCapture()
    liquidation_instances = _GaugeCapture()
    direct_retire = _GaugeCapture()
    liquidation_retire = _GaugeCapture()
    close_finalizers = _GaugeCapture()
    close_timeouts = _GaugeCapture()
    direct_ccxt_clients = _GaugeCapture()
    direct_ccxt_subscriptions = _GaugeCapture()
    direct_idle_ccxt_clients = _GaugeCapture()
    liquidation_ccxt_clients = _GaugeCapture()
    liquidation_ccxt_subscriptions = _GaugeCapture()
    shared_evidence_ccxt_clients = _GaugeCapture()
    shared_evidence_ccxt_subscriptions = _GaugeCapture()
    ccxt_clients = _GaugeCapture()
    ccxt_subscriptions = _GaugeCapture()
    monkeypatch.setattr(main, "websocket_active_tasks_metric", active, raising=False)
    monkeypatch.setattr(main, "websocket_liquidation_tasks_metric", liquidation, raising=False)
    monkeypatch.setattr(main, "websocket_shared_liquidation_tasks_metric", shared, raising=False)
    monkeypatch.setattr(main, "websocket_shared_liquidation_subscribers_metric", subscribers, raising=False)
    monkeypatch.setattr(main, "websocket_shared_evidence_tasks_metric", shared_evidence_tasks, raising=False)
    monkeypatch.setattr(main, "websocket_shared_evidence_subscribers_metric", shared_evidence_subscribers, raising=False)
    monkeypatch.setattr(main, "websocket_shared_evidence_active_subscribers_metric", shared_evidence_active_subscribers, raising=False)
    monkeypatch.setattr(main, "websocket_shared_evidence_exchange_instances_metric", shared_evidence_instances, raising=False)
    monkeypatch.setattr(main, "websocket_shared_evidence_reconcile_tasks_metric", shared_evidence_reconcile, raising=False)
    monkeypatch.setattr(main, "websocket_shared_evidence_retirement_failures_metric", shared_evidence_retirement_failures, raising=False)
    monkeypatch.setattr(main, "websocket_shared_evidence_blocked_exchanges_metric", shared_evidence_blocked, raising=False)
    monkeypatch.setattr(main, "websocket_shared_evidence_direct_fallbacks_metric", shared_evidence_direct_fallbacks, raising=False)
    monkeypatch.setattr(main, "websocket_shared_evidence_generations_metric", shared_evidence_generations, raising=False)
    monkeypatch.setattr(main, "websocket_direct_exchange_instances_metric", direct_instances, raising=False)
    monkeypatch.setattr(main, "websocket_liquidation_exchange_instances_metric", liquidation_instances, raising=False)
    monkeypatch.setattr(main, "websocket_direct_exchange_retire_tasks_metric", direct_retire, raising=False)
    monkeypatch.setattr(main, "websocket_liquidation_exchange_retire_tasks_metric", liquidation_retire, raising=False)
    monkeypatch.setattr(main, "websocket_exchange_close_finalizer_tasks_metric", close_finalizers, raising=False)
    monkeypatch.setattr(main, "websocket_exchange_close_timeouts_metric", close_timeouts, raising=False)
    monkeypatch.setattr(main, "websocket_direct_ccxt_clients_metric", direct_ccxt_clients, raising=False)
    monkeypatch.setattr(main, "websocket_direct_ccxt_subscriptions_metric", direct_ccxt_subscriptions, raising=False)
    monkeypatch.setattr(main, "websocket_direct_idle_ccxt_clients_metric", direct_idle_ccxt_clients, raising=False)
    monkeypatch.setattr(main, "websocket_liquidation_ccxt_clients_metric", liquidation_ccxt_clients, raising=False)
    monkeypatch.setattr(main, "websocket_liquidation_ccxt_subscriptions_metric", liquidation_ccxt_subscriptions, raising=False)
    monkeypatch.setattr(main, "websocket_shared_evidence_ccxt_clients_metric", shared_evidence_ccxt_clients, raising=False)
    monkeypatch.setattr(main, "websocket_shared_evidence_ccxt_subscriptions_metric", shared_evidence_ccxt_subscriptions, raising=False)
    monkeypatch.setattr(main, "websocket_ccxt_clients_metric", ccxt_clients, raising=False)
    monkeypatch.setattr(main, "websocket_ccxt_subscriptions_metric", ccxt_subscriptions, raising=False)

    main._update_websocket_metrics()

    assert active.values == [7.0]
    assert liquidation.values == [3.0]
    assert shared.values == [1.0]
    assert subscribers.values == [19.0]
    assert shared_evidence_tasks.values == [3.0]
    assert shared_evidence_subscribers.values == [43.0]
    assert shared_evidence_active_subscribers.values == [37.0]
    assert shared_evidence_instances.values == [2.0]
    assert shared_evidence_reconcile.values == [1.0]
    assert shared_evidence_retirement_failures.values == [4.0]
    assert shared_evidence_blocked.values == [1.0]
    assert shared_evidence_direct_fallbacks.values == [6.0]
    assert shared_evidence_generations.values == [9.0]
    assert direct_instances.values == [2.0]
    assert liquidation_instances.values == [1.0]
    assert direct_retire.values == [4.0]
    assert liquidation_retire.values == [1.0]
    assert close_finalizers.values == [2.0]
    assert close_timeouts.values == [5.0]
    assert direct_ccxt_clients.values == [8.0]
    assert direct_ccxt_subscriptions.values == [6.0]
    assert direct_idle_ccxt_clients.values == [2.0]
    assert liquidation_ccxt_clients.values == [2.0]
    assert liquidation_ccxt_subscriptions.values == [1.0]
    assert shared_evidence_ccxt_clients.values == [1.0]
    assert shared_evidence_ccxt_subscriptions.values == [2.0]
    assert ccxt_clients.values == [11.0]
    assert ccxt_subscriptions.values == [9.0]
