from schema_test_support import migrate_test_database
from waterfallhunter.core.db import (
    DBAdapter,
)
from waterfallhunter.core.lbank_execution_stats import (
    LBankExecutionStats,
)
from waterfallhunter.core.lbank_execution_store import (
    LBankExecutionStore,
)
from waterfallhunter.core.lbank_execution_suitability_report import (
    LBankExecutionSuitabilityReport,
)


def packet(
    *,
    observed_at,
    spread,
    cost100,
    depth25,
):
    return {
        "available": True,
        "observed_at": observed_at,
        "spread_pct": spread,
        "depth": {
            "bounded": {
                "10": {
                    "minimum_side_depth_usdt": (
                        depth25 / 2
                    ),
                },
                "25": {
                    "minimum_side_depth_usdt": (
                        depth25
                    ),
                },
                "50": {
                    "minimum_side_depth_usdt": (
                        depth25 * 2
                    ),
                },
                "100": {
                    "minimum_side_depth_usdt": (
                        depth25 * 4
                    ),
                },
            }
        },
        "execution": {
            "25": {
                "effective_crossing_cost_pct": (
                    cost100
                ),
            },
            "50": {
                "effective_crossing_cost_pct": (
                    cost100
                ),
            },
            "100": {
                "effective_crossing_cost_pct": (
                    cost100
                ),
            },
        },
    }


def seed_symbol(
    store,
    symbol,
    *,
    spread,
    cost100,
    depth25,
):
    for observed_at in (
        1000.0,
        2800.0,
        4600.0,
        6400.0,
        8200.0,
    ):
        store.record_observation(
            symbol,
            packet(
                observed_at=observed_at,
                spread=spread,
                cost100=cost100,
                depth25=depth25,
            ),
        )


def build_report(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )
    db_path = migrate_test_database(db_path)

    DBAdapter(
        str(
            db_path
        )
    )

    store = LBankExecutionStore(
        str(
            db_path
        )
    )

    seed_symbol(
        store,
        "GOOD/USDT:USDT",
        spread=0.05,
        cost100=0.08,
        depth25=10_000.0,
    )

    seed_symbol(
        store,
        "MID/USDT:USDT",
        spread=0.15,
        cost100=0.20,
        depth25=2_000.0,
    )

    seed_symbol(
        store,
        "BAD/USDT:USDT",
        spread=2.0,
        cost100=2.5,
        depth25=20.0,
    )

    store.record_observation(
        "UNKNOWN/USDT:USDT",
        packet(
            observed_at=1000.0,
            spread=0.05,
            cost100=0.08,
            depth25=10_000.0,
        ),
    )

    stats = LBankExecutionStats(
        str(
            db_path
        )
    )

    return (
        LBankExecutionSuitabilityReport(
            stats
        )
    )


def test_report_counts_all_four_statuses(
    tmp_path,
):
    report = (
        build_report(
            tmp_path
        )
        .build_report()
    )

    assert (
        report[
            "status_counts"
        ][
            "SUITABLE"
        ]
        == 1
    )

    assert (
        report[
            "status_counts"
        ][
            "MARGINAL"
        ]
        == 1
    )

    assert (
        report[
            "status_counts"
        ][
            "POOR"
        ]
        == 1
    )

    assert (
        report[
            "status_counts"
        ][
            "UNKNOWN"
        ]
        == 1
    )


def test_report_is_explicitly_observational_only(
    tmp_path,
):
    report = (
        build_report(
            tmp_path
        )
        .build_report()
    )

    assert (
        report[
            "observational_only"
        ]
        is True
    )

    assert (
        report[
            "trade_eligible"
        ]
        is None
    )


def test_report_calculates_classification_rate(
    tmp_path,
):
    report = (
        build_report(
            tmp_path
        )
        .build_report()
    )

    assert (
        report[
            "symbol_count"
        ]
        == 4
    )

    assert (
        report[
            "known_classification_count"
        ]
        == 3
    )

    assert (
        report[
            "unknown_classification_count"
        ]
        == 1
    )

    assert (
        report[
            "classification_rate"
        ]
        == 0.75
    )


def test_poor_failure_breakdown_is_exposed(
    tmp_path,
):
    report = (
        build_report(
            tmp_path
        )
        .build_report()
    )

    failures = report[
        "poor_failed_check_counts"
    ]

    assert (
        failures[
            "cost100_p90"
        ]
        == 1
    )

    assert (
        failures[
            "spread_p90"
        ]
        == 1
    )

    assert (
        failures[
            "depth25_p50"
        ]
        == 1
    )


def test_report_contains_threshold_provenance_packet(
    tmp_path,
):
    report = (
        build_report(
            tmp_path
        )
        .build_report()
    )

    thresholds = report[
        "thresholds"
    ]

    assert (
        thresholds[
            "suitable"
        ][
            "maximum_cost_100_p90_pct"
        ]
        == 0.1225
    )

    assert (
        thresholds[
            "marginal"
        ][
            "maximum_cost_100_p90_pct"
        ]
        == 0.305
    )


def test_report_contains_execution_coverage(
    tmp_path,
):
    report = (
        build_report(
            tmp_path
        )
        .build_report()
    )

    coverage = report[
        "coverage"
    ]

    assert (
        coverage[
            "unique_symbols"
        ]
        == 4
    )

    assert (
        coverage[
            "history_rows"
        ]
        == 16
    )


def test_examples_are_limited_per_status(
    tmp_path,
):
    report = (
        build_report(
            tmp_path
        )
        .build_report(
            examples_per_status=0
        )
    )

    assert (
        report[
            "examples"
        ][
            "SUITABLE"
        ]
        == []
    )

    assert (
        report[
            "examples"
        ][
            "POOR"
        ]
        == []
    )


def test_classify_symbol_contains_evidence_metadata(
    tmp_path,
):
    builder = build_report(
        tmp_path
    )

    row = (
        builder
        .classify_symbol(
            "GOOD/USDT:USDT"
        )
    )

    assert (
        row[
            "status"
        ]
        == "SUITABLE"
    )

    assert (
        row[
            "observed_samples"
        ]
        == 5
    )

    assert (
        row[
            "observation_span_hours"
        ]
        == 2.0
    )

    assert (
        row[
            "availability_rate"
        ]
        == 1.0
    )


def test_report_does_not_change_catalogue_state(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )
    db_path = migrate_test_database(db_path)

    db = DBAdapter(
        str(
            db_path
        )
    )

    db.update_candidates(
        {
            "GOOD/USDT:USDT": {
                "last_price": 0.1,
                "quote_volume": 1000.0,
                "is_meme": True,
                "scan_eligible": False,
            }
        }
    )

    store = LBankExecutionStore(
        str(
            db_path
        )
    )

    seed_symbol(
        store,
        "GOOD/USDT:USDT",
        spread=0.05,
        cost100=0.08,
        depth25=10_000.0,
    )

    before = (
        db
        .get_catalog_symbols()
    )

    report = (
        LBankExecutionSuitabilityReport(
            LBankExecutionStats(
                str(
                    db_path
                )
            )
        )
    )

    report.build_report()

    after = (
        db
        .get_catalog_symbols()
    )

    assert before == after
