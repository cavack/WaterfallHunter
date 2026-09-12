from waterfallhunter.core.dashboard import compact_metrics


def test_compact_metrics_preserves_explicit_strategy_profile_for_ui_separation():
    compacted = compact_metrics(
        {
            "strategy_profile": "experimental_pretrigger_v1",
            "score_version": "score_v2_watch_v1",
        }
    )

    assert compacted == {
        "strategy_profile": "experimental_pretrigger_v1",
        "score_version": "score_v2_watch_v1",
    }


def test_compact_metrics_keeps_both_stage_chain_views():
    lifecycle = {
        "version": "stage_lifecycle_v1",
        "available": True,
        "confirmed": {"passed": True},
    }
    compacted = compact_metrics(
        {
            "strategy_stages": {"passed": False},
            "snapshot_stage_chain": {"passed": False},
            "stage_lifecycle": lifecycle,
        }
    )

    assert compacted["strategy_stages"]["passed"] is False
    assert compacted["snapshot_stage_chain"]["passed"] is False
    assert compacted["stage_lifecycle"] == lifecycle


def test_live_candidate_without_completed_analysis_has_an_explicit_pending_reason(
    monkeypatch,
):
    import waterfallhunter.main as main

    symbol = "TEST/USDT:USDT"

    monkeypatch.setattr(
        main.db,
        "get_all_active_candidates",
        lambda: {
            symbol: {
                "symbol": symbol,
                "status": "WATCH",
            },
        },
    )

    monkeypatch.setattr(
        main.scanner,
        "active_candidates",
        {
            symbol: {
                "score": None,
                "quote_volume": 1_000_000,
                "analysis_observed_at": 1_700_000_010,
            },
        },
    )

    monkeypatch.setattr(
        main.scanner,
        "get_live_reference",
        lambda _: (
            0.5,
            1_700_000_015,
        ),
    )

    candidate = (
        main.get_formatted_candidates(evaluation_time=1_700_000_020)
        ["candidates"][symbol]
    )

    assert candidate["data_status"] == "live"
    assert candidate["analysis_status"] == "pending"
    assert candidate["score"] is None
    assert candidate["analysis_observed_at"] == 1_700_000_010
    assert candidate["analysis_age_seconds"] == 10.0
    assert candidate["reference_observed_at"] == 1_700_000_015
    assert candidate["reference_age_seconds"] == 5.0

    assert candidate["metrics"] == {
        "analysis_reason": "live analysis pending"
    }


def test_derivative_packet_metric_labels_are_bounded_and_keep_source():
    from waterfallhunter.main import (
        derivative_packet_metric_labels,
    )

    assert derivative_packet_metric_labels(
        {
            "available": True,
            "source_exchange": "binance",
        }
    ) == {
        "source": "binance",
        "outcome": "complete",
        "reason": "none",
    }

    assert derivative_packet_metric_labels(
        {
            "available": False,
            "source_exchange": (
                "untrusted-venue-name-with-cardinality"
            ),
            "reason": (
                "missing valid taker buy/sell ratio: "
                "raw upstream details"
            ),
        }
    ) == {
        "source": "unknown",
        "outcome": "incomplete",
        "reason": "missing_taker_buy_sell_ratio",
    }


def test_main_metrics_are_safe_to_reload_and_keep_bounded_derivative_labels():
    import importlib
    import waterfallhunter.main as main

    reloaded = importlib.reload(main)

    assert reloaded.derivative_packet_metric_labels(
        {
            "available": False,
            "source_exchange": "unexpected-source",
            "reason": (
                "missing valid funding rate: "
                "upstream payload omitted"
            ),
        }
    ) == {
        "source": "unknown",
        "outcome": "incomplete",
        "reason": "missing_funding_rate",
    }


def test_compact_metrics_excludes_heavy_market_payloads():
    compacted = compact_metrics(
        {
            "source_exchange": "binance",
            "total_score": 72.5,
            "ticker": {
                "last": 1.0,
            },
            "orderbook": {
                "bids": [
                    [1.0, 2.0],
                ],
            },
            "microstructure": {
                "spread_pct": 0.1,
                "exchange_filters": {
                    "contract_size": 1,
                },
            },
            "position_setup": {
                "entry_price": 1.0,
            },
        }
    )

    assert compacted == {
        "source_exchange": "binance",
        "total_score": 72.5,
        "microstructure": {
            "spread_pct": 0.1,
        },
        "position_setup": {
            "entry_price": 1.0,
        },
    }


