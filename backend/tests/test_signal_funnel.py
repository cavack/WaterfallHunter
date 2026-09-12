from waterfallhunter.core.signal_funnel import SignalFunnel


def test_funnel_reports_snapshot_and_persisted_stage_chains_separately():
    report = SignalFunnel.build(
        {
            "CHAIN/USDT:USDT": {
                "status": "WATCH",
                "metrics": {
                    "strategy_stages": {
                        "hype": False,
                        "damage": False,
                        "setup": False,
                        "trigger": True,
                        "passed": False,
                    },
                    "stage_lifecycle": {
                        "available": True,
                        "confirmed": {
                            "hype": True,
                            "damage": True,
                            "setup": True,
                            "trigger": True,
                            "passed": True,
                        },
                    },
                },
            }
        }
    )

    assert report["version"] == "signal_funnel_observational_v2"
    assert report["stages"]["passed"]["passed"] == 0
    assert report["stage_lifecycle"]["stages"]["passed"]["passed"] == 1
    assert report["stage_lifecycle"]["hard_gating_allowed"] is False


def test_funnel_exposes_symbols_for_current_persisted_trigger():
    report = SignalFunnel.build(
        {
            "ZETA/USDT:USDT": {
                "status": "WATCH",
                "metrics": {
                    "stage_lifecycle": {
                        "available": True,
                        "confirmed": {
                            "hype": True,
                            "damage": True,
                            "setup": True,
                            "trigger": True,
                            "passed": True,
                        },
                    },
                },
            },
            "ALPHA/USDT:USDT": {
                "status": "PRE-TRIGGER",
                "metrics": {
                    "stage_lifecycle": {
                        "available": True,
                        "confirmed": {
                            "hype": True,
                            "damage": True,
                            "setup": True,
                            "trigger": True,
                            "passed": True,
                        },
                    },
                },
            },
            "BETA/USDT:USDT": {
                "status": "ARMED",
                "metrics": {
                    "stage_lifecycle": {
                        "available": True,
                        "confirmed": {
                            "hype": True,
                            "damage": True,
                            "setup": True,
                            "trigger": False,
                            "passed": False,
                        },
                    },
                },
            },
        }
    )

    assert report["stage_lifecycle"]["stages"]["trigger"]["passed"] == 2
    assert report["stage_lifecycle"]["members"]["trigger"] == [
        "ALPHA/USDT:USDT",
        "ZETA/USDT:USDT",
    ]
