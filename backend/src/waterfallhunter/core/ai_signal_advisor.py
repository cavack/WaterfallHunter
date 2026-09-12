"""
ai_signal_advisor.py — AI-Powered Multi-Angle Signal Advisor for WaterfallHunter.

Provides comprehensive multi-angle analysis of trading signals using:
  1. Ollama (local, via Ollama API)
  2. Ollama (fallback, local LLM)
  3. Rule-based heuristic (final fallback)

Usage
-----
    import asyncio
    from waterfallhunter.core.ai_signal_advisor import AISignalAdvisor

    advisor = AISignalAdvisor(
        # Ollama removed
        ollama_url="http://localhost:11434",
    )
    result = await advisor.analyze(signal_data={...})
    print(result["overall"], result["confidence"], result["summary"])

The returned dict always has the same shape regardless of provider:

    {
        "provider": "ollama" | "heuristic",
        "technical_score": 75,      # 0-100
        "risk_score": 60,           # 0-100
        "timing_score": 70,         # 0-100
        "sentiment_score": 65,      # 0-100
        "overall": "GO",            # GO | WAIT | AVOID
        "confidence": 72,           # 0-100
        "summary": "...",           # 2-3 line plain-text summary
        "raw_response": "...",      # full AI text (empty for heuristic)
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger("waterfallhunter.ai_signal_advisor")

# ── Constants ──────────────────────────────────────────────────────────────

_OLLAMA_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    "?key={api_key}"
)

_CACHE_TTL_SECONDS = 300          # 5 minutes
_RATE_LIMIT_MAX_REQUESTS = 10    # per minute
_RATE_LIMIT_WINDOW_SECONDS = 60  # 1 minute window
_HTTP_TIMEOUT = 120.0            # seconds for Ollama (CPU mode)
_OLLAMA_TIMEOUT = 120.0            # seconds for Ollama (CPU mode is slow)


# ── Helpers ────────────────────────────────────────────────────────────────


def _env(name: str, default: str | None = None) -> str | None:
    """Read an environment variable, stripping whitespace."""
    val = os.environ.get(name)
    if val is not None:
        val = val.strip()
        if val:
            return val
    return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Best-effort float conversion."""
    try:
        return float(value)
    except (TypeError, ValueError, ArithmeticError):
        return default


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


# ── Prompt Builder ─────────────────────────────────────────────────────────