def test_compact_metrics_keeps_the_exact_microstructure_observation_time():
    compacted = compact_metrics(
        {
            "microstructure": {
                "observed_at": 1_700_000_000.25,
                "executable": True,
                "entry_slippage_pct": 0.01,
            },
        }
    )

    assert compacted == {
        "microstructure": {
            "observed_at": 1_700_000_000.25,
            "executable": True,
            "entry_slippage_pct": 0.01,
        },
    }


def test_compact_metrics_keeps_missing_metrics_missing():
    assert compact_metrics(None) is None


def test_compact_metrics_keeps_live_data_provenance():
    compacted = compact_metrics(
        {
            "data_sources": {
                "reference": "bybit",
                (
                    "ticker_orderbook_candles_trades"
                ): "bybit",
            },
            "selected_quote_volume_usdt": (
                12_000_000.0
            ),
            "source_failures": [
                {
                    "exchange": "binance",
                    "reason": "empty orderbook",
                },
            ],
        }
    )

    assert compacted == {
        "data_sources": {
            "reference": "bybit",
            "ticker_orderbook_candles_trades": (
                "bybit"
            ),
        },
        "selected_quote_volume_usdt": (
            12_000_000.0
        ),
        "source_failures": [
            {
                "exchange": "binance",
                "reason": "empty orderbook",
            },
        ],
    }


def test_compact_metrics_keeps_derivatives_without_raw_provider_payloads():
    compacted = compact_metrics(
        {
            "derivatives": {
                "available": True,
                "source_exchange": "binance",
                "mapped_symbol": (
                    "1000PEPE/USDT:USDT"
                ),
                "funding_rate": 0.0002,
                "raw": {
                    "must_not": (
                        "reach the dashboard"
                    ),
                },
            },
        }
    )

    assert compacted == {
        "derivatives": {
            "available": True,
            "source_exchange": "binance",
            "mapped_symbol": (
                "1000PEPE/USDT:USDT"
            ),
            "funding_rate": 0.0002,
        },
    }


def test_compact_metrics_strips_raw_derivatives_from_fallback_attempts():
    compacted = compact_metrics(
        {
            "derivatives": {
                "available": False,
                "fallback_attempts": [
                    {
                        "exchange": "binance",
                        "mapped_symbol": "TEST/USDT:USDT",
                        "market_id": "TESTUSDT",
                        "retrieved_at": 1_700_000_000.0,
                        "reason": "missing valid funding rate",
                        "source_capture": {"funding_rows": ["large", "raw", "payload"]},
                    }
                ],
            }
        }
    )

    assert compacted["derivatives"]["fallback_attempts"] == [
        {
            "exchange": "binance",
            "mapped_symbol": "TEST/USDT:USDT",
            "market_id": "TESTUSDT",
            "retrieved_at": 1_700_000_000.0,
            "reason": "missing valid funding rate",
        }
    ]


def test_compact_metrics_exposes_score_v2_and_normalized_derivative_provenance_only():
    compacted = compact_metrics(
        {
            "score_version": "score_v2",
            "score": 82.5,
            "score_components": {
                "derivatives_confirmation": 12.0,
            },
            "quality_gates": {
                (
                    "complete_fresh_"
                    "derivatives_packet"
                ): True,
            },
            "analysis_reason": None,
            "derivatives": {
                "available": True,
                "source_exchange": "binance",
                "mapped_symbol": (
                    "1000PEPE/USDT:USDT"
                ),
                "market_id": "1000PEPEUSDT",
                "retrieved_at": (
                    1_700_000_000.0
                ),
                "funding_rate": 0.0002,
                "funding_percentile": 0.9,
                "oi_change_1h_pct": -2.0,
                "taker_buy_sell_ratio": 0.8,
                (
                    "top_trader_"
                    "long_short_ratio"
                ): 1.2,
                "raw": {
                    "must_not": (
                        "reach the dashboard"
                    ),
                },
            },
        }
    )

    assert compacted == {
        "score_version": "score_v2",
        "score": 82.5,
        "score_components": {
            "derivatives_confirmation": 12.0,
        },
        "quality_gates": {
            (
                "complete_fresh_"
                "derivatives_packet"
            ): True,
        },
        "analysis_reason": None,
        "derivatives": {
            "available": True,
            "source_exchange": "binance",
            "mapped_symbol": (
                "1000PEPE/USDT:USDT"
            ),
            "market_id": "1000PEPEUSDT",
            "retrieved_at": (
                1_700_000_000.0
            ),
            "funding_rate": 0.0002,
            "funding_percentile": 0.9,
            "oi_change_1h_pct": -2.0,
            "taker_buy_sell_ratio": 0.8,
            "top_trader_long_short_ratio": (
                1.2
            ),
        },
    }


