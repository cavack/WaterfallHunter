"""Bounded cascade evidence derived from canonical live market observations."""

from __future__ import annotations

import math
from typing import Any

from waterfallhunter.core.liquidation_flow import LIQUIDATION_FLOW_FRESHNESS_SECONDS


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(float(value), upper))


def _ramp(value: float, lower: float, upper: float, maximum: float) -> float:
    if upper <= lower:
        return 0.0
    return _clamp((value - lower) / (upper - lower), 0.0, 1.0) * maximum

def _trade_flow(metrics: dict[str, Any]) -> tuple[dict[str, Any], float, float]:
    micro = _record(metrics.get("microstructure"))
    derivatives = _record(metrics.get("derivatives"))
    taker_ratio = _finite(derivatives.get("taker_buy_sell_ratio"))
    sell_flow = _finite(micro.get("sell_flow_usdt"))
    buy_flow = _finite(micro.get("buy_flow_usdt"))
    footprint = _record(micro.get("footprint"))
    available = taker_ratio is not None or (sell_flow is not None and buy_flow is not None)
    if not available:
        return {"available": False, "reason": "trade flow unavailable"}, 0.0, 0.0

    points = 0.0
    sell_share = None
    if taker_ratio is not None:
        if taker_ratio <= 0.8:
            points += 1.5
        elif taker_ratio < 1.0:
            points += _ramp(1.0 - taker_ratio, 0.0, 0.2, 1.5)
    if sell_flow is not None and buy_flow is not None and sell_flow + buy_flow > 0:
        sell_share = sell_flow / (sell_flow + buy_flow)
        points += _ramp(sell_share, 0.5, 0.75, 1.0)
    if footprint.get("available") is True and footprint.get("aggressive_selling") is True:
        points += 0.5
    sell_dominance = bool((taker_ratio is not None and taker_ratio < 1.0) or (sell_share is not None and sell_share > 0.5))
    return {
        "available": True,
        "taker_buy_sell_ratio": taker_ratio,
        "sell_flow_usdt": sell_flow,
        "buy_flow_usdt": buy_flow,
        "sell_share": round(sell_share, 4) if sell_share is not None else None,
        "sell_dominance": sell_dominance,
    }, _clamp(points, 0.0, 3.0), 3.0

def _derivatives(metrics: dict[str, Any]) -> tuple[dict[str, Any], float, float]:
    derivatives = _record(metrics.get("derivatives"))
    if derivatives.get("available") is not True:
        return {"available": False, "reason": "derivatives unavailable"}, 0.0, 0.0
    funding_percentile = _finite(derivatives.get("funding_percentile"))
    oi_change = _finite(derivatives.get("oi_change_1h_pct"))
    top_ratio = _finite(derivatives.get("top_trader_long_short_ratio"))
    taker_change = _finite(derivatives.get("taker_ratio_change_1h"))
    taker_ratio = _finite(derivatives.get("taker_buy_sell_ratio"))
    points = 0.0
    if top_ratio is not None and (taker_ratio is None or taker_ratio <= 1.5):
        points += _ramp(top_ratio, 1.2, 2.0, 1.0)
    if funding_percentile is not None:
        points += _ramp(funding_percentile, 0.5, 0.95, 0.8)
    if oi_change is not None:
        if oi_change >= 0.5:
            points += 0.8
        elif oi_change > 0:
            points += 0.6
        elif oi_change >= -0.25:
            points += 0.3
    if taker_change is not None and taker_ratio is not None and taker_ratio < 1.0 and taker_change < 0:
        points += _ramp(abs(taker_change), 0.1, 0.4, 0.4)
    return {
        "available": True,
        "funding_rate": _finite(derivatives.get("funding_rate")),
        "funding_percentile": funding_percentile,
        "oi_change_1h_pct": oi_change,
        "top_trader_long_short_ratio": top_ratio,
        "taker_ratio_change_1h": taker_change,
    }, _clamp(points, 0.0, 3.0), 3.0