def _build_prompt(signal_data: dict[str, Any]) -> str:
    """Construct the multi-angle analysis prompt sent to Ollama / Ollama.

    The prompt asks the model to analyse a single trading signal from five
    angles and return a **strict JSON** block so parsing is deterministic.
    """
    symbol = signal_data.get("symbol", "UNKNOWN")
    readiness = signal_data.get("readiness", 0)
    decision = signal_data.get("decision", "HOLD")
    direction = signal_data.get("direction", signal_data.get("signal", "long"))

    # ── Trade plan ──
    trade_plan = signal_data.get("trade_plan") or {}
    ep = trade_plan.get("EP") or trade_plan.get("entry") or signal_data.get("entry_price")
    sl = trade_plan.get("SL") or trade_plan.get("stop_loss")
    tp = trade_plan.get("TP") or trade_plan.get("take_profit")

    # ── Technical evidence ──
    cascade = signal_data.get("cascade", {})
    cross_exchange = signal_data.get("cross_exchange", {})
    timing = signal_data.get("timing", {})
    anti_chase = signal_data.get("anti_chase", {})
    structure = signal_data.get("structure", {})

    cascade_pass = cascade.get("pass", cascade.get("status", "UNKNOWN"))
    cascade_reasons = cascade.get("reasons", [])

    cross_pass = cross_exchange.get("pass", cross_exchange.get("status", "UNKNOWN"))
    cross_signal = cross_exchange.get("signal", "NONE")
    cross_reasons = cross_exchange.get("reasons", [])

    timing_confirmed = timing.get("confirmed", False)
    timing_reasons = timing.get("reasons", [])

    anti_chase_pass = anti_chase.get("pass", anti_chase.get("status", "UNKNOWN"))
    anti_chase_reasons = anti_chase.get("reasons", [])

    structure_signal = structure.get("signal", "NONE")
    structure_reasons = structure.get("reasons", [])

    # ── Market context ──
    btc_trend = signal_data.get("btc_trend", "UNKNOWN")
    market_sentiment = signal_data.get("market_sentiment", "UNKNOWN")

    # ── Fundamental score ──
    fundamental = signal_data.get("fundamental") or {}
    fundamental_score = fundamental.get("score", fundamental.get("total", "N/A"))
    fundamental_notes = fundamental.get("notes", fundamental.get("summary", ""))

    # ── R:R ratio ──
    rr_ratio = signal_data.get("rr_ratio")
    if rr_ratio is None and ep and sl and tp:
        try:
            risk = abs(float(ep) - float(sl))
            reward = abs(float(tp) - float(ep))
            if risk > 0:
                rr_ratio = round(reward / risk, 2)
        except (TypeError, ValueError, ArithmeticError):
            rr_ratio = None

    prompt = f"""You are an expert crypto trading signal analyst. Analyse the following
signal from multiple angles and provide a structured assessment.

## Signal Details
- Symbol: {symbol}
- Readiness Score: {readiness}
- Decision: {decision}
- Direction: {direction}
- Entry Price (EP): {ep}
- Stop Loss (SL): {sl}
- Take Profit (TP): {tp}
- Risk:Reward Ratio: {rr_ratio if rr_ratio is not None else 'N/A'}

## Technical Evidence
### Cascade Check
- Pass: {cascade_pass}
- Reasons: {cascade_reasons}

### Cross-Exchange Correlation
- Pass: {cross_pass}
- Signal: {cross_signal}
- Reasons: {cross_reasons}

### Timing Confirmation
- Confirmed: {timing_confirmed}
- Reasons: {timing_reasons}

### Anti-Chase Filter
- Pass: {anti_chase_pass}
- Reasons: {anti_chase_reasons}

### Market Structure
- Signal: {structure_signal}
- Reasons: {structure_reasons}

## Market Context
- BTC Trend: {btc_trend}
- Market Sentiment: {market_sentiment}

## Fundamental Score
- Score: {fundamental_score}
- Notes: {fundamental_notes}

## Analysis Required
Analyse this signal from the following angles:
1. **Technical**: Is the setup technically sound? (score 0-100)
2. **Risk**: Are the stop loss and take profit levels appropriate for the risk? (score 0-100)
3. **Market Timing**: Is this a good time to enter based on market conditions? (score 0-100)
4. **Sentiment**: Does the market sentiment and fundamental data support this trade? (score 0-100)
5. **Overall**: GO / WAIT / AVOID with a confidence percentage (0-100)

Respond with ONLY a JSON object in this exact format (no markdown, no extra text):
{{
  "technical_score": <0-100>,
  "risk_score": <0-100>,
  "timing_score": <0-100>,
  "sentiment_score": <0-100>,
  "overall": "GO" | "WAIT" | "AVOID",
  "confidence": <0-100>,
  "summary": "<2-3 sentence summary of the analysis>",
  "technical_analysis": "<brief technical analysis>",
  "risk_analysis": "<brief risk analysis>",
  "timing_analysis": "<brief timing analysis>",
  "sentiment_analysis": "<brief sentiment analysis>"
}}
"""
    return prompt


# ── Response Parsing ───────────────────────────────────────────────────────


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _extract_json_block(text: str) -> dict[str, Any] | None:
    """Try to extract a JSON object from a model response text.

    Handles three scenarios:
      1. Pure JSON (ideal case)
      2. JSON inside a ```json fenced code block
      3. JSON embedded anywhere in the text (first { ... } match)
    """
    if not text:
        return None

    # Strip markdown code fences if present
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1)
    else:
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            return None
        candidate = match.group(0)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Try a more forgiving approach: find the outermost braces
        first_brace = candidate.find("{")
        last_brace = candidate.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            try:
                return json.loads(candidate[first_brace : last_brace + 1])
            except json.JSONDecodeError:
                pass
        return None


