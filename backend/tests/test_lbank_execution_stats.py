import sqlite3

import pytest

from schema_test_support import migrate_test_database
from waterfallhunter.core.db import (
    DBAdapter,
)
from waterfallhunter.core.lbank_execution_stats import (
    EVIDENCE_INSUFFICIENT,
    EVIDENCE_NO_EVIDENCE,
    EVIDENCE_SUFFICIENT,
    LBankExecutionStats,
)
from waterfallhunter.core.lbank_execution_store import (
    LBankExecutionStore,
)


def seed_catalog(
    db_path,
):
    migrate_test_database(db_path)
    db = DBAdapter(
        str(db_path)
    )

    db.update_candidates(
        {
            "MEME/USDT:USDT": {
                "last_price": 0.1,
                "quote_volume": 100_000,
                "is_meme": True,
                "scan_eligible": False,
            },
            "SECOND/USDT:USDT": {
                "last_price": 0.2,
                "quote_volume": 200_000,
                "is_meme": False,
                "scan_eligible": False,
            },
        }
    )


def packet(
    *,
    observed_at,
    spread,
    cost_25,
    cost_50,
    cost_100,
    depth_25,
):
    return {
        "available": True,
        "symbol": "MEME/USDT:USDT",
        "source_exchange": "lbank",
        "observed_at": observed_at,
        "spread_pct": spread,
        "depth": {
            "bounded": {
                "10": {
                    "minimum_side_depth_usdt": (
                        depth_25 / 2
                    ),
                },
                "25": {
                    "minimum_side_depth_usdt": (
                        depth_25
                    ),
                },
                "50": {
                    "minimum_side_depth_usdt": (
                        depth_25 * 2
                    ),
                },
                "100": {
                    "minimum_side_depth_usdt": (
                        depth_25 * 4
                    ),
                },
            }
        },
        "execution": {
            "25": {
                "effective_crossing_cost_pct": (
                    cost_25
                ),
            },
            "50": {
                "effective_crossing_cost_pct": (
                    cost_50
                ),
            },
            "100": {
                "effective_crossing_cost_pct": (
                    cost_100
                ),
            },
        },
    }


def build_history(
    db_path,
):
    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    for observed_at, spread, cost_100, depth in (
        (
            1000.0,
            0.01,
            0.04,
            100.0,
        ),
        (
            2000.0,
            0.02,
            0.08,
            200.0,
        ),
        (
            3000.0,
            0.03,
            0.12,
            300.0,
        ),
        (
            4000.0,
            0.04,
            0.16,
            400.0,
        ),
    ):
        store.record_observation(
            "MEME/USDT:USDT",
            packet(
                observed_at=(
                    observed_at
                ),
                spread=spread,
                cost_25=(
                    cost_100 / 2
                ),
                cost_50=(
                    cost_100 * 0.75
                ),
                cost_100=(
                    cost_100
                ),
                depth_25=depth,
            ),
        )

    store.record_observation(
        "MEME/USDT:USDT",
        {
            "available": False,
            "symbol": "MEME/USDT:USDT",
            "reason": (
                "temporary unavailable"
            ),
        },
    )

    return store


def test_metric_summary_reuses_one_ordered_sample(monkeypatch):
    def forbidden_percentile(*_args, **_kwargs):
        raise AssertionError("metric summary must not re-clean and re-sort per percentile")

    monkeypatch.setattr(LBankExecutionStats, "percentile", forbidden_percentile)
    summary = LBankExecutionStats._metric_summary([4.0, 1.0, 3.0, 2.0])

    assert summary["count"] == 4
    assert summary["min"] == 1.0
    assert summary["max"] == 4.0
    assert summary["mean"] == 2.5
    assert summary["p10"] == pytest.approx(1.3)
    assert summary["p50"] == pytest.approx(2.5)
    assert summary["p90"] == pytest.approx(3.7)