def test_compact_metrics_exposes_split_breakdown_evidence():
    compacted = compact_metrics(
        {
            "breakdown_confirmation": {
                "primary_bearish_timeframes": 1,
                "primary_breakdown_confirmed": False,
                "confirmation_exchange_15m": True,
                "composite_breakdown_confirmed": False,
            }
        }
    )

    assert compacted == {
        "breakdown_confirmation": {
            "primary_bearish_timeframes": 1,
            "primary_breakdown_confirmed": False,
            "confirmation_exchange_15m": True,
            "composite_breakdown_confirmed": False,
        }
    }


def test_compact_metrics_exposes_watch_score_without_promoting_it_to_score_v2():
    compacted = compact_metrics(
        {
            "score": None,
            "analysis_reason": (
                "channel stage chain incomplete"
            ),
            "watch_score": {
                "score_version": (
                    "score_v2_watch_v1"
                ),
                "trade_eligible": False,
                "score": 72.5,
                "coverage_pct": 85.0,
            },
        }
    )

    assert compacted == {
        "score": None,
        "analysis_reason": (
            "channel stage chain incomplete"
        ),
        "watch_score": {
            "score_version": (
                "score_v2_watch_v1"
            ),
            "trade_eligible": False,
            "score": 72.5,
            "coverage_pct": 85.0,
        },
    }


def test_compact_metrics_preserves_observation_contract():
    metrics = {
        "score": None,
        "total_score": None,
        "trade_eligible": False,
        "observation_score": 47.25,
        "observation_status": "PRE-TRIGGER",
        "observation_score_version": "score_v2",
        "observation_components": {
            "timing": 12.5,
        },
        "analysis_reason": (
            "strict trade gates incomplete"
        ),
    }

    compact = compact_metrics(
        metrics
    )

    assert compact["score"] is None
    assert compact["total_score"] is None
    assert compact["trade_eligible"] is False

    assert (
        compact["observation_score"]
        == 47.25
    )

    assert (
        compact["observation_status"]
        == "PRE-TRIGGER"
    )

    assert (
        compact[
            "observation_score_version"
        ]
        == "score_v2"
    )

    assert compact[
        "observation_components"
    ] == {
        "timing": 12.5,
    }


def test_compact_metrics_preserves_candle_geometry_features():
    metrics = {
        "candle_features": {
            "15m": {
                "atr_14": 0.012,
                "atr_pct": 1.2,
                "dynamic_support": 0.95,
                "distance_to_support_pct": (
                    0.3
                ),
                "distance_to_support_atr": (
                    0.25
                ),
                "distance_from_recent_high_pct": (
                    4.8
                ),
                "extension_from_support_atr": (
                    0.25
                ),
                "return_3bars_pct": -1.4,
                "return_6bars_pct": -2.1,
                "return_12bars_pct": -3.0,
                "lower_high": True,
                "support_broken": False,
                "regime_bearish": False,
                "trigger_ready": True,
                "setup": None,
                "pump_pct": 28.0,
            },
        },
    }

    compact = compact_metrics(
        metrics
    )

    assert (
        compact["candle_features"]["15m"]
        ["atr_14"]
        == 0.012
    )

    assert (
        compact["candle_features"]["15m"]
        ["atr_pct"]
        == 1.2
    )

    assert (
        compact["candle_features"]["15m"]
        ["distance_to_support_atr"]
        == 0.25
    )

    assert (
        compact["candle_features"]["15m"]
        ["return_3bars_pct"]
        == -1.4
    )

    assert (
        compact["candle_features"]["15m"]
        ["support_broken"]
        is False
    )

    assert (
        compact["candle_features"]["15m"]
        ["trigger_ready"]
        is True
    )


