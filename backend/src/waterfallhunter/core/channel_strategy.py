from typing import Any, Mapping


CHANNEL_STRATEGY_ID = "channel_v1"


def channel_stages(checks: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate the deterministic Pump → damage → waterfall-short signal."""
    h4, h1 = checks["4h"], checks["1h"]
    h4_flags, h1_flags = h4["flags"], h1["flags"]
    damage = bool(h4["support_broken"] and h4_flags["lower_high"])
    if h4["failed_pullback"]:
        setup_type = "FAILED_PULLBACK"
    elif damage and h4_flags["bearish_close"] and h4_flags["volume_acceleration"]:
        setup_type = "BREAKDOWN"
    elif damage and all(h1_flags[name] for name in ("two_bearish", "lower_high", "bearish_close")):
        setup_type = "CONTINUATION"
    else:
        setup_type = None
    trigger_15m, trigger_5m = checks["15m"]["flags"], checks["5m"]["flags"]
    return {
        "hype": bool(h4["hype_context"]),
        "damage": damage,
        "setup": setup_type is not None,
        "setup_type": setup_type,
        "trigger": bool(
            all(trigger_15m[name] for name in ("lower_high", "bearish_close"))
            and all(trigger_5m[name] for name in ("lower_high", "bearish_close"))
        ),
    }
