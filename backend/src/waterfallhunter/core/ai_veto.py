"""AI advisory integration for WaterfallHunter.

Pure Ollama integration — no Gemini. AI advisory is observational only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
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

    def to_observational_advisory(self) -> dict[str, Any]:
        """Project the opinion onto the observational advisory contract.

        Consumers (``EntryDecisionStore.append_advisory``,
        ``dashboard_projection``, ``notifier``) read this dictionary shape, so
        the opinion is never handed over as a dataclass instance. Both the
        canonical flags (``observational_only``/``decision_mutated``) and the
        legacy flags (``ai_observational_only``/``ai_decision_critical``) are
        emitted so that every consumer keeps working.
        """
        available = self.provider == "ollama"
        return {
            "observational_only": True,
            "decision_mutated": False,
            "ai_observational_only": True,
            "ai_decision_critical": False,
            "ai_status": "AVAILABLE" if available else "UNAVAILABLE",
            "ai_advice": ("NEUTRAL" if self.verified else "AVOID")
            if available
            else "UNAVAILABLE",
            "ai_confidence": int(self.score) if available else 0,
            "ai_reasoning": str(self.note),
            "ai_provider": self.provider if available else "none",
            "ai_model": self.model if available else "none",
        }


class AICascadeIntelligence:
    """Fetch AI advisory from Ollama (local, no external API)."""

    def __init__(self) -> None:
        self.ollama_url = (
            str(settings.ollama_base_url or "http://host.docker.internal:11434").rstrip("/")
            + "/api/chat"
        )
        self.ollama_model = str(settings.ollama_model or "qwen2.5:1.5b")
        # CPU-only inference: bound the queue instead of letting requests pile
        # up behind a single llama-server slot and expire on timeout.
        self.timeout = 60.0
        self.max_concurrent_requests = 2
        self._request_gate: asyncio.Semaphore | None = None
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
        if self._request_gate is None:
            self._request_gate = asyncio.Semaphore(self.max_concurrent_requests)
        try:
            await asyncio.wait_for(
                self._request_gate.acquire(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Ollama advisory queue saturated; skipping request rather than "
                "queueing behind an unbounded backlog."
            )
            return None
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
        except httpx.TimeoutException as exc:
            logger.warning(
                "Ollama request timed out after %ss on CPU (%s); advisory marked "
                "unavailable.",
                self.timeout,
                type(exc).__name__,
            )
            return None
        except Exception as exc:
            logger.warning("Ollama request failed: %s", exc)
            return None
        finally:
            self._request_gate.release()

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
    - evaluate_deterministic: provider-free market-data veto, no AI call
    - advisory_for_decision: async AI advisory from Ollama
    - get_observational_advisory: async Ollama advisory without veto authority
    - evaluate_symbol: compatibility API combining both
    """

    def __init__(self) -> None:
        self._intel = get_ai_intelligence()
        self.max_bid_ask_ratio = 3.0

    @staticmethod
    def _observational_placeholder(reason: str) -> dict[str, Any]:
        """Provider-free placeholder that satisfies the advisory contract."""
        return {
            "observational_only": True,
            "decision_mutated": False,
            "ai_observational_only": True,
            "ai_decision_critical": False,
            "deterministic_veto": False,
            "deterministic_reason": None,
            "ai_status": "UNAVAILABLE",
            "ai_advice": "PENDING",
            "ai_confidence": 0,
            "ai_reasoning": reason,
            "ai_provider": "none",
            "ai_model": "none",
        }

    def evaluate_deterministic(
        self,
        symbol: str,
        orderbook: dict[str, Any],
        ticker: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Provider-free deterministic market-data veto.

        Returns (vetoed, advisory) — AI is never consulted here. The advisory
        is the observational dictionary contract so that
        ``metrics["ai_advisory"]`` stays consumable by the entry-decision
        reasons and the dashboard projection.
        """
        if not orderbook or not ticker:
            logger.warning(
                "SOFT WARNING [%s]: Missing real market data, but not vetoing.",
                symbol,
            )
            return False, {
                **self._observational_placeholder(
                    "Insufficient market data for AI advisory"
                ),
                "deterministic_reason": "Missing real data (soft warning)",
            }

        bids = orderbook.get("bids", [])[:10]
        asks = orderbook.get("asks", [])[:10]
        bid_vol = sum(row[1] for row in bids) if bids else 0
        ask_vol = sum(row[1] for row in asks) if asks else 0

        deterministic_veto = False
        veto_reason = "Approved by Deterministic Math"

        if ask_vol == 0:
            deterministic_veto = True
            veto_reason = "No Ask liquidity available."
        elif (bid_vol / ask_vol) > self.max_bid_ask_ratio:
            deterministic_veto = True
            veto_reason = (
                f"Bid wall is {(bid_vol / ask_vol):.1f}x larger than Ask wall. "
                "Long squeeze risk."
            )

        if deterministic_veto:
            logger.warning("HARD VETO APPLIED for %s: %s", symbol, veto_reason)

        return deterministic_veto, {
            **self._observational_placeholder(
                "AI advisory pending — runs asynchronously after signal persistence."
            ),
            "deterministic_veto": deterministic_veto,
            "deterministic_reason": veto_reason,
        }

    async def advisory_for_decision(
        self,
        symbol: str,
        metrics: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        """Get AI advisory from Ollama for the given metrics."""
        opinion = await self._intel.get_advisory(metrics)
        return opinion.to_observational_advisory()

    async def get_observational_advisory(
        self,
        symbol: str,
        orderbook: dict[str, Any],
        ticker: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch the Ollama advisory without granting it veto authority."""
        opinion = await self._intel.get_advisory(
            {
                "symbol": symbol,
                "orderbook": orderbook,
                "ticker": ticker,
            }
        )
        advisory = opinion.to_observational_advisory()
        logger.info(
            "Ollama Advisory [%s]: %s (Conf: %s%%) | Reason: %s",
            symbol,
            advisory["ai_advice"],
            advisory["ai_confidence"],
            advisory["ai_reasoning"],
        )
        return advisory

    async def evaluate_symbol(
        self,
        symbol: str,
        orderbook: dict[str, Any],
        ticker: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Compatibility API for callers that want the full advisory."""
        deterministic_veto, advisory_data = self.evaluate_deterministic(
            symbol,
            orderbook,
            ticker,
        )
        if not orderbook or not ticker:
            return deterministic_veto, advisory_data

        advisory_data.update(
            await self.get_observational_advisory(
                symbol,
                orderbook,
                ticker,
            )
        )
        return deterministic_veto, advisory_data

    def status(self) -> dict[str, str]:
        """Return AI status for health checks."""
        return self._intel.status()


# Singleton instance
ai_veto = AIVetoEngine()