def test_dashboard_projects_stale_entry_ready_to_invalidated(monkeypatch):
    import waterfallhunter.main as main

    symbol = "STALEUI/USDT:USDT"
    stored_decision = {
        "contract_version": "entry_decision_v1",
        "policy_version": "entry_policy_v1",
        "evaluated_at": 1_700_000_000,
        "decision": "ENTRY_READY",
        "lifecycle_state": "PRE-TRIGGER",
        "entry_readiness": 85.0,
        "evidence_coverage_pct": 90.0,
        "hard_blocked": False,
        "block_reasons": [],
        "reason_codes": ["ENTRY_GATES_PASS"],
        "components": {},
        "evidence_summary": {},
        "trade_plan": {
            "entry_price": 1.0,
            "stop_loss": 1.1,
            "take_profit_1": 0.9,
            "take_profit_2": 0.8,
        },
        "policy": {},
        "event_id": 123,
    }
    monkeypatch.setattr(
        main.db,
        "get_all_active_candidates",
        lambda: {symbol: {"symbol": symbol, "status": "PRE-TRIGGER"}},
    )
    monkeypatch.setattr(
        main.scanner,
        "active_candidates",
        {
            symbol: {
                "score": 80.0,
                "quote_volume": 2_000_000.0,
                "analysis_observed_at": 1_700_000_000,
                "metrics": {"entry_decision": stored_decision},
            }
        },
    )
    monkeypatch.setattr(
        main.scanner,
        "get_live_reference",
        lambda _symbol: (1.0, 1_700_000_195),
    )
    monkeypatch.setattr(main.historical_outcome_store, "symbol_summaries", lambda: {})
    monkeypatch.setattr(
        main.execution_suitability_enricher,
        "for_symbol",
        lambda _symbol: {"status": "UNKNOWN", "observational_only": True},
    )
    monkeypatch.setattr(main.entry_decision_store, "recent_changes", lambda limit=10: [])

    payload = main.get_formatted_candidates(evaluation_time=1_700_000_200)
    projected = payload["candidates"][symbol]["metrics"]["entry_decision"]
    assert payload["candidates"][symbol]["analysis_age_seconds"] == 200.0
    assert projected["decision"] == "INVALIDATED"
    assert "STALE_ANALYSIS" in projected["block_reasons"]
    assert projected["event_id"] == 123


def test_dashboard_drops_stale_forming_from_closest_setups(monkeypatch):
    import waterfallhunter.main as main

    symbol = "STALEFORM/USDT:USDT"
    stored_decision = {
        "contract_version": "entry_decision_v1",
        "policy_version": "entry_policy_v1",
        "evaluated_at": 1_700_000_000,
        "decision": "FORMING",
        "lifecycle_state": "PRE-TRIGGER",
        "entry_readiness": 72.0,
        "evidence_coverage_pct": 90.0,
        "hard_blocked": False,
        "block_reasons": [],
        "reason_codes": ["TIMING_INCOMPLETE"],
        "components": {},
        "evidence_summary": {},
        "trade_plan": None,
        "policy": {},
        "event_id": 124,
    }
    monkeypatch.setattr(
        main.db,
        "get_all_active_candidates",
        lambda: {symbol: {"symbol": symbol, "status": "PRE-TRIGGER"}},
    )
    monkeypatch.setattr(
        main.scanner,
        "active_candidates",
        {
            symbol: {
                "score": 72.0,
                "quote_volume": 2_000_000.0,
                "analysis_observed_at": 1_700_000_000,
                "metrics": {"entry_decision": stored_decision},
            }
        },
    )
    monkeypatch.setattr(
        main.scanner,
        "get_live_reference",
        lambda _symbol: (1.0, 1_700_000_195),
    )
    monkeypatch.setattr(main.historical_outcome_store, "symbol_summaries", lambda: {})
    monkeypatch.setattr(
        main.execution_suitability_enricher,
        "for_symbol",
        lambda _symbol: {"status": "UNKNOWN", "observational_only": True},
    )
    monkeypatch.setattr(main.entry_decision_store, "recent_changes", lambda limit=10: [])

    payload = main.get_formatted_candidates(evaluation_time=1_700_000_200)
    projected = payload["candidates"][symbol]["metrics"]["entry_decision"]
    assert projected["decision"] == "NO_TRADE"
    assert "STALE_ANALYSIS" in projected["block_reasons"]
    assert symbol not in payload["decision_terminal"]["forming"]