def test_percentile_uses_linear_interpolation():
    assert (
        LBankExecutionStats.percentile(
            [
                1.0,
                2.0,
                3.0,
                4.0,
            ],
            50,
        )
        == 2.5
    )

    assert (
        LBankExecutionStats.percentile(
            [
                1.0,
                2.0,
                3.0,
                4.0,
            ],
            90,
        )
        == pytest.approx(
            3.7
        )
    )


def test_statistics_count_observed_and_unavailable_rows(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    build_history(
        db_path
    )

    stats = LBankExecutionStats(
        str(db_path)
    )

    result = stats.summarize_symbol(
        "MEME/USDT:USDT"
    )

    assert (
        result["observation_count"]
        == 5
    )

    assert (
        result["observed_count"]
        == 4
    )

    assert (
        result["unavailable_count"]
        == 1
    )

    assert (
        result["availability_rate"]
        == pytest.approx(
            0.8
        )
    )


def test_statistics_calculate_cost_percentiles(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    build_history(
        db_path
    )

    stats = LBankExecutionStats(
        str(db_path)
    )

    result = stats.summarize_symbol(
        "MEME/USDT:USDT"
    )

    cost_100 = result[
        "metrics"
    ][
        "cost_100_pct"
    ]

    assert (
        cost_100["count"]
        == 4
    )

    assert (
        cost_100["p50"]
        == pytest.approx(
            0.10
        )
    )

    assert (
        cost_100["p90"]
        == pytest.approx(
            0.148
        )
    )

    assert (
        cost_100[
            "p90_minus_p50"
        ]
        == pytest.approx(
            0.048
        )
    )

    assert (
        cost_100[
            "p90_to_p50_ratio"
        ]
        == pytest.approx(
            1.48
        )
    )


def test_statistics_calculate_depth_distribution(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    build_history(
        db_path
    )

    stats = LBankExecutionStats(
        str(db_path)
    )

    result = stats.summarize_symbol(
        "MEME/USDT:USDT"
    )

    depth = result[
        "metrics"
    ][
        "depth_25bps_min_usdt"
    ]

    assert (
        depth["count"]
        == 4
    )

    assert (
        depth["p10"]
        == pytest.approx(
            130.0
        )
    )

    assert (
        depth["p50"]
        == pytest.approx(
            250.0
        )
    )

    assert (
        depth["p90"]
        == pytest.approx(
            370.0
        )
    )


def test_since_filter_limits_statistics_window(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    for observed_at in (
        1000.0,
        2000.0,
        3000.0,
        4000.0,
    ):
        store.record_observation(
            "MEME/USDT:USDT",
            packet(
                observed_at=(
                    observed_at
                ),
                spread=0.01,
                cost_25=0.02,
                cost_50=0.03,
                cost_100=0.04,
                depth_25=100.0,
            ),
        )

    stats = LBankExecutionStats(
        str(db_path)
    )

    result = stats.summarize_symbol(
        "MEME/USDT:USDT",
        since=3000.0,
    )

    assert (
        result["observation_count"]
        == 2
    )

    assert (
        result["first_observed_at"]
        == 3000.0
    )

    assert (
        result["last_observed_at"]
        == 4000.0
    )

    assert (
        result[
            "observation_span_seconds"
        ]
        == 1000.0
    )


def test_missing_metric_values_are_not_fabricated(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    store.record_observation(
        "MEME/USDT:USDT",
        {
            "available": True,
            "observed_at": 1000.0,
            "spread_pct": 0.05,
        },
    )

    stats = LBankExecutionStats(
        str(db_path)
    )

    result = stats.summarize_symbol(
        "MEME/USDT:USDT"
    )

    assert (
        result["metrics"][
            "spread_pct"
        ]["count"]
        == 1
    )

    assert (
        result["metrics"][
            "cost_100_pct"
        ]["count"]
        == 0
    )

    assert (
        result["metrics"][
            "cost_100_pct"
        ]["p50"]
        is None
    )


def test_statistics_layer_does_not_mutate_catalog_or_latest_state(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    build_history(
        db_path
    )

    with sqlite3.connect(
        str(db_path)
    ) as conn:
        before_catalog = conn.execute(
            """
            SELECT
                scan_eligible,
                status
            FROM lbank_catalog
            WHERE symbol = ?
            """,
            (
                "MEME/USDT:USDT",
            ),
        ).fetchone()

        before_latest = conn.execute(
            """
            SELECT
                observation_status,
                observed_at,
                failures
            FROM lbank_execution_observations
            WHERE symbol = ?
            """,
            (
                "MEME/USDT:USDT",
            ),
        ).fetchone()

    stats = LBankExecutionStats(
        str(db_path)
    )

    stats.summarize_symbol(
        "MEME/USDT:USDT"
    )

    stats.list_symbols()

    stats.coverage_summary(
        now=5000.0
    )

    with sqlite3.connect(
        str(db_path)
    ) as conn:
        after_catalog = conn.execute(
            """
            SELECT
                scan_eligible,
                status
            FROM lbank_catalog
            WHERE symbol = ?
            """,
            (
                "MEME/USDT:USDT",
            ),
        ).fetchone()

        after_latest = conn.execute(
            """
            SELECT
                observation_status,
                observed_at,
                failures
            FROM lbank_execution_observations
            WHERE symbol = ?
            """,
            (
                "MEME/USDT:USDT",
            ),
        ).fetchone()

    assert (
        after_catalog
        == before_catalog
    )

    assert (
        after_latest
        == before_latest
    )


def test_symbol_summary_exposes_coverage_age_and_span(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    for observed_at in (
        1000.0,
        4600.0,
    ):
        store.record_observation(
            "MEME/USDT:USDT",
            packet(
                observed_at=(
                    observed_at
                ),
                spread=0.01,
                cost_25=0.02,
                cost_50=0.03,
                cost_100=0.04,
                depth_25=100.0,
            ),
        )

    stats = LBankExecutionStats(
        str(db_path)
    )

    result = stats.summarize_symbol(
        "MEME/USDT:USDT"
    )

    assert (
        result[
            "observation_span_seconds"
        ]
        == 3600.0
    )

    assert (
        result[
            "observation_span_hours"
        ]
        == pytest.approx(
            1.0
        )
    )

    assert (
        result[
            "last_observation_age_seconds"
        ]
        is not None
    )


def test_coverage_summary_reports_distribution_and_thresholds(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    for observed_at in (
        1000.0,
        2000.0,
        3000.0,
    ):
        store.record_observation(
            "MEME/USDT:USDT",
            packet(
                observed_at=(
                    observed_at
                ),
                spread=0.01,
                cost_25=0.02,
                cost_50=0.03,
                cost_100=0.04,
                depth_25=100.0,
            ),
        )

    store.record_observation(
        "SECOND/USDT:USDT",
        {
            "available": False,
            "symbol": (
                "SECOND/USDT:USDT"
            ),
            "reason": "unavailable",
        },
    )

    stats = LBankExecutionStats(
        str(db_path)
    )

    result = stats.coverage_summary(
        now=5000.0
    )

    assert (
        result["history_rows"]
        == 4
    )

    assert (
        result["unique_symbols"]
        == 2
    )

    assert (
        result["observed_rows"]
        == 3
    )

    assert (
        result["unavailable_rows"]
        == 1
    )

    assert (
        result["availability_rate"]
        == pytest.approx(
            0.75
        )
    )

    assert (
        result[
            "observation_count_distribution"
        ]
        == {
            "1": 1,
            "3": 1,
        }
    )

    assert (
        result[
            "observed_count_distribution"
        ]
        == {
            "0": 1,
            "3": 1,
        }
    )

    assert (
        result[
            "coverage_thresholds"
        ]["1"]
        == 1
    )

    assert (
        result[
            "coverage_thresholds"
        ]["2"]
        == 1
    )

    assert (
        result[
            "coverage_thresholds"
        ]["3"]
        == 1
    )

    assert (
        result[
            "coverage_thresholds"
        ]["5"]
        == 0
    )

    assert (
        result[
            "max_observations_per_symbol"
        ]
        == 3
    )

    assert (
        result[
            "max_observed_samples_per_symbol"
        ]
        == 3
    )


def test_coverage_summary_reports_temporal_diversity(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    for observed_at in (
        1000.0,
        4600.0,
    ):
        store.record_observation(
            "MEME/USDT:USDT",
            packet(
                observed_at=(
                    observed_at
                ),
                spread=0.01,
                cost_25=0.02,
                cost_50=0.03,
                cost_100=0.04,
                depth_25=100.0,
            ),
        )

    stats = LBankExecutionStats(
        str(db_path)
    )

    result = stats.coverage_summary(
        now=5000.0
    )

    assert (
        result["max_span_hours"]
        == pytest.approx(
            1.0
        )
    )

    assert (
        result["median_span_hours"]
        == pytest.approx(
            1.0
        )
    )

    assert (
        result["latest_observation_at"]
        == 4600.0
    )

    assert (
        result[
            "latest_observation_age_seconds"
        ]
        == pytest.approx(
            400.0
        )
    )


def test_empty_coverage_summary_is_explicit(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    LBankExecutionStore(
        str(db_path)
    )

    stats = LBankExecutionStats(
        str(db_path)
    )

    result = stats.coverage_summary(
        now=5000.0
    )

    assert (
        result["history_rows"]
        == 0
    )

    assert (
        result["unique_symbols"]
        == 0
    )

    assert (
        result["availability_rate"]
        is None
    )

    assert (
        result[
            "max_observations_per_symbol"
        ]
        == 0
    )

    assert (
        result[
            "median_span_hours"
        ]
        is None
    )


def test_evidence_no_evidence_requires_successful_observation():
    result = (
        LBankExecutionStats
        .evidence_sufficiency(
            observed_count=0,
            observation_span_hours=None,
        )
    )

    assert (
        result["status"]
        == EVIDENCE_NO_EVIDENCE
    )

    assert (
        result[
            "samples_requirement_met"
        ]
        is False
    )

    assert (
        result[
            "span_requirement_met"
        ]
        is False
    )


def test_evidence_insufficient_when_sample_count_is_too_low():
    result = (
        LBankExecutionStats
        .evidence_sufficiency(
            observed_count=2,
            observation_span_hours=10.0,
        )
    )

    assert (
        result["status"]
        == EVIDENCE_INSUFFICIENT
    )

    assert (
        result[
            "samples_requirement_met"
        ]
        is False
    )

    assert (
        result[
            "span_requirement_met"
        ]
        is True
    )

    assert (
        result[
            "missing_observed_samples"
        ]
        == 3
    )


def test_evidence_insufficient_when_temporal_span_is_too_short():
    result = (
        LBankExecutionStats
        .evidence_sufficiency(
            observed_count=5,
            observation_span_hours=1.0,
        )
    )

    assert (
        result["status"]
        == EVIDENCE_INSUFFICIENT
    )

    assert (
        result[
            "samples_requirement_met"
        ]
        is True
    )

    assert (
        result[
            "span_requirement_met"
        ]
        is False
    )

    assert (
        result[
            "missing_span_hours"
        ]
        == pytest.approx(
            1.0
        )
    )


def test_evidence_sufficient_requires_samples_and_temporal_span():
    result = (
        LBankExecutionStats
        .evidence_sufficiency(
            observed_count=5,
            observation_span_hours=2.0,
        )
    )

    assert (
        result["status"]
        == EVIDENCE_SUFFICIENT
    )

    assert (
        result[
            "samples_requirement_met"
        ]
        is True
    )

    assert (
        result[
            "span_requirement_met"
        ]
        is True
    )

    assert (
        result["reasons"]
        == []
    )


def test_symbol_summary_contains_evidence_contract(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    for observed_at in (
        1000.0,
        2800.0,
        4600.0,
        6400.0,
        8200.0,
    ):
        store.record_observation(
            "MEME/USDT:USDT",
            packet(
                observed_at=(
                    observed_at
                ),
                spread=0.01,
                cost_25=0.02,
                cost_50=0.03,
                cost_100=0.04,
                depth_25=100.0,
            ),
        )

    stats = LBankExecutionStats(
        str(db_path)
    )

    result = stats.summarize_symbol(
        "MEME/USDT:USDT"
    )

    assert (
        result[
            "observed_count"
        ]
        == 5
    )

    assert (
        result[
            "observation_span_hours"
        ]
        == pytest.approx(
            2.0
        )
    )

    assert (
        result[
            "evidence"
        ][
            "status"
        ]
        == EVIDENCE_SUFFICIENT
    )


def test_unavailable_attempts_do_not_fake_sufficient_evidence(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    for _ in range(
        10
    ):
        store.record_observation(
            "SECOND/USDT:USDT",
            {
                "available": False,
                "symbol": (
                    "SECOND/USDT:USDT"
                ),
                "reason": "unavailable",
            },
        )

    stats = LBankExecutionStats(
        str(db_path)
    )

    result = stats.summarize_symbol(
        "SECOND/USDT:USDT"
    )

    assert (
        result[
            "observation_count"
        ]
        == 10
    )

    assert (
        result[
            "observed_count"
        ]
        == 0
    )

    assert (
        result[
            "evidence"
        ][
            "status"
        ]
        == EVIDENCE_NO_EVIDENCE
    )


def test_coverage_summary_counts_evidence_statuses(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    for observed_at in (
        1000.0,
        2800.0,
        4600.0,
        6400.0,
        8200.0,
    ):
        store.record_observation(
            "MEME/USDT:USDT",
            packet(
                observed_at=(
                    observed_at
                ),
                spread=0.01,
                cost_25=0.02,
                cost_50=0.03,
                cost_100=0.04,
                depth_25=100.0,
            ),
        )

    store.record_observation(
        "SECOND/USDT:USDT",
        {
            "available": False,
            "symbol": (
                "SECOND/USDT:USDT"
            ),
            "reason": "unavailable",
        },
    )

    stats = LBankExecutionStats(
        str(db_path)
    )

    result = stats.coverage_summary(
        now=9000.0
    )

    assert (
        result[
            "evidence_status_counts"
        ][
            EVIDENCE_SUFFICIENT
        ]
        == 1
    )

    assert (
        result[
            "evidence_status_counts"
        ][
            EVIDENCE_NO_EVIDENCE
        ]
        == 1
    )

    assert (
        result[
            "evidence_status_counts"
        ][
            EVIDENCE_INSUFFICIENT
        ]
        == 0
    )


def test_suitability_bulk_summary_matches_full_required_metrics(tmp_path):
    db_path = tmp_path / "registry.db"
    build_history(db_path)
    stats = LBankExecutionStats(str(db_path))

    full = stats.summarize_symbols(["MEME/USDT:USDT"])[0]
    compact = stats.summarize_symbols_for_suitability(["MEME/USDT:USDT"])[0]

    for field in ("observation_count", "observed_count", "unavailable_count", "availability_rate"):
        assert compact[field] == full[field]
    assert compact["evidence"] == full["evidence"]
    for metric, percentile_name in (
        ("spread_pct", "p90"),
        ("cost_100_pct", "p90"),
        ("depth_25bps_min_usdt", "p50"),
    ):
        assert compact["metrics"][metric][percentile_name] == pytest.approx(
            full["metrics"][metric][percentile_name]
        )
