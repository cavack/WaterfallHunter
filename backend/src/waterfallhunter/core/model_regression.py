"""Pure dispatch for source-bound model regression cases."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from waterfallhunter.core.final_ranking import FinalRanking
from waterfallhunter.core.multi_exchange_validator import MultiExchangeValidator
from waterfallhunter.core.risk_manager import get_leverage
from waterfallhunter.core.score_v2 import ScoreV2


def _envelope(result: Any, *, reason_codes: list[str] | None = None,
              lifecycle_trace: list[str] | None = None,
              ordered_output: list[str] | None = None) -> dict[str, Any]:
    return {
        "reason_codes": reason_codes or [],
        "lifecycle_trace": lifecycle_trace or [],
        "ordered_output": ordered_output or [],
        "result": result,
    }


def replay_model_case(case_input: dict[str, Any]) -> dict[str, Any]:
    """Replay one allowlisted deterministic model entry point."""

    evaluator = str(case_input.get("evaluator") or "")
    arguments = deepcopy(case_input.get("arguments") or {})
    if evaluator in {"score_v2", "score_v2_watch"}:
        scorer = ScoreV2()
        method = scorer.evaluate if evaluator == "score_v2" else scorer.evaluate_watch
        result = method(
            arguments["candles"],
            arguments["microstructure"],
            arguments["derivatives"],
            arguments.get("cross_exchange_confirmed"),
            arguments["price_location"],
        )
        reason = result.get("reason")
        return _envelope(result, reason_codes=[reason] if reason else [])
    if evaluator == "suggested_status":
        validator = object.__new__(MultiExchangeValidator)
        status = validator._suggested_status(
            float(arguments["score"]),
            arguments["stages"],
            bool(arguments["microstructure_approved"]),
            bool(arguments["cross_exchange_confirmed"]),
        )
        return _envelope(
            {"suggested_status": status},
            lifecycle_trace=[status],
        )
    if evaluator == "legacy_leverage":
        symbol = str(arguments["symbol"])
        return _envelope({"symbol": symbol, "leverage": get_leverage(symbol)})
    if evaluator == "final_ranking":
        result = FinalRanking.rank(
            arguments["candidates"],
            limit=int(arguments.get("limit", 3)),
            evaluation_time=float(arguments["evaluation_time"]),
        )
        return _envelope(
            result,
            ordered_output=[item["symbol"] for item in result["all"]],
        )
    raise ValueError(f"unsupported model regression evaluator: {evaluator}")