def test_dashboard_preserves_canonical_invalidation_when_reference_is_unavailable(monkeypatch):
    import waterfallhunter.main as main

    symbol = "NOREFUI/USDT:USDT"
    invalidated = {
        "contract_version": "entry_decision_v1",
        "policy_version": "entry_policy_v1",
        "evaluated_at": 1_700_000_100,
        "decision": "INVALIDATED",
        "lifecycle_state": "WATCH",
        "entry_readiness": 40.0,
        "evidence_coverage_pct": 80.0,
        "hard_blocked": True,
        "block_reasons": ["STALE_REFERENCE", "ENTRY_CONDITIONS_LOST"],
        "reason_codes": ["STALE_REFERENCE"],
        "components": {},
        "evidence_summary": {},
        "trade_plan": None,
        "policy": {},
        "event_id": 777,
    }
    monkeypatch.setattr(
        main.db,
        "get_all_active_candidates",
        lambda: {symbol: {"symbol": symbol, "status": "WATCH"}},
    )
    monkeypatch.setattr(
        main.scanner,
        "active_candidates",
        {
            symbol: {
                "score": None,
                "quote_volume": 2_000_000.0,
                "analysis_observed_at": 1_700_000_100,
                "metrics": {
                    "error": "no fresh reference price in exchange waterfall",
                    "entry_decision": invalidated,
                    "derivatives": {"available": True, "funding_rate": 0.123},
                },
            }
        },
    )
    monkeypatch.setattr(main.scanner, "get_live_reference", lambda _symbol: (None, None))
    monkeypatch.setattr(main.historical_outcome_store, "symbol_summaries", lambda: {})
    monkeypatch.setattr(
        main.execution_suitability_enricher,
        "for_symbol",
        lambda _symbol: {"status": "UNKNOWN", "observational_only": True},
    )
    monkeypatch.setattr(main.entry_decision_store, "recent_changes", lambda limit=10: [])

    payload = main.get_formatted_candidates(evaluation_time=1_700_000_120)
    candidate = payload["candidates"][symbol]
    assert candidate["data_status"] == "unavailable"
    assert candidate["metrics"]["entry_decision"]["decision"] == "INVALIDATED"
    assert candidate["metrics"]["entry_decision"]["event_id"] == 777
    assert "derivatives" not in candidate["metrics"]


def test_compact_metrics_keeps_bounded_observational_reference_trade_plan():
    compacted = compact_metrics({
        "technical_trade_plan_shadow": {
            "version": "technical_trade_plan_shadow_v1",
            "observational_only": True,
            "hard_gating_allowed": False,
            "available": True,
            "feasible": True,
            "status": "FEASIBLE",
            "setup": {
                "status": "READY", "entry_price": 1.0, "stop_loss": 1.1,
                "take_profit_1": 0.9, "take_profit_2": 0.8,
                "take_profit_3": 0.7, "reward_to_risk": 2.0,
                "raw_heavy_field": [1, 2, 3],
            },
            "reference": {"price": 1.01, "source": "mark"},
        }
    })
    assert compacted["technical_trade_plan_shadow"] == {
        "version": "technical_trade_plan_shadow_v1",
        "observational_only": True, "hard_gating_allowed": False,
        "available": True, "feasible": True, "status": "FEASIBLE",
        "setup": {
            "status": "READY", "entry_price": 1.0, "stop_loss": 1.1,
            "take_profit_1": 0.9, "take_profit_2": 0.8,
            "take_profit_3": 0.7, "reward_to_risk": 2.0,
        },
        "reference": {"price": 1.01, "source": "mark"},
    }
