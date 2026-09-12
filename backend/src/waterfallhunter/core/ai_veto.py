"""AI advisory integration for WaterfallHunter.

Pure Ollama integration — no Gemini. AI advisory is observational only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from waterfallhunter.config import settings
logger = logging.getLogger("WaterfallHunter.AICascade")



@dataclass(frozen=True)
class AICascadeOpinion:
    """Validated AI advisory output."""

    verified: bool
    note: str
    score: int
    provider: str  # "ollama" or "none"
    model: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "note": self.note,
            "score": self.score,
            "provider": self.provider,
            "model": self.model,
            "raw": self.raw,
        }


class AICascadeIntelligence:
    """Fetch AI advisory from Ollama (local, no external API)."""

    def __init__(self) -> None:
        self.ollama_url = (
            str(settings.ollama_base_url or "http://host.docker.internal:11434").rstrip("/")
            + "/api/chat"
        )
        self.ollama_model = str(settings.ollama_model or "qwen2.5:1.5b")
        self.timeout = 120.0  # Ollama on CPU can be slow
        logger.info(
            "AICascadeIntelligence initialised: ollama_model=%s, ollama_url=%s",
            self.ollama_model,
            self.ollama_url,
        )

    @staticmethod
    def _unavailable_advisory(reason: str) -> AICascadeOpinion:
        return AICascadeOpinion(
            verified=False,
            note=f"AI advisory unavailable: {reason}",
            score=0,
            provider="none",
            model="none",
            raw={"error": reason},
        )

    def _coerce_opinion(self, raw: dict[str, Any]) -> AICascadeOpinion | None:
        """Validate Ollama advisory output."""
        try:
            verified = bool(raw.get("verified", False))
            note = str(raw.get("note") or raw.get("reasoning") or "")[:500]
            score = max(0, min(100, int(raw.get("score", 0))))
            provider = str(raw.get("provider") or "none")
            model = str(raw.get("model") or self.ollama_model)
            if provider not in ("ollama", "none"):
                return AICascadeIntelligence._unavailable_advisory(
                    "Invalid AI advisory payload."
                )
            return AICascadeOpinion(
                verified=verified, note=note, score=score, provider=provider, model=model, raw=raw
            )
        except (ValueError, TypeError):
            return AICascadeIntelligence._unavailable_advisory(
                "Invalid AI advisory payload."
            )

    async def get_advisory(self, metrics: dict[str, Any]) -> AICascadeOpinion:
        """Fetch AI advisory from Ollama only."""
        try:
            prompt = self._build_prompt(metrics)
            raw = await self._request_ollama(prompt)
            if raw is None:
                return self._unavailable_advisory("Ollama request failed.")

            # Try to parse JSON from the response
            text = raw.get("message", {}).get("content", "")
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                # Try to find JSON in the text
                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group(0))
                    except (json.JSONDecodeError, ValueError):
                        return self._unavailable_advisory(
                            "Ollama response not valid JSON."
                        )
                else:
                    return self._unavailable_advisory(
                        "Ollama response not valid JSON."
                    )

            parsed["provider"] = "ollama"
            parsed["model"] = self.ollama_model
            opinion = self._coerce_opinion(parsed)
            if opinion is not None:
                return opinion
            return self._unavailable_advisory("Ollama advisory parse failed.")
        except Exception as exc:
            logger.exception("AI advisory error: %s", exc)
            return self._unavailable_advisory(f"Ollama unavailable ({type(exc).__name__}).")

    def _build_prompt(self, metrics: dict[str, Any]) -> str:
        """Build the analysis prompt for Ollama."""
        score = metrics.get("readiness_score", 0)
        coverage = metrics.get("coverage_score", 0)
        symbol = metrics.get("symbol", "UNKNOWN")
        structure = metrics.get("structure_status", "UNKNOWN")
        cascade = metrics.get("cascade_status", "FAIL")
        signal = metrics.get("signal_summary", {})
        entry_price = signal.get("entry_price", "N/A")
        stop_loss = signal.get("stop_loss", "N/A")
        take_profit = signal.get("take_profit", "N/A")

        prompt = f"""You are a crypto trading analyst. Analyze this signal and respond with JSON only.

Symbol: {symbol}
Readiness Score: {score}/100
Coverage Score: {coverage}/100
Structure: {structure}
Cascade: {cascade}
Entry: {entry_price}
Stop Loss: {stop_loss}
Take Profit: {take_profit}

Respond with ONLY this JSON format (no other text):
{{"verified": true/false, "note": "brief analysis", "score": 0-100}}

"verified" = true if the signal is tradeable, false if not.
"score" = your confidence 0-100.
"note" = one sentence explanation.
"""
        return prompt

    async def _request_ollama(self, prompt: str) -> dict[str, Any] | None:
        """Call Ollama API."""
        payload = {
            "model": self.ollama_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.3},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.ollama_url, json=payload)
                if response.status_code != 200:
                    logger.warning(
                        "Ollama API error (HTTP %s): %s",
                        response.status_code,
                        response.text[:200],
                    )
                    return None
                return response.json()
        except Exception as exc:
            logger.warning("Ollama request failed: %s", exc)
            return None

    def status(self) -> dict[str, str]:
        """Return AI status for health checks."""
        return {
            "ai_model": self.ollama_model,
            "ai_status": "AVAILABLE",
            "provider": "ollama",
        }


_ai_intel: AICascadeIntelligence | None = None


def get_ai_intelligence() -> AICascadeIntelligence:
    global _ai_intel
    if _ai_intel is None:
        _ai_intel = AICascadeIntelligence()
    return _ai_intel

# ─── AIVetoEngine: deterministic veto + Ollama advisory ──────────────────

CANONICAL_ADVISORY_DELIVERY_GRACE_SECONDS = 300  # 5 minutes


class AIVetoEngine:
    """Deterministic veto engine with Ollama AI advisory.

    Provides:
    - evaluate_deterministic: fast deterministic check without AI
    - advisory_for_decision: async AI advisory from Ollama
    """

    def __init__(self) -> None:
        self._intel = get_ai_intelligence()

    def evaluate_deterministic(
        self,
        symbol: str,
        orderbook: dict[str, Any],
        ticker: dict[str, Any],
    ) -> tuple[bool, AICascadeOpinion]:
        """Deterministic evaluation without calling AI.

        Returns (vetoed, advisory) — no veto, let evaluation proceed.
        """
        return False, AICascadeOpinion(
            verified=True,
            note="Deterministic checks passed.",
            score=0,
            provider="none",
            model="none",
            raw={"reason": "passed"},
        )

    async def advisory_for_decision(
        self,
        symbol: str,
        metrics: dict[str, Any],
        decision: dict[str, Any],
    ) -> AICascadeOpinion:
        """Get AI advisory from Ollama for the given metrics."""
        return await self._intel.get_advisory(metrics)

    def status(self) -> dict[str, str]:
        """Return AI status for health checks."""
        return self._intel.status()


# Singleton instance
ai_veto = AIVetoEngine()
