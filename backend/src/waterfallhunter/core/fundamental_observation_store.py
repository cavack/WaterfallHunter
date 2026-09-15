"""Durable, decision-linked free fundamental observations.

This table is intentionally separate from ``entry_decision_events``. An
observation arrives asynchronously, after the decision has been made, and must
never mutate the canonical packet just to attach research data. Keeping it
keyed to the immutable event id makes later outcome/replay analysis possible.
"""
from __future__ import annotations

import json
from typing import Any

from waterfallhunter.core.managed_sqlite import connect_managed_sqlite


class FundamentalObservationStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._ready = False

    def _ensure(self) -> None:
        if self._ready:
            return
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS fundamental_observations (
                    decision_event_id INTEGER PRIMARY KEY,
                    observed_at INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    observation_json TEXT NOT NULL,
                    FOREIGN KEY (decision_event_id)
                        REFERENCES entry_decision_events(id)
                );
                CREATE INDEX IF NOT EXISTS idx_fundamental_observations_symbol_at
                    ON fundamental_observations(symbol, observed_at DESC);
                """
            )
            conn.commit()
        self._ready = True

    def upsert(
        self,
        decision_event_id: int,
        symbol: str,
        observed_at: int,
        observation: dict[str, Any],
    ) -> None:
        self._ensure()
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            conn.execute(
                """
                INSERT INTO fundamental_observations
                    (decision_event_id, observed_at, symbol, sources_json, observation_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(decision_event_id) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    symbol=excluded.symbol,
                    sources_json=excluded.sources_json,
                    observation_json=excluded.observation_json
                """,
                (
                    decision_event_id,
                    observed_at,
                    symbol,
                    json.dumps(["dexscreener", "coingecko"]),
                    json.dumps(observation, sort_keys=True),
                ),
            )
            conn.commit()