def _parse_response(raw_text: str) -> dict[str, Any]:
    """Parse an AI response into the structured result dict.

    Always returns a dict with all expected keys. If parsing fails,
    the raw text is used as the summary and scores default to 50
    (neutral).
    """
    parsed = _extract_json_block(raw_text)

    if parsed is None:
        logger.warning("Failed to parse AI response as JSON, using raw text as summary")
        return {
            "provider": "",  # set by caller
            "technical_score": 50,
            "risk_score": 50,
            "timing_score": 50,
            "sentiment_score": 50,
            "overall": "WAIT",
            "confidence": 50,
            "summary": (raw_text or "No analysis available")[:500],
            "raw_response": raw_text,
        }

    def _get_int(key: str, default: int = 50) -> int:
        val = parsed.get(key, default)
        return int(_clamp(_safe_float(val, default)))

    overall_raw = str(parsed.get("overall", "WAIT")).upper().strip()
    # Normalise to one of the three valid values
    if overall_raw.startswith("GO"):
        overall = "GO"
    elif overall_raw.startswith("AVOID") or overall_raw.startswith("NO"):
        overall = "AVOID"
    else:
        overall = "WAIT"

    summary = parsed.get("summary", "")
    if not summary:
        # Build a summary from the sub-analyses if available
        parts = []
        for k in ("technical_analysis", "risk_analysis", "timing_analysis", "sentiment_analysis"):
            v = parsed.get(k)
            if v:
                parts.append(str(v))
        summary = " ".join(parts)[:500] if parts else "Analysis completed."

    return {
        "provider": "",  # set by caller
        "technical_score": _get_int("technical_score"),
        "risk_score": _get_int("risk_score"),
        "timing_score": _get_int("timing_score"),
        "sentiment_score": _get_int("sentiment_score"),
        "overall": overall,
        "confidence": _get_int("confidence"),
        "summary": summary[:500],
        "raw_response": raw_text,
    }


# ── Rule-Based Heuristic Fallback ──────────────────────────────────────────


def _heuristic_analysis(signal_data: dict[str, Any]) -> dict[str, Any]:
    """Generate a rule-based analysis when both AI providers are unavailable.

    Uses simple heuristics based on signal readiness, cascade, cross-exchange
    correlation, and R:R ratio to produce a reasonable assessment.
    """
    readiness = _safe_float(signal_data.get("readiness", 0))
    cascade = signal_data.get("cascade", {}) or {}
    cross_exchange = signal_data.get("cross_exchange", {}) or {}
    trade_plan = signal_data.get("trade_plan") or {}

    cascade_pass = str(cascade.get("pass", cascade.get("status", ""))).upper() in ("PASS", "TRUE", "1", "YES")
    cross_pass = str(cross_exchange.get("pass", cross_exchange.get("status", ""))).upper() in ("PASS", "TRUE", "1", "YES")
    cross_signal = str(cross_exchange.get("signal", "")).upper()

    # R:R calculation
    ep = trade_plan.get("EP") or trade_plan.get("entry") or signal_data.get("entry_price")
    sl = trade_plan.get("SL") or trade_plan.get("stop_loss")
    tp = trade_plan.get("TP") or trade_plan.get("take_profit")
    rr_ratio = signal_data.get("rr_ratio")
    if rr_ratio is None and ep and sl and tp:
        try:
            risk = abs(float(ep) - float(sl))
            reward = abs(float(tp) - float(ep))
            if risk > 0:
                rr_ratio = round(reward / risk, 2)
        except (TypeError, ValueError, ArithmeticError):
            rr_ratio = None

    # ── Overall recommendation ──
    if readiness > 60 and cascade_pass and cross_pass:
        overall = "GO"
        summary = f"GO - Strong signal. Readiness {readiness:.0f}, cascade PASS, cross-exchange confirmed."
        confidence = min(85, int(readiness) + 15)
    elif readiness > 50 and cascade_pass:
        overall = "WAIT"
        summary = f"WAIT - Moderate signal, missing confirmation. Readiness {readiness:.0f}, cascade PASS."
        confidence = 50
    else:
        overall = "AVOID"
        summary = f"AVOID - Weak signal. Readiness {readiness:.0f}."
        confidence = max(20, int(readiness) - 10)

    # ── Technical score ──
    tech_score = int(_clamp(readiness))
    if cascade_pass:
        tech_score += 10
    if cross_pass:
        tech_score += 10
    tech_score = int(_clamp(tech_score))

    # ── Risk score (based on R:R) ──
    if rr_ratio is not None:
        if rr_ratio >= 2.0:
            risk_score = 80
            risk_note = f"Good R:R ratio of {rr_ratio}"
        elif rr_ratio >= 1.0:
            risk_score = 55
            risk_note = f"Acceptable R:R ratio of {rr_ratio}"
        else:
            risk_score = 30
            risk_note = f"Poor R:R ratio of {rr_ratio}"
    else:
        risk_score = 50
        risk_note = "R:R ratio not available"

    # ── Timing score ──
    timing = signal_data.get("timing", {}) or {}
    timing_confirmed = timing.get("confirmed", False)
    timing_score = 75 if timing_confirmed else 45

    # ── Sentiment score ──
    market_sentiment = str(signal_data.get("market_sentiment", "")).upper()
    fundamental = signal_data.get("fundamental") or {}
    fund_score = _safe_float(fundamental.get("score", fundamental.get("total", 50)), 50)

    if "BULL" in market_sentiment or "GREED" in market_sentiment:
        sentiment_score = 70
    elif "BEAR" in market_sentiment or "FEAR" in market_sentiment:
        sentiment_score = 30
    else:
        sentiment_score = int(_clamp(fund_score))

    # ── Build summary ──
    summary_lines = [
        f"{overall} - Confidence {confidence}%. Readiness {readiness:.0f}.",
        f"Technical: cascade {'PASS' if cascade_pass else 'FAIL'}, cross-exchange {'confirmed' if cross_pass else 'not confirmed'}.",
        f"Risk: {risk_note}. Timing {'confirmed' if timing_confirmed else 'not confirmed'}.",
    ]

    return {
        "provider": "heuristic",
        "technical_score": tech_score,
        "risk_score": risk_score,
        "timing_score": timing_score,
        "sentiment_score": sentiment_score,
        "overall": overall,
        "confidence": confidence,
        "summary": " ".join(summary_lines),
        "raw_response": "",
    }


