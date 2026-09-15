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
        # CPU-only qwen can take longer than a web request even for a tiny
        # answer. This runs only for ENTRY_READY and the durable notification
        # worker allows 300s advisory grace, so 120s gives one legitimate
        # attempt without blocking the hunter or the signal path.
        self.timeout = 120.0
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

    async def get_advisory(
        self,
        metrics: dict[str, Any],
        decision: dict[str, Any] | None = None,
    ) -> AICascadeOpinion:
        """Fetch AI advisory from Ollama only."""
        try:
            prompt = self._build_prompt(metrics, decision)
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

    @staticmethod
    def _fmt(value: Any, digits: int = 4, suffix: str = "") -> str:
        if isinstance(value, bool) or value is None:
            return "n/a"
        if isinstance(value, (int, float)):
            if value != value or value in (float("inf"), float("-inf")):
                return "n/a"
            return f"{value:.{digits}f}{suffix}"
        text = str(value).strip()
        return text if text else "n/a"

    def _build_prompt(
        self,
        metrics: dict[str, Any],
        decision: dict[str, Any] | None = None,
    ) -> str:
        """Build the analysis prompt from the canonical decision packet.

        The previous prompt read ``readiness_score`` / ``coverage_score`` /
        ``structure_status`` / ``cascade_status`` / ``signal_summary``. None of
        those keys are produced anywhere, so every request told the model
        "Readiness 0/100, Cascade FAIL, Entry N/A" and the model correctly
        answered that the signal carried no information. This version reads
        the fields ``build_entry_decision`` and the validator actually emit.
        """
        packet = decision if isinstance(decision, dict) else {}
        if not packet:
            candidate = metrics.get("entry_decision")
            packet = candidate if isinstance(candidate, dict) else {}

        def rec(value: Any) -> dict[str, Any]:
            return value if isinstance(value, dict) else {}

        symbol = str(metrics.get("symbol") or packet.get("symbol") or "UNKNOWN")
        readiness = packet.get("entry_readiness")
        coverage = packet.get("evidence_coverage_pct")
        decision_label = str(packet.get("decision") or "UNAVAILABLE")
        lifecycle = str(packet.get("lifecycle_state") or metrics.get("status") or "n/a")
        reasons = packet.get("reason_codes")
        reason_text = ", ".join(str(r) for r in reasons[:10]) if isinstance(reasons, list) and reasons else "none"
        blocks = packet.get("block_reasons")
        block_text = ", ".join(str(b) for b in blocks) if isinstance(blocks, list) and blocks else "none"

        cascade = rec(metrics.get("cascade_intelligence"))
        evidence = rec(packet.get("evidence_summary"))
        ev_deriv = rec(evidence.get("derivatives"))
        ev_flow = rec(evidence.get("order_flow"))
        ev_exec = rec(evidence.get("execution"))
        plan = rec(packet.get("trade_plan"))
        candles = rec(metrics.get("candle_features"))
        h4 = rec(candles.get("4h"))
        h1 = rec(candles.get("1h"))

        f = self._fmt
        # Keep the CPU prompt compact. The former version was ~500 tokens and
        # repeated qualitative explanations the model does not need; on this
        # host that multiplied prompt-evaluation time and starved the advisory
        # queue. Every value is still canonical and immutable for the decision.
        prompt = f"""Short-signal second opinion. Advisory only; never overrides engine.
sym={symbol} decision={decision_label} lifecycle={lifecycle}
readiness={f(readiness, 1)} coverage={f(coverage, 1)} blocks={block_text}
cascade={f(cascade.get("status"))}:{f(cascade.get("readiness_points"), 1)}/{f(cascade.get("maximum_available"), 1)} taker={f(ev_flow.get("taker_buy_sell_ratio"), 3)} sell%={f(ev_flow.get("sell_share_pct"), 1)} oi1h%={f(ev_deriv.get("oi_change_1h_pct"), 2)} funding%={f(ev_deriv.get("funding_rate_pct"), 4)} spread%={f(ev_exec.get("spread_pct"), 3)} cross={f(evidence.get("cross_exchange_confirmed"))} extATR={f(evidence.get("anti_chase_extension_atr"), 2)}
4h lower_high={f(h4.get("lower_high"))} failed_pullback={f(h4.get("setup") == "FAILED_PULLBACK")} bearish={f(h4.get("bearish_close"))}; 1h lower_high={f(h1.get("lower_high"))} rsi_rollover={f(h1.get("rsi_rollover"))} bearish={f(h1.get("bearish_close"))}
plan entry={f(plan.get("entry_price"), 6)} stop={f(plan.get("stop_loss"), 6)} tp1={f(plan.get("take_profit_1"), 6)} tp2={f(plan.get("take_profit_2"), 6)} rr={f(plan.get("reward_to_risk"), 2)}
JSON only. `score` must be an integer confidence from 0 through 100; `note`
must be 12 words or fewer and name the strongest factor:
{{"verified":true,"note":"Bearish structure and order flow support the short","score":75}}
"""
        return prompt

    async def _request_ollama(self, prompt: str) -> dict[str, Any] | None:
        """Call Ollama API."""
        payload = {
            "model": self.ollama_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            # Force JSON at the provider boundary and bound generation. The
            # old advisory prompt merely *asked* for a tiny JSON object, so a
            # CPU model could still spend tens of seconds generating prose
            # before failing the parser. This contract needs one sentence and
            # one score, not an essay.
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 96},
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
        opinion = await self._intel.get_advisory(
            {**metrics, "symbol": symbol},
            decision,
        )
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
