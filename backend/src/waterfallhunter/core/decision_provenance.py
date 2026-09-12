import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from waterfallhunter.core.score_v2 import ScoreV2
from waterfallhunter.core.signal_metadata import canonical_sha256
from waterfallhunter.core.stage_lifecycle import StageLifecycleStore


CONTRACT_SCHEMA_VERSION = "production_decision_contract_v2"


@lru_cache(maxsize=4)
def source_tree_sha256(package_root: str | None = None) -> tuple[str, int]:
    root = (
        Path(package_root).resolve()
        if package_root is not None
        else Path(__file__).resolve().parents[1]
    )
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*.py") if path.is_file())
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest(), len(files)


def _public_settings(settings: Any) -> dict[str, Any]:
    token_map = str(getattr(settings, "dexscreener_token_map_json", "{}"))
    return {
        "environment": str(getattr(settings, "environment", "unknown")),
        "live_trading_enabled": bool(getattr(settings, "live_trading_enabled", False)),
        "coinglass_configured": bool(getattr(settings, "coinglass_api_key", None)),
        "coinglass_base_url": str(getattr(settings, "coinglass_base_url", "")),
        "dexscreener_enabled": bool(getattr(settings, "dexscreener_enabled", False)),
        "dexscreener_token_map_sha256": hashlib.sha256(token_map.encode()).hexdigest(),
        "onchain_large_transfer_usd": float(
            getattr(settings, "onchain_large_transfer_usd", 100_000.0)
        ),
    }


def build_decision_contract(
    *,
    app_version: str,
    validator: Any,
    settings: Any,
    recorder_bucket_seconds: int,
) -> dict[str, Any]:
    code_sha256, source_file_count = source_tree_sha256()
    micro = validator.microstructure
    position = validator.position_calculator
    candle = validator.candle_analyzer
    derivatives = validator.derivatives
    return {
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "application": {
            "app_version": str(app_version),
            "source_tree_sha256": code_sha256,
            "source_file_count": source_file_count,
        },
        "strategy": {
            "score_version": ScoreV2.version,
            "armed_threshold": validator.armed_threshold,
            "triggered_threshold": validator.triggered_threshold,
            "experimental_profile": validator.experimental_profile,
            "experimental_pretrigger_enabled": bool(
                getattr(settings, "experimental_pretrigger_enabled", False)
            ),
            "experimental_pretrigger_threshold": float(
                getattr(settings, "experimental_pretrigger_threshold", 45.0)
            ),
            "analysis_prefilter_score": validator.analysis_prefilter_score,
            "max_cross_exchange_deviation_pct": validator.max_cross_exchange_deviation_pct,
            "candle_timeframes": list(candle.timeframes),
            "candle_limit": candle.candle_limit,
            "max_closed_candle_age_intervals": candle.max_closed_candle_age_intervals,
            "stage_lifecycle": StageLifecycleStore.contract(),
        },
        "microstructure": {
            "executable_notional": micro.executable_notional,
            "snapshot_delay_seconds": micro.snapshot_delay_seconds,
            "snapshot_ttl_seconds": micro.snapshot_ttl_seconds,
            "trade_ttl_seconds": micro.trade_ttl_seconds,
        },
        "derivatives": {
            "max_data_age_seconds": derivatives.max_data_age_seconds,
            "max_funding_age_seconds": derivatives.max_funding_age_seconds,
            "max_funding_history_age_seconds": derivatives.max_funding_history_age_seconds,
            "min_oi_span_seconds": derivatives.min_oi_span_seconds,
            "max_oi_span_seconds": derivatives.max_oi_span_seconds,
        },
        "position": {
            "taker_fee_pct": position.fee_pct,
            "slippage_pct": position.slippage_pct,
            "funding_pct": position.funding_pct,
            "target_buffer_pct": position.target_buffer_pct,
            "target_rr": position.target_rr,
            "default_capital_usdt": position.default_capital,
        },
        "recorder": {"bucket_seconds": int(recorder_bucket_seconds)},
        "runtime_settings": _public_settings(settings),
    }


def decision_contract_sha256(contract: dict[str, Any]) -> str:
    """Return the RFC8785/JCS SHA-256 identity for a decision contract."""

    return canonical_sha256(contract)