# ── Cache Entry ─────────────────────────────────────────────────────────────


@dataclass
class _CacheEntry:
    result: dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def is_expired(self, ttl: float = _CACHE_TTL_SECONDS) -> bool:
        return (time.time() - self.timestamp) > ttl


# ── Rate Limiter ────────────────────────────────────────────────────────────


class _RateLimiter:
    """Simple sliding-window rate limiter for Ollama API calls.

    Limits to ``max_requests`` within a ``window_seconds`` window.
    """

    def __init__(self, max_requests: int = _RATE_LIMIT_MAX_REQUESTS, window: float = _RATE_LIMIT_WINDOW_SECONDS):
        self.max_requests = max_requests
        self.window = window
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """Try to acquire a slot. Returns True if allowed, False if rate-limited."""
        async with self._lock:
            now = time.time()
            # Prune expired timestamps
            self._timestamps = [t for t in self._timestamps if now - t < self.window]
            if len(self._timestamps) >= self.max_requests:
                logger.debug("Rate limit reached: %d/%d in last %ds", len(self._timestamps), self.max_requests, self.window)
                return False
            self._timestamps.append(now)
            return True


# ── Main Class ─────────────────────────────────────────────────────────────


class AISignalAdvisor:
    """AI-powered multi-angle signal advisor with Ollama → Heuristic fallback.

    Parameters
    ----------
    ollama_url : str | None
        Base URL for Ollama. Falls back to ``OLLAMA_BASE_URL`` env var,
        default ``http://localhost:11434``.
    ollama_model : str
        Ollama model name. Falls back to ``OLLAMA_MODEL`` env var,
        default ``llama3.2:3b``.
    cache_ttl : int
        Cache TTL in seconds. Default 300 (5 minutes).
    rate_limit_max : int
        Max requests per minute to Ollama. Default 10.
    """

    def __init__(
        self,
        # Ollama removed — Ollama only
        ollama_url: str | None = None,
        ollama_model: str | None = None,
        cache_ttl: int = _CACHE_TTL_SECONDS,
        rate_limit_max: int = _RATE_LIMIT_MAX_REQUESTS,
    ) -> None:
        # Ollama removed — Ollama only
        self.ollama_url = (ollama_url or _env("OLLAMA_BASE_URL", "http://localhost:11434") or "").rstrip("/")
        self.ollama_model = ollama_model or _env("OLLAMA_MODEL", "llama3.2:3b")
        self.cache_ttl = cache_ttl
        self._cache: dict[str, _CacheEntry] = {}
        self._cache_lock = asyncio.Lock()
        self._rate_limiter = _RateLimiter(max_requests=rate_limit_max)

        logger.info(
            "AISignalAdvisor initialised: ollama_url=%s, ollama_model=%s",
            self.ollama_model,
            
            self.ollama_url,
            self.ollama_model,
        )

    # ── Public API ─────────────────────────────────────────────────────────

    async def analyze(self, signal_data: dict[str, Any]) -> dict[str, Any]:
        """Analyse a trading signal from multiple angles.

        Tries Ollama first, then Ollama, then falls back to rule-based
        heuristic. Results are cached per-symbol for ``cache_ttl`` seconds.

        Returns
        -------
        dict with keys: provider, technical_score, risk_score, timing_score,
        sentiment_score, overall, confidence, summary, raw_response
        """
        symbol = signal_data.get("symbol", "UNKNOWN")

        # ── Check cache ──
        cached = await self._get_cached(symbol)
        if cached is not None:
            logger.debug("Cache hit for %s", symbol)
            return cached

        # ── Build the prompt once (shared by Ollama & Ollama) ──
        prompt = _build_prompt(signal_data)

        # ── Try Ollama FIRST (local, reliable, no rate limits) ──
        if self.ollama_url:
            try:
                result = await self._call_ollama(prompt)
                if result is not None:
                    await self._set_cached(symbol, result)
                    return result
            except Exception as exc:
                logger.warning("Ollama analysis failed: %s", exc)
        else:
            logger.debug("Ollama URL not configured, skipping Ollama")

        # ── Final fallback to heuristic ──
        logger.info("All AI providers unavailable, using heuristic analysis for %s", symbol)
        result = _heuristic_analysis(signal_data)
        await self._set_cached(symbol, result)
        return result

    # ── Ollama ──────────────────────────────────────────────────────────────

    async def _call_ollama(self, prompt: str) -> dict[str, Any] | None:
        """Call the Ollama API and return parsed result, or None on failure."""
        url = f"{self.ollama_url}/api/generate"
        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 256,
            },
        }

        async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT) as client:
            logger.debug("Calling Ollama API: model=%s, url=%s (timeout=%ss)", self.ollama_model, url, _OLLAMA_TIMEOUT)
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        raw_text = data.get("response", "")
        if not raw_text:
            logger.warning("Ollama returned empty response")
            return None

        result = _parse_response(raw_text)
        result["provider"] = "ollama"
        return result

    # ── Cache ────────────────────────────────────────────────────────────────

    async def _get_cached(self, symbol: str) -> dict[str, Any] | None:
        """Return cached result if still valid, else None."""
        async with self._cache_lock:
            entry = self._cache.get(symbol)
            if entry is not None and not entry.is_expired(self.cache_ttl):
                return entry.result
            # Clean up expired entry
            if entry is not None:
                self._cache.pop(symbol, None)
        return None

    async def _set_cached(self, symbol: str, result: dict[str, Any]) -> None:
        """Store a result in the cache."""
        async with self._cache_lock:
            self._cache[symbol] = _CacheEntry(result=result)

    # ── Utility ──────────────────────────────────────────────────────────────

    def clear_cache(self) -> None:
        """Clear all cached results."""
        self._cache.clear()
        logger.info("Signal advisor cache cleared")


