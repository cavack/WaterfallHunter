import asyncio
import math
import time

from waterfallhunter import main


def test_signal_evidence_metrics_use_observational_report(
    monkeypatch,
):
    monkeypatch.setattr(
        main.execution_outcome_report,
        "build_report",
        lambda: {
            "settlement": {
                "signal_count": 12,
                "settled_outcome_count": 9,
                "unsettled_mature_signal_count": 2,
                "oldest_unsettled_mature_age_seconds": 3600,
                "mature_settlement_coverage_rate": 0.75,
            },
            "evidence": {
                "decisive_outcome_count": 7,
                "ready": False,
                "observation_span_days": 3.5,
            },
            "by_proxy_execution_comparison": {
                "AGREE_ACCEPT": {
                    "signal_count": 5,
                    "decisive_outcome_count": 3,
                },
                "VOLUME_PASS_EXECUTION_REJECT": {
                    "signal_count": 2,
                    "decisive_outcome_count": 1,
                },
                "UNBOUNDED": {
                    "signal_count": 999,
                    "decisive_outcome_count": 999,
                },
            },
        },
    )

    asyncio.run(
        main._update_signal_evidence_metrics(
            force=True
        )
    )

    assert main.signal_ledger_metric._value.get() == 12
    assert main.signal_outcome_metric._value.get() == 9
    assert main.signal_mature_pending_metric._value.get() == 2
    assert main.signal_decisive_outcome_metric._value.get() == 7
    assert main.signal_evidence_ready_metric._value.get() == 0
    assert main.signal_evidence_span_metric._value.get() == 3.5
    assert main.signal_settlement_coverage_metric._value.get() == 0.75
    assert main.signal_oldest_mature_pending_age_metric._value.get() == 3600
    assert (
        main.signal_proxy_execution_metric
        .labels(comparison="AGREE_ACCEPT")
        ._value.get()
        == 5
    )
    assert (
        main.signal_proxy_execution_decisive_metric
        .labels(comparison="AGREE_ACCEPT")
        ._value.get()
        == 3
    )
    assert (
        main.signal_proxy_execution_metric
        .labels(comparison="VOLUME_PASS_EXECUTION_REJECT")
        ._value.get()
        == 2
    )
    assert (
        "UNBOUNDED",
    ) not in main.signal_proxy_execution_metric._metrics


def test_empty_mature_population_exports_nan_coverage(
    monkeypatch,
):
    monkeypatch.setattr(
        main.execution_outcome_report,
        "build_report",
        lambda: {
            "settlement": {
                "signal_count": 0,
                "settled_outcome_count": 0,
                "unsettled_mature_signal_count": 0,
                "oldest_unsettled_mature_age_seconds": None,
                "mature_settlement_coverage_rate": None,
            },
            "evidence": {
                "decisive_outcome_count": 0,
                "ready": False,
                "observation_span_days": 0,
            },
            "by_proxy_execution_comparison": {},
        },
    )

    asyncio.run(
        main._update_signal_evidence_metrics(
            force=True
        )
    )

    assert math.isnan(
        main.signal_settlement_coverage_metric._value.get()
    )
    assert math.isnan(
        main.signal_oldest_mature_pending_age_metric._value.get()
    )
    for comparison in main._PROXY_EXECUTION_METRIC_COMPARISONS:
        assert (
            main.signal_proxy_execution_metric
            .labels(comparison=comparison)
            ._value.get()
            == 0
        )


def test_signal_settlement_worker_metrics_export_health(monkeypatch):
    class Worker:
        @staticmethod
        def health_snapshot():
            return {
                "running": True,
                "total_cycles": 8,
                "total_failures": 2,
                "last_completed_at": 1234.0,
                "last_error_at": 1200.0,
            }

    monkeypatch.setattr(main, "_signal_settlement_worker", Worker())

    main._update_signal_settlement_worker_metrics()

    assert main.signal_settlement_worker_running_metric._value.get() == 1
    assert main.signal_settlement_cycles_metric._value.get() == 8
    assert main.signal_settlement_failures_metric._value.get() == 2
    assert main.signal_settlement_last_completed_metric._value.get() == 1234
    assert main.signal_settlement_last_error_metric._value.get() == 1200


def test_signal_evidence_report_refresh_is_cached(
    monkeypatch,
):
    main._signal_evidence_metrics_last_refresh = time.monotonic()

    def forbidden_report():
        raise AssertionError(
            "cached scrape must not query the outcome report"
        )

    monkeypatch.setattr(
        main.execution_outcome_report,
        "build_report",
        forbidden_report,
    )

    asyncio.run(
        main._update_signal_evidence_metrics()
    )


def test_candidate_state_metric_has_fixed_cardinality():
    main._update_candidate_state_metrics(
        {
            "A": {"status": "WATCH"},
            "B": {"status": "PRE-TRIGGER"},
            "C": {"status": "PRE-TRIGGER"},
            "D": {"status": "UNEXPECTED"},
            "E": "invalid",
        }
    )

    expected = {
        "WATCH": 1,
        "FUEL-RICH": 0,
        "PRE-TRIGGER": 2,
        "ARMED": 0,
        "TRIGGERED": 0,
    }
    for state, value in expected.items():
        assert (
            main.candidate_state_metric
            .labels(state=state)
            ._value.get()
            == value
        )
