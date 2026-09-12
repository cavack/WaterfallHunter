from __future__ import annotations

import inspect
from pathlib import Path

import waterfallhunter.main as main


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_CONSUMERS = (
    ROOT / "src" / "waterfallhunter" / "core" / "lbank_signal_outcome.py",
    ROOT / "src" / "waterfallhunter" / "core" / "lbank_execution_outcome_report.py",
)


def test_canonical_consumers_have_no_metadata_or_strict_fallbacks() -> None:
    for path in CANONICAL_CONSUMERS:
        source = path.read_text(encoding="utf-8")
        assert "LEFT JOIN signal_metadata" not in source
        assert 'metrics.get("signal_class")' not in source
        assert 'metrics.get("strategy_profile")' not in source
        assert "COALESCE" not in source.upper() or "STRICT" not in source.upper()


def test_only_legacy_classifier_may_classify_from_historical_trigger_json() -> None:
    report_source = CANONICAL_CONSUMERS[1].read_text(encoding="utf-8")
    outcome_source = CANONICAL_CONSUMERS[0].read_text(encoding="utf-8")

    assert "trigger_metrics_json" not in report_source
    assert 'metrics.get("strategy_profile")' not in outcome_source
    assert 'metrics.get("signal_class")' not in outcome_source


def test_startup_gate_is_read_only_and_precedes_worker_creation() -> None:
    source = inspect.getsource(main.startup_event)
    schema_index = source.index("require_managed_schema")
    completeness_index = source.index("require_signal_metadata_completeness")
    worker_index = source.index("_start_background_task")

    assert schema_index < completeness_index < worker_index
    for forbidden in (
        "migrate_database",
        "MigrationRunner",
        "apply_legacy_classification",
        "preview_legacy_classification",
        "backfill",
        "repair",
    ):
        assert forbidden not in source