# ── Module-level convenience ────────────────────────────────────────────────

_default_advisor: AISignalAdvisor | None = None


def get_advisor() -> AISignalAdvisor:
    """Return a module-level singleton AISignalAdvisor instance.

    Useful for code that doesn't want to manage the advisor lifecycle
    manually. Configuration comes entirely from environment variables.
    """
    global _default_advisor
    if _default_advisor is None:
        _default_advisor = AISignalAdvisor()
    return _default_advisor


# ── CLI entry point for manual testing ──────────────────────────────────────


async def _main() -> None:
    """Quick manual test with a sample signal."""
    import pprint

    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    sample_signal = {
        "symbol": "BTCUSDT",
        "readiness": 72,
        "decision": "LONG",
        "direction": "long",
        "trade_plan": {"EP": 68000, "SL": 67000, "TP": 71000},
        "cascade": {"pass": True, "reasons": ["MA alignment confirmed", "Volume increasing"]},
        "cross_exchange": {"pass": True, "signal": "BID_ASK_SKEW", "reasons": ["Binance bid premium 0.3%"]},
        "timing": {"confirmed": True, "reasons": ["BTC hourly candle above VWAP"]},
        "anti_chase": {"pass": True, "reasons": ["No FOMO detected"]},
        "structure": {"signal": "BULLISH", "reasons": ["Higher highs, higher lows"]},
        "btc_trend": "BULLISH",
        "market_sentiment": "GREED",
        "fundamental": {"score": 68, "notes": "Positive funding rates, whale accumulation detected"},
    }

    advisor = AISignalAdvisor()
    result = await advisor.analyze(signal_data=sample_signal)
    print("\n" + "=" * 60)
    print("AI Signal Advisor — Result")
    print("=" * 60)
    pprint.pprint(result)
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(_main())
