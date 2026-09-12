import sqlite3

from waterfallhunter.core import lbank_execution_stats as stats_module
from waterfallhunter.core.lbank_execution_stats import (
    EXECUTION_HISTORY_METRICS,
    LBankExecutionStats,
)
from waterfallhunter.core.lbank_execution_suitability_report import (
    LBankExecutionSuitabilityReport,
)


def _create_history_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE lbank_execution_observation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            observation_status TEXT NOT NULL,
            observed_at REAL NOT NULL,
            spread_pct REAL,
            cost_25_pct REAL,
            cost_50_pct REAL,
            cost_100_pct REAL,
            depth_10bps_min_usdt REAL,
            depth_25bps_min_usdt REAL,
            depth_50bps_min_usdt REAL,
            depth_100bps_min_usdt REAL,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        )
        """
    )


def _insert_history_row(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    status: str,
    observed_at: float,
    value: float,
) -> None:
    columns = ",".join(EXECUTION_HISTORY_METRICS)
    placeholders = ",".join("?" for _ in EXECUTION_HISTORY_METRICS)
    conn.execute(
        f"""
        INSERT INTO lbank_execution_observation_history (
            symbol, observation_status, observed_at,
            {columns}, payload, created_at
        ) VALUES (?, ?, ?, {placeholders}, '{{}}', ?)
        """,
        (
            symbol,
            status,
            observed_at,
            *(value + index for index, _ in enumerate(EXECUTION_HISTORY_METRICS)),
            observed_at,
        ),
    )


def _seed_history(conn: sqlite3.Connection) -> None:
    rows = (
        ("A/USDT:USDT", "OBSERVED", 100.0, 1.0),
        ("A/USDT:USDT", "OBSERVED", 200.0, 2.0),
        ("B/USDT:USDT", "OBSERVED", 300.0, 3.0),
        ("B/USDT:USDT", "UNAVAILABLE", 400.0, 4.0),
        ("B/USDT:USDT", "OBSERVED", 500.0, 5.0),
        ("B/USDT:USDT", "OBSERVED", 700.0, 7.0),
        ("C/USDT:USDT", "OBSERVED", 250.0, 2.5),
        ("C/USDT:USDT", "OBSERVED", 350.0, 3.5),
        ("C/USDT:USDT", "OBSERVED", 450.0, 4.5),
        ("C/USDT:USDT", "OBSERVED", 600.0, 6.0),
    )
    for symbol, status, observed_at, value in rows:
        _insert_history_row(
            conn,
            symbol=symbol,
            status=status,
            observed_at=observed_at,
            value=value,
        )
    conn.commit()


def test_bulk_universe_summary_matches_per_symbol_semantics(monkeypatch):
    database_uri = "file:wfh-bulk-stats-parity?mode=memory&cache=shared"
    anchor = sqlite3.connect(database_uri, uri=True)
    try:
        _create_history_schema(anchor)
        _seed_history(anchor)
        monkeypatch.setattr(stats_module.time, "time", lambda: 10_000.0)
        stats = LBankExecutionStats("unused-in-memory.db")
        monkeypatch.setattr(
            stats,
            "_connect",
            lambda timeout=10.0: sqlite3.connect(database_uri, uri=True),
        )

        symbols = stats.list_symbols(limit=2)
        expected = [
            stats.summarize_symbol(symbol, limit=3)
            for symbol in symbols
        ]
        actual = stats.summarize_universe(
            symbol_limit=2,
            per_symbol_limit=3,
        )
    finally:
        anchor.close()

    assert symbols == ["B/USDT:USDT", "C/USDT:USDT"]
    assert actual == expected


def test_bulk_exact_symbols_match_per_symbol_semantics_and_preserve_order(monkeypatch):
    database_uri = "file:wfh-bulk-exact-symbols?mode=memory&cache=shared"
    anchor = sqlite3.connect(database_uri, uri=True)
    try:
        _create_history_schema(anchor)
        _seed_history(anchor)
        monkeypatch.setattr(stats_module.time, "time", lambda: 10_000.0)
        stats = LBankExecutionStats("unused-in-memory.db")
        monkeypatch.setattr(
            stats,
            "_connect",
            lambda timeout=10.0: sqlite3.connect(database_uri, uri=True),
        )
        requested = [
            "A/USDT:USDT",
            "C/USDT:USDT",
            "MISSING/USDT:USDT",
        ]
        expected = [
            stats.summarize_symbol(symbol, limit=3)
            for symbol in requested
        ]
        actual = stats.summarize_symbols(
            requested,
            per_symbol_limit=3,
        )
    finally:
        anchor.close()

    assert [row["symbol"] for row in actual] == requested
    assert actual == expected
    assert all(row["symbol"] != "B/USDT:USDT" for row in actual)


class _BulkOnlyStats:
    def __init__(self) -> None:
        self.bulk_calls = 0

    def summarize_universe(self, *, symbol_limit=10_000, per_symbol_limit=10_000):
        self.bulk_calls += 1
        assert symbol_limit == 7
        assert per_symbol_limit == 10_000
        return [
            {
                "symbol": "A/USDT:USDT",
                "availability_rate": 1.0,
                "evidence": {
                    "observed_samples": 5,
                    "observation_span_hours": 3.0,
                },
            },
            {
                "symbol": "B/USDT:USDT",
                "availability_rate": 0.5,
                "evidence": {
                    "observed_samples": 2,
                    "observation_span_hours": 1.0,
                },
            },
        ]

    def list_symbols(self, *, limit=10_000):
        raise AssertionError("report builder must not use list_symbols")

    def summarize_symbol(self, symbol, *, since=None, limit=10_000):
        raise AssertionError("report builder must not issue per-symbol summaries")

    def coverage_summary(self):
        return {"unique_symbols": 2}


class _UnknownClassifier:
    def classify_summary(self, summary):
        return {
            "symbol": summary["symbol"],
            "status": "UNKNOWN",
            "metrics": {},
            "failed_checks": [],
        }

    def thresholds(self):
        return {}


def test_report_builder_consumes_one_bulk_universe_snapshot() -> None:
    stats = _BulkOnlyStats()
    report = LBankExecutionSuitabilityReport(
        stats,
        classifier=_UnknownClassifier(),
    ).build_report(symbol_limit=7, examples_per_status=0)

    assert stats.bulk_calls == 1
    assert report["symbol_count"] == 2
    assert report["unknown_classification_count"] == 2