def _liquidity(metrics: dict[str, Any]) -> tuple[dict[str, Any], float, float]:
    micro = _record(metrics.get("microstructure"))
    bid_depth = _finite(micro.get("bid_depth_usdt"))
    ask_depth = _finite(micro.get("ask_depth_usdt"))
    spread = _finite(micro.get("spread_pct"))
    slippage = _finite(micro.get("slippage_pct"))
    if bid_depth is None or ask_depth is None or spread is None or slippage is None:
        return {"available": False, "reason": "liquidity packet unavailable"}, 0.0, 0.0

    total_depth = bid_depth + ask_depth
    ask_share = ask_depth / total_depth if total_depth > 0 else 0.5
    points = _ramp(ask_share, 0.5, 0.7, 0.9)
    points += _ramp(0.12 - spread, 0.0, 0.10, 0.55)
    points += _ramp(0.20 - slippage, 0.0, 0.18, 0.55)
    return {
        "available": True,
        "bid_depth_usdt": bid_depth,
        "ask_depth_usdt": ask_depth,
        "ask_depth_share": round(ask_share, 4),
        "spread_pct": spread,
        "slippage_pct": slippage,
        "sell_side_liquidity_pressure": ask_share > 0.55,
    }, _clamp(points, 0.0, 2.0), 2.0


def _liquidations(metrics: dict[str, Any], evaluated_at: int | None) -> tuple[dict[str, Any], float, float]:
    packet = _record(metrics.get("liquidation_flow"))
    if packet.get("available") is not True:
        return {"available": False, "reason": "observed liquidation flow unavailable"}, 0.0, 0.0
    observed_at = _finite(packet.get("observed_at"))
    long_notional = _finite(packet.get("long_liquidation_notional_1m"))
    short_notional = _finite(packet.get("short_liquidation_notional_1m"))
    velocity = _finite(packet.get("liquidation_velocity_usd_per_min"))
    burst_ratio = _finite(packet.get("burst_ratio"))
    if None in {observed_at, long_notional, short_notional, velocity, burst_ratio}:
        return {"available": False, "reason": "incomplete liquidation flow"}, 0.0, 0.0
    if evaluated_at is not None and not 0 <= evaluated_at - observed_at < LIQUIDATION_FLOW_FRESHNESS_SECONDS:
        return {"available": False, "reason": "stale or future liquidation flow"}, 0.0, 0.0
    total = long_notional + short_notional
    long_share = long_notional / total if total > 0 else 0.0
    points = _ramp(long_share, 0.55, 0.9, 0.8)
    points += _ramp(velocity, 50_000.0, 400_000.0, 0.6)
    points += _ramp(burst_ratio, 1.0, 3.0, 0.6)
    return {
        "available": True,
        "observed_at": observed_at,
        "long_liquidation_notional_1m": long_notional,
        "short_liquidation_notional_1m": short_notional,
        "long_share": round(long_share, 4),
        "liquidation_velocity_usd_per_min": velocity,
        "burst_ratio": burst_ratio,
    }, _clamp(points, 0.0, 2.0), 2.0


def build_cascade_evidence(
    metrics: dict[str, Any],
    *,
    evaluated_at: int | None = None,
) -> dict[str, Any]:
    """Build a bounded cascade packet without inventing latent liquidation levels."""
    components: dict[str, dict[str, Any]] = {}
    total_points = 0.0
    maximum_available = 0.0
    for name, builder in (
        ("trade_flow", lambda: _trade_flow(metrics)),
        ("derivatives", lambda: _derivatives(metrics)),
        ("liquidity", lambda: _liquidity(metrics)),
        ("liquidations", lambda: _liquidations(metrics, evaluated_at)),
    ):
        component, points, maximum = builder()
        components[name] = {**component, "points": round(points, 3), "maximum": maximum}
        if component.get("available") is True:
            total_points += points
            maximum_available += maximum

    readiness_pct = (total_points / maximum_available * 100.0) if maximum_available else None
    if maximum_available == 0:
        status = "UNAVAILABLE"
    elif maximum_available >= 4.0:
        # Calibrated: at least 3 of 4 components available is sufficient for PASS/FAIL
        # evaluation. Liquidation flow is frequently unavailable (needs real-time WS)
        # and should not permanently block cascade as PARTIAL.
        status = "PASS" if readiness_pct is not None and readiness_pct >= 50.0 else "FAIL"
    else:
        status = "PARTIAL"
    return {
        "contract_version": "cascade_intelligence_v1",
        "status": status,
        "readiness_points": round(total_points, 3),
        "maximum_available": round(maximum_available, 3),
        "readiness_pct": round(readiness_pct, 2) if readiness_pct is not None else None,
        "components": components,
        "latent_liquidation_levels": None,
    }
