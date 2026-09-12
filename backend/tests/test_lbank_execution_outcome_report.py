from fastapi import FastAPI
from fastapi.testclient import TestClient

from schema_test_support import migrate_test_database
from signal_metadata_test_support import strict_signal_metadata
from waterfallhunter.core.db import DBAdapter
from waterfallhunter.core.lbank_execution_outcome_report import (
    LBankExecutionOutcomeReport,
)
from waterfallhunter.core.lbank_signal_ledger import LBankSignalLedger
from waterfallhunter.core.lbank_signal_outcome import (
    LBankSignalOutcomeStore,
)
from waterfallhunter.routes_execution_outcomes import (
    build_execution_outcome_router,
)


def _database(tmp_path):
    path = tmp_path / "report.db"
    migrate_test_database(path)
    db_path = str(path)
    DBAdapter(db_path)
    LBankSignalLedger(db_path)
    LBankSignalOutcomeStore(db_path)
    return db_path


def _seed_outcome(
    db_path,
    *,
    index,
    execution_status,
    triggered_at,
    outcome_status,
    tp1=False,
    tp2=False,
    stop=False,
    mfe=1.0,
    mae=0.5,
    comparison_kind="AGREE_ACCEPT",
):
    symbol = f"R{index}/USDT:USDT"
    db = DBAdapter(db_path)
    db.update_candidates(
        {
            symbol: {
                "last_price": 100.0,
                "quote_volume": 3_000_000.0,
                "is_meme": False,
                "scan_eligible": True,
            }
        }
    )
    assert db.update_candidate_state(symbol, "ARMED")
    ledger = LBankSignalLedger(db_path)
    signal_id = ledger.persist_trigger(
        symbol,
        "ARMED",
        score=90.0,
        trigger_metrics={
            "exchange": "binance",
            "mapped_symbol": symbol,
            "position_setup": {
                "entry_price": 100.0,
                "stop_loss": 102.0,
                "take_profit_1": 98.0,
                "take_profit_2": 96.0,
            },
        },
        execution_suitability={
            "status": execution_status,
            "observational_only": True,
            "trade_eligible": None,
        },
        metadata=strict_signal_metadata(analysis_observed_at=triggered_at),
        quote_volume=3_000_000.0,
        volume_gate_passed=True,
        proxy_execution_disagreement=comparison_kind,
        triggered_at=triggered_at,
    )
    store = LBankSignalOutcomeStore(db_path)
    signal = store.pending_signals(
        mature_before=triggered_at,
        limit=100,
    )[-1]
    assert signal["id"] == signal_id
    event_at = triggered_at + 300
    assert store.append_outcome(
        signal,
        {
            "status": outcome_status,
            "observation_started_at": triggered_at + 60,
            "observation_ended_at": triggered_at + 86_460,
            "horizon_seconds": 86_400,
            "first_tp1_at": event_at if tp1 else None,
            "first_tp2_at": event_at if tp2 else None,
            "first_stop_at": event_at if stop else None,
            "min_price": 95.0,
            "max_price": 103.0,
            "mfe_pct": mfe,
            "mae_pct": mae,
            "observed_candles": 1440,
            "expected_candles": 1440,
            "details": {"test": True},
        },
        source_exchange="binance",
        source_mapped_symbol=symbol,
        resolved_at=triggered_at + 87_000,
    )


