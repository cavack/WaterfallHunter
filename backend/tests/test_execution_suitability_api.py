import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from schema_test_support import migrate_test_database
from waterfallhunter.core.db import (
    DBAdapter,
)
from waterfallhunter.core.lbank_execution_store import (
    LBankExecutionStore,
)
from waterfallhunter.routes_execution_suitability import (
    build_execution_suitability_router,
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


def seed_sufficient(
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
        assert store.record_observation(
            symbol,
            packet(
                observed_at=observed_at,
                spread=spread,
                cost100=cost100,
                depth25=depth25,
            ),
        )


def build_client(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )
    migrate_test_database(db_path)

    db = DBAdapter(
        str(
            db_path
        )
    )

    db.update_candidates(
        {
            "GOOD/USDT:USDT": {
                "last_price": 0.1,
                "quote_volume": 3_000_000.0,
                "is_meme": True,
                "scan_eligible": True,
            },
            "MID/USDT:USDT": {
                "last_price": 0.1,
                "quote_volume": 3_000_000.0,
                "is_meme": True,
                "scan_eligible": True,
            },
            "BAD/USDT:USDT": {
                "last_price": 0.1,
                "quote_volume": 3_000_000.0,
                "is_meme": True,
                "scan_eligible": True,
            },
        }
    )

    store = LBankExecutionStore(
        str(
            db_path
        )
    )

    seed_sufficient(
        store,
        "GOOD/USDT:USDT",
        spread=0.05,
        cost100=0.08,
        depth25=10_000.0,
    )

    seed_sufficient(
        store,
        "MID/USDT:USDT",
        spread=0.15,
        cost100=0.20,
        depth25=2_000.0,
    )

    seed_sufficient(
        store,
        "BAD/USDT:USDT",
        spread=2.0,
        cost100=2.5,
        depth25=20.0,
    )

    assert store.record_observation(
        "UNKNOWN/USDT:USDT",
        packet(
            observed_at=1000.0,
            spread=0.05,
            cost100=0.08,
            depth25=10_000.0,
        ),
    )

    app = FastAPI()

    app.include_router(
        build_execution_suitability_router(
            str(
                db_path
            )
        )
    )

    return (
        TestClient(
            app
        ),
        db_path,
    )


def test_execution_suitability_route_is_registered(
    tmp_path,
):
    client, _ = build_client(
        tmp_path
    )

    response = client.get(
        "/api/execution-suitability"
    )

    assert response.status_code == 200


def test_execution_suitability_endpoint_returns_all_classes(
    tmp_path,
):
    client, _ = build_client(
        tmp_path
    )

    response = client.get(
        "/api/execution-suitability"
    )

    assert response.status_code == 200

    data = response.json()

    assert (
        data[
            "observational_only"
        ]
        is True
    )

    assert (
        data[
            "trade_eligible"
        ]
        is None
    )

    assert (
        data[
            "symbol_count"
        ]
        == 4
    )

    assert (
        data[
            "status_counts"
        ]
        == {
            "SUITABLE": 1,
            "MARGINAL": 1,
            "POOR": 1,
            "UNKNOWN": 1,
        }
    )


def test_examples_per_status_can_be_reduced_to_zero(
    tmp_path,
):
    client, _ = build_client(
        tmp_path
    )

    response = client.get(
        "/api/execution-suitability"
        "?examples_per_status=0"
    )

    assert response.status_code == 200

    examples = response.json()[
        "examples"
    ]

    assert examples[
        "SUITABLE"
    ] == []

    assert examples[
        "MARGINAL"
    ] == []

    assert examples[
        "POOR"
    ] == []

    assert examples[
        "UNKNOWN"
    ] == []


def test_threshold_override_query_parameters_are_rejected(
    tmp_path,
):
    client, _ = build_client(
        tmp_path
    )

    response = client.get(
        "/api/execution-suitability"
        "?cost100_p90_max=999"
    )

    assert (
        response.status_code
        == 422
    )

    detail = response.json()[
        "detail"
    ]

    assert (
        detail[
            "unsupported_parameters"
        ]
        == [
            "cost100_p90_max"
        ]
    )

    assert (
        detail[
            "allowed_parameters"
        ]
        == [
            "examples_per_status"
        ]
    )


def test_excessive_example_limit_is_rejected(
    tmp_path,
):
    client, _ = build_client(
        tmp_path
    )

    response = client.get(
        "/api/execution-suitability"
        "?examples_per_status=101"
    )

    assert (
        response.status_code
        == 422
    )


def test_api_exposes_fixed_auditable_thresholds(
    tmp_path,
):
    client, _ = build_client(
        tmp_path
    )

    response = client.get(
        "/api/execution-suitability"
        "?examples_per_status=1"
    )

    assert response.status_code == 200

    thresholds = response.json()[
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
            "suitable"
        ][
            "minimum_depth_25bps_p50_usdt"
        ]
        == 3590.0
    )

    assert (
        thresholds[
            "marginal"
        ][
            "maximum_cost_100_p90_pct"
        ]
        == 0.305
    )

    assert (
        thresholds[
            "marginal"
        ][
            "minimum_depth_25bps_p50_usdt"
        ]
        == 1190.0
    )


def test_api_request_does_not_mutate_catalogue_state(
    tmp_path,
):
    client, db_path = build_client(
        tmp_path
    )

    with sqlite3.connect(
        str(
            db_path
        )
    ) as conn:
        before = conn.execute(
            """
            SELECT
                symbol,
                scan_eligible,
                status,
                trigger_data
            FROM lbank_catalog
            ORDER BY symbol
            """
        ).fetchall()

    response = client.get(
        "/api/execution-suitability"
    )

    assert response.status_code == 200

    with sqlite3.connect(
        str(
            db_path
        )
    ) as conn:
        after = conn.execute(
            """
            SELECT
                symbol,
                scan_eligible,
                status,
                trigger_data
            FROM lbank_catalog
            ORDER BY symbol
            """
        ).fetchall()

    assert after == before