def test_empty_report_fails_closed_without_calibration(tmp_path):
    report = LBankExecutionOutcomeReport(
        _database(tmp_path)
    ).build_report(now=2_000_000)

    assert report["evidence"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert report["evidence"]["ready"] is False
    assert report["settlement"]["signal_count"] == 0
    assert report["comparative_metrics"] is None
    assert report["proxy_execution_comparative_metrics"] is None
    assert report["threshold_calibration_allowed"] is False
    assert report["hard_gating_allowed"] is False
    assert report["observational_only"] is True
    assert report["trade_eligible"] is None
    assert report["settlement"][
        "oldest_unsettled_mature_age_seconds"
    ] is None


def test_report_exposes_oldest_mature_backlog_age(tmp_path):
    db_path = _database(tmp_path)
    triggered_at = 1_000
    symbol = "PENDING/USDT:USDT"
    db = DBAdapter(db_path)
    db.update_candidates(
        {
            symbol: {
                "last_price": 100.0,
                "quote_volume": 3_000_000.0,
                "is_meme": False,
                "scan_eligible": True,
            }
        }
    )
    assert db.update_candidate_state(symbol, "ARMED")
    ledger = LBankSignalLedger(db_path)
    assert ledger.persist_trigger(
        symbol,
        "ARMED",
        score=90.0,
        trigger_metrics={
            "exchange": "binance",
            "mapped_symbol": symbol,
            "position_setup": {
                "entry_price": 100.0,
                "stop_loss": 102.0,
                "take_profit_1": 98.0,
                "take_profit_2": 96.0,
            },
        },
        execution_suitability={
            "status": "SUITABLE",
            "observational_only": True,
            "trade_eligible": None,
        },
        metadata=strict_signal_metadata(analysis_observed_at=triggered_at),
        triggered_at=triggered_at,
    ) == 1

    report = LBankExecutionOutcomeReport(db_path).build_report(
        now=triggered_at + 86_400 + 180 + 600
    )

    assert report["settlement"]["mature_signal_count"] == 1
    assert report["settlement"]["unsettled_mature_signal_count"] == 1
    assert report["settlement"][
        "oldest_unsettled_mature_age_seconds"
    ] == 600


def test_ambiguous_and_incomplete_outcomes_are_not_decisive(tmp_path):
    db_path = _database(tmp_path)
    _seed_outcome(
        db_path,
        index=1,
        execution_status="SUITABLE",
        triggered_at=1_000,
        outcome_status="AMBIGUOUS_INTRACANDLE_PATH",
        tp1=True,
        stop=True,
    )
    _seed_outcome(
        db_path,
        index=2,
        execution_status="MARGINAL",
        triggered_at=2_000,
        outcome_status="DATA_INCOMPLETE",
    )

    report = LBankExecutionOutcomeReport(
        db_path,
        minimum_decisive_outcomes=1,
        minimum_outcomes_per_status=1,
        minimum_span_days=0,
    ).build_report(now=200_000)

    assert report["evidence"]["decisive_outcome_count"] == 0
    assert report["by_execution_status"]["SUITABLE"][
        "decisive_outcome_count"
    ] == 0
    assert report["comparative_metrics"] is None


def test_sufficient_report_exposes_observed_rates_without_gating(tmp_path):
    db_path = _database(tmp_path)
    day = 86_400
    rows = [
        ("SUITABLE", 1_000, "TP2_FIRST", True, True, False),
        ("SUITABLE", 2 * day, "TP2_FIRST", True, True, False),
        ("MARGINAL", 2_000, "TP1_ONLY_24H", True, False, False),
        ("MARGINAL", 2 * day + 1_000, "NO_LEVEL_HIT_24H", False, False, False),
        ("POOR", 3_000, "STOP_FIRST", False, False, True),
        ("POOR", 2 * day + 2_000, "STOP_FIRST", False, False, True),
    ]
    for index, row in enumerate(rows, start=1):
        status, timestamp, outcome, tp1, tp2, stop = row
        _seed_outcome(
            db_path,
            index=index,
            execution_status=status,
            triggered_at=timestamp,
            outcome_status=outcome,
            tp1=tp1,
            tp2=tp2,
            stop=stop,
            mfe=float(index),
            mae=float(index) / 2,
        )

    report = LBankExecutionOutcomeReport(
        db_path,
        minimum_decisive_outcomes=6,
        minimum_outcomes_per_status=2,
        minimum_span_days=1,
    ).build_report(now=4 * day)

    assert report["evidence"]["ready"] is True
    assert report["evidence"]["failed_checks"] == []
    assert report["comparative_metrics"] is not None
    assert report["proxy_execution_comparative_metrics"] is not None
    assert report["by_proxy_execution_comparison"]["AGREE_ACCEPT"][
        "decisive_outcome_count"
    ] == 6
    assert report["by_execution_status"]["SUITABLE"][
        "tp2_observed_rate"
    ] == 1.0
    assert report["by_execution_status"]["MARGINAL"][
        "tp1_observed_rate"
    ] == 0.5
    assert report["by_execution_status"]["POOR"][
        "stop_observed_rate"
    ] == 1.0
    assert report["threshold_calibration_allowed"] is False
    assert report["hard_gating_allowed"] is False


def test_proxy_execution_outcomes_are_grouped_from_trigger_snapshot(tmp_path):
    db_path = _database(tmp_path)
    day = 86_400
    rows = [
        ("AGREE_ACCEPT", "TP2_FIRST", True, True, False),
        ("VOLUME_PASS_EXECUTION_REJECT", "STOP_FIRST", False, False, True),
    ]
    for index, row in enumerate(rows, start=1):
        comparison, outcome, tp1, tp2, stop = row
        _seed_outcome(
            db_path,
            index=index,
            execution_status=("SUITABLE" if index == 1 else "POOR"),
            triggered_at=index * day,
            outcome_status=outcome,
            tp1=tp1,
            tp2=tp2,
            stop=stop,
            comparison_kind=comparison,
        )

    report = LBankExecutionOutcomeReport(
        db_path,
        minimum_decisive_outcomes=2,
        minimum_outcomes_per_status=1,
        minimum_span_days=0,
    ).build_report(now=4 * day)

    assert report["by_proxy_execution_comparison"]["AGREE_ACCEPT"][
        "tp2_observed_rate"
    ] == 1.0
    assert report["by_proxy_execution_comparison"][
        "VOLUME_PASS_EXECUTION_REJECT"
    ]["stop_observed_rate"] == 1.0
    assert report["threshold_calibration_allowed"] is False
    assert report["hard_gating_allowed"] is False


def test_outcome_validation_api_is_read_only_and_parameter_locked(tmp_path):
    db_path = _database(tmp_path)
    app = FastAPI()
    app.include_router(
        build_execution_outcome_router(db_path)
    )
    client = TestClient(app)

    response = client.get(
        "/api/execution-outcome-validation"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["evidence"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert data["observational_only"] is True

    rejected = client.get(
        "/api/execution-outcome-validation?minimum=1"
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["allowed_parameters"] == []
