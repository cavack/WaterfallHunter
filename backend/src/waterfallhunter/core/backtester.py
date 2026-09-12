"""
backtester.py — WaterfallHunter Crypto Signal Backtester
=========================================================

An automatic backtester with $200 initial capital that learns from its mistakes.

Features:
  - Capital management with 2% risk-per-trade position sizing
  - Trade simulation using historical candle data (TP1/TP2/TP3 partial closes, SL)
  - Self-learning mistake journal (SQLite) with calibration adjustments
  - Performance metrics (Sharpe, max drawdown, profit factor, expectancy)
  - Equity curve tracking
  - HTML report generation for Telegram

Usage:
    from waterfallhunter.core.backtester import Backtester

    bt = Backtester(db_path="/app/data/backtest.db")
    await bt.run_signal_trade(symbol="BTC/USDT:USDT", signal_data={...}, candle_history=[...])
    report = bt.generate_report()

Author: WaterfallHunter Team
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("waterfallhunter.backtester")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(name)s][%(levelname)s] %(message)s"))
    logger.addHandler(_h)


# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

INITIAL_CAPITAL: float = 200.0
RISK_PER_TRADE_PCT: float = 2.0           # 2% of current capital per trade
DEFAULT_ENTRY_READY_MINIMUM: float = 70.0  # baseline readiness threshold
DEFAULT_CALIBRATION_ADJUSTMENT: float = 0.0

# Partial-close percentages for TP levels
TP1_CLOSE_PCT = 0.50   # close 50% at TP1
TP2_CLOSE_PCT = 0.25   # close 25% at TP2
TP3_CLOSE_PCT = 0.25   # close remaining 25% at TP3


# ──────────────────────────────────────────────
# Enums & Dataclasses
# ──────────────────────────────────────────────

class TradeOutcome(str, Enum):
    WIN_TP1 = "win_tp1"
    WIN_TP2 = "win_tp2"
    WIN_TP3 = "win_tp3"
    LOSS_SL = "loss_sl"
    LOSS_TIMEOUT = "loss_timeout"
    BREAKEVEN = "breakeven"


class SignalStatus(str, Enum):
    ENTRY_READY = "ENTRY_READY"
    ENTRY_BLOCKED = "ENTRY_BLOCKED"
    PROTOCOL_VIOLATION = "PROTOCOL_VIOLATION"


@dataclass
class TradePlan:
    """Extracted from signal_data.trade_plan."""
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float

    @classmethod
    def from_signal(cls, signal_data: dict) -> "TradePlan":
        tp = signal_data.get("trade_plan", {})
        return cls(
            entry_price=float(tp["entry_price"]),
            stop_loss=float(tp["stop_loss"]),
            take_profit_1=float(tp["take_profit_1"]),
            take_profit_2=float(tp["take_profit_2"]),
            take_profit_3=float(tp["take_profit_3"]),
        )


@dataclass
class TradeResult:
    trade_id: int
    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_usd: float
    outcome: str
    signal_data: dict
    position_size: float = 0.0
    shares_traded: float = 0.0


@dataclass
class PerformanceMetrics:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    current_capital: float = INITIAL_CAPITAL
    total_pnl: float = 0.0
    buy_hold_return_pct: float = 0.0


# ──────────────────────────────────────────────
# SQLite Schema
# ──────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    entry_time      TEXT NOT NULL,
    exit_time       TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    exit_price      REAL NOT NULL,
    pnl_pct         REAL NOT NULL,
    pnl_usd         REAL NOT NULL,
    outcome         TEXT NOT NULL,
    position_size   REAL NOT NULL DEFAULT 0,
    shares_traded   REAL NOT NULL DEFAULT 0,
    signal_data     TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS backtest_mistakes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER NOT NULL,
    pattern_type    TEXT NOT NULL,
    description     TEXT NOT NULL,
    adjustment      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (trade_id) REFERENCES backtest_trades(id)
);

CREATE TABLE IF NOT EXISTS backtest_equity (
    timestamp       TEXT NOT NULL,
    capital         REAL NOT NULL,
    trade_count     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_calibration (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    key_name        TEXT UNIQUE NOT NULL,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol ON backtest_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_outcome ON backtest_trades(outcome);
CREATE INDEX IF NOT EXISTS idx_mistakes_trade ON backtest_mistakes(trade_id);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON backtest_equity(timestamp);
"""


# ──────────────────────────────────────────────
# Backtester
# ──────────────────────────────────────────────

class Backtester:
    """
    Automatic backtester for the WaterfallHunter crypto signal system.

    - Starts with $200 initial capital.
    - Risks 2% of current capital per trade.
    - Simulates trades against historical candle data.
    - Learns from losing trades and adjusts calibration.
    - Generates HTML reports for Telegram.
    """

    def __init__(
        self,
        db_path: str = "/app/data/backtest.db",
        initial_capital: float = INITIAL_CAPITAL,
        risk_per_trade_pct: float = RISK_PER_TRADE_PCT,
        entry_ready_minimum: float = DEFAULT_ENTRY_READY_MINIMUM,
    ) -> None:
        self.db_path = db_path
        self.initial_capital = float(initial_capital)
        self.risk_per_trade_pct = float(risk_per_trade_pct)
        self.entry_ready_minimum = float(entry_ready_minimum)

        # In-memory state (loaded from DB)
        self.current_capital: float = self.initial_capital
        self.total_trades: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.max_drawdown: float = 0.0
        self.peak_capital: float = self.initial_capital
        self.equity_curve: list[dict] = []

        # Mistake journal in-memory cache
        self._mistake_patterns: dict[str, int] = {}

        # Calibration
        self.calibration_adjustment: float = DEFAULT_CALIBRATION_ADJUSTMENT

        # Ensure DB is ready
        self._ensure_db()

    # ──────────────────────────────────────────────
    # Database
    # ──────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite connection with row factory."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_db(self) -> None:
        """Create tables and load in-memory state."""
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()
        self._load_state()
        self._load_calibration()

    def _load_state(self) -> None:
        """Load capital, trade counts, and equity curve from DB."""
        with self._connect() as conn:
            # Load equity curve
            rows = conn.execute(
                "SELECT timestamp, capital, trade_count FROM backtest_equity ORDER BY timestamp ASC"
            ).fetchall()
            if rows:
                self.equity_curve = [dict(r) for r in rows]
                latest = rows[-1]
                self.current_capital = float(latest["capital"])
                self.total_trades = int(latest["trade_count"])
            else:
                self.current_capital = self.initial_capital
                self.total_trades = 0
                self._record_equity()

            # Count wins / losses
            self.wins = conn.execute(
                "SELECT COUNT(*) FROM backtest_trades WHERE outcome LIKE 'win_%'"
            ).fetchone()[0]
            self.losses = conn.execute(
                "SELECT COUNT(*) FROM backtest_trades WHERE outcome LIKE 'loss_%'"
            ).fetchone()[0]

            # Max drawdown from equity curve
            peak = self.initial_capital
            max_dd = 0.0
            for point in self.equity_curve:
                cap = float(point["capital"])
                if cap > peak:
                    peak = cap
                dd = peak - cap
                if dd > max_dd:
                    max_dd = dd
            self.max_drawdown = max_dd
            self.peak_capital = peak

            # Load mistake patterns
            mistake_rows = conn.execute(
                "SELECT pattern_type, COUNT(*) as cnt FROM backtest_mistakes GROUP BY pattern_type"
            ).fetchall()
            self._mistake_patterns = {r["pattern_type"]: int(r["cnt"]) for r in mistake_rows}

    def _load_calibration(self) -> None:
        """Load calibration adjustment from DB."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM backtest_calibration WHERE key_name = 'calibration_adjustment'"
            ).fetchone()
            if row:
                self.calibration_adjustment = float(row["value"])
            row2 = conn.execute(
                "SELECT value FROM backtest_calibration WHERE key_name = 'entry_ready_minimum'"
            ).fetchone()
            if row2:
                self.entry_ready_minimum = float(row2["value"])

    def _save_calibration(self) -> None:
        """Persist calibration values."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            for key, val in [
                ("calibration_adjustment", str(self.calibration_adjustment)),
                ("entry_ready_minimum", str(self.entry_ready_minimum)),
            ]:
                conn.execute(
                    """
                    INSERT INTO backtest_calibration (key_name, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key_name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (key, val, now),
                )
            conn.commit()

    def _record_equity(self) -> None:
        """Record current capital in the equity curve table."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO backtest_equity (timestamp, capital, trade_count) VALUES (?, ?, ?)",
                (now, self.current_capital, self.total_trades),
            )
            conn.commit()
        self.equity_curve.append({
            "timestamp": now,
            "capital": self.current_capital,
            "trade_count": self.total_trades,
        })

    # ──────────────────────────────────────────────
    # Position Sizing
    # ──────────────────────────────────────────────

    def calculate_position_size(self, entry_price: float, stop_loss: float) -> tuple[float, float]:
        """
        Calculate position size based on stop-loss distance.

        size (shares) = risk_amount / (entry - stop_loss)
        Returns (risk_amount_usd, shares).
        """
        risk_amount = self.current_capital * (self.risk_per_trade_pct / 100.0)
        sl_distance = abs(entry_price - stop_loss)

        if sl_distance <= 0:
            logger.warning("Stop-loss distance is zero or negative; skipping trade.")
            return (0.0, 0.0)

        shares = risk_amount / sl_distance
        return (risk_amount, shares)

    # ──────────────────────────────────────────────
    # Trade Simulation
    # ──────────────────────────────────────────────

    def _simulate_trade(
        self,
        entry_price: float,
        stop_loss: float,
        tp1: float,
        tp2: float,
        tp3: float,
        shares: float,
        candle_history: list[dict],
    ) -> tuple[float, float, str, str]:
        """
        Walk through candle_history to determine trade outcome.

        Returns: (exit_price, pnl_usd, outcome, exit_time)
        """
        is_long = entry_price > stop_loss  # long if SL below entry

        remaining_shares = shares
        realized_pnl = 0.0
        moved_sl_to_entry = False
        exit_time = ""

        tp1_hit = False
        tp2_hit = False
        tp3_hit = False
        sl_hit = False

        for candle in candle_history:
            high = float(candle.get("high", candle.get("h", 0)))
            low = float(candle.get("low", candle.get("l", 0)))
            close = float(candle.get("close", candle.get("c", 0)))
            ts = candle.get("timestamp", candle.get("time", candle.get("date", "")))
            if ts:
                exit_time = str(ts)

            current_sl = entry_price if moved_sl_to_entry else stop_loss

            # Determine which level was hit first in this candle
            # Check SL first (conservative: assume SL hit first if both hit in same candle)
            if is_long:
                if low <= current_sl:
                    sl_hit = True
                    break
                if not tp1_hit and high >= tp1:
                    tp1_hit = True
                    pnl_partial = (tp1 - entry_price) * remaining_shares * TP1_CLOSE_PCT
                    realized_pnl += pnl_partial
                    remaining_shares *= (1 - TP1_CLOSE_PCT)
                    moved_sl_to_entry = True
                    continue
                if tp1_hit and not tp2_hit and high >= tp2:
                    tp2_hit = True
                    pnl_partial = (tp2 - entry_price) * remaining_shares * TP2_CLOSE_PCT
                    realized_pnl += pnl_partial
                    remaining_shares *= (1 - TP2_CLOSE_PCT)
                    continue
                if tp2_hit and not tp3_hit and high >= tp3:
                    tp3_hit = True
                    pnl_partial = (tp3 - entry_price) * remaining_shares
                    realized_pnl += pnl_partial
                    remaining_shares = 0.0
                    break
            else:
                # Short trade
                if high >= current_sl:
                    sl_hit = True
                    break
                if not tp1_hit and low <= tp1:
                    tp1_hit = True
                    pnl_partial = (entry_price - tp1) * remaining_shares * TP1_CLOSE_PCT
                    realized_pnl += pnl_partial
                    remaining_shares *= (1 - TP1_CLOSE_PCT)
                    moved_sl_to_entry = True
                    continue
                if tp1_hit and not tp2_hit and low <= tp2:
                    tp2_hit = True
                    pnl_partial = (entry_price - tp2) * remaining_shares * TP2_CLOSE_PCT
                    realized_pnl += pnl_partial
                    remaining_shares *= (1 - TP2_CLOSE_PCT)
                    continue
                if tp2_hit and not tp3_hit and low <= tp3:
                    tp3_hit = True
                    pnl_partial = (entry_price - tp3) * remaining_shares
                    realized_pnl += pnl_partial
                    remaining_shares = 0.0
                    break

        # Determine outcome
        if sl_hit:
            outcome = TradeOutcome.LOSS_SL
            exit_price = current_sl
            # Realize remaining shares at SL
            if remaining_shares > 0:
                if is_long:
                    realized_pnl += (exit_price - entry_price) * remaining_shares
                else:
                    realized_pnl += (entry_price - exit_price) * remaining_shares
        elif tp3_hit:
            outcome = TradeOutcome.WIN_TP3
            exit_price = tp3
        elif tp2_hit:
            outcome = TradeOutcome.WIN_TP2
            exit_price = tp2
            # Close remaining at current candle close
            if remaining_shares > 0:
                if is_long:
                    realized_pnl += (close - entry_price) * remaining_shares
                else:
                    realized_pnl += (entry_price - close) * remaining_shares
        elif tp1_hit:
            outcome = TradeOutcome.WIN_TP1
            exit_price = tp1
            if remaining_shares > 0:
                if is_long:
                    realized_pnl += (close - entry_price) * remaining_shares
                else:
                    realized_pnl += (entry_price - close) * remaining_shares
        else:
            # Timeout — neither SL nor any TP hit
            outcome = TradeOutcome.LOSS_TIMEOUT
            exit_price = close
            if remaining_shares > 0:
                if is_long:
                    realized_pnl += (close - entry_price) * remaining_shares
                else:
                    realized_pnl += (entry_price - close) * remaining_shares

        pnl_pct = (realized_pnl / (shares * entry_price)) * 100 if shares > 0 and entry_price > 0 else 0.0

        return (exit_price, realized_pnl, outcome.value, exit_time, pnl_pct)

    # ──────────────────────────────────────────────
    # Signal Trade Execution
    # ──────────────────────────────────────────────

    async def run_signal_trade(
        self,
        symbol: str,
        signal_data: dict,
        candle_history: list[dict],
    ) -> Optional[TradeResult]:
        """
        Simulate a trade from a WaterfallHunter signal.

        Args:
            symbol: Trading pair (e.g. "BTC/USDT:USDT")
            signal_data: Signal dict containing trade_plan, readiness_score, etc.
            candle_history: List of candle dicts with high/low/close keys.

        Returns:
            TradeResult or None if trade was skipped.
        """
        try:
            # Check signal status
            status = signal_data.get("status", "")
            if status and status != SignalStatus.ENTRY_READY.value:
                logger.info(f"Signal status '{status}' — skipping trade for {symbol}")
                return None

            # Check readiness with calibration
            readiness = float(signal_data.get("readiness_score", 0))
            adjusted_threshold = self.entry_ready_minimum + self.calibration_adjustment
            if readiness < adjusted_threshold:
                logger.info(
                    f"Readiness {readiness} < adjusted threshold {adjusted_threshold} — skipping {symbol}"
                )
                return None

            # Extract trade plan
            trade_plan = TradePlan.from_signal(signal_data)

            # Calculate position size
            risk_amount, shares = self.calculate_position_size(
                trade_plan.entry_price, trade_plan.stop_loss
            )
            if shares <= 0:
                logger.warning(f"Position size is zero — skipping trade for {symbol}")
                return None

            # Simulate trade
            entry_time = datetime.now(timezone.utc).isoformat()
            exit_price, pnl_usd, outcome_str, exit_time, pnl_pct = self._simulate_trade(
                trade_plan.entry_price,
                trade_plan.stop_loss,
                trade_plan.take_profit_1,
                trade_plan.take_profit_2,
                trade_plan.take_profit_3,
                shares,
                candle_history,
            )

            # Determine win/loss
            outcome = TradeOutcome(outcome_str)
            is_win = outcome.value.startswith("win_")

            # Record trade in DB
            trade_id = self._record_trade(
                symbol=symbol,
                entry_time=entry_time,
                exit_time=exit_time or entry_time,
                entry_price=trade_plan.entry_price,
                exit_price=exit_price,
                pnl_pct=pnl_pct,
                pnl_usd=pnl_usd,
                outcome=outcome_str,
                position_size=risk_amount,
                shares_traded=shares,
                signal_data=signal_data,
            )

            # Update capital
            self.current_capital += pnl_usd
            self.total_trades += 1
            if is_win:
                self.wins += 1
            else:
                self.losses += 1

            # Update drawdown
            if self.current_capital > self.peak_capital:
                self.peak_capital = self.current_capital
            dd = self.peak_capital - self.current_capital
            if dd > self.max_drawdown:
                self.max_drawdown = dd

            # Record equity point
            self._record_equity()

            # Learn from mistakes if losing trade
            if not is_win:
                self._record_mistakes(trade_id, signal_data, outcome)

            # Recalibrate if we have enough data
            if self.total_trades % 10 == 0:
                self._recalibrate()

            result = TradeResult(
                trade_id=trade_id,
                symbol=symbol,
                entry_time=entry_time,
                exit_time=exit_time or entry_time,
                entry_price=trade_plan.entry_price,
                exit_price=exit_price,
                pnl_pct=pnl_pct,
                pnl_usd=pnl_usd,
                outcome=outcome_str,
                signal_data=signal_data,
                position_size=risk_amount,
                shares_traded=shares,
            )

            logger.info(
                f"Trade #{trade_id} {symbol}: {outcome_str} | "
                f"PnL: ${pnl_usd:.2f} ({pnl_pct:.2f}%) | "
                f"Capital: ${self.current_capital:.2f}"
            )

            return result

        except Exception as e:
            logger.error(f"Error running signal trade for {symbol}: {e}", exc_info=True)
            return None

    def _record_trade(
        self,
        symbol: str,
        entry_time: str,
        exit_time: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        pnl_usd: float,
        outcome: str,
        position_size: float,
        shares_traded: float,
        signal_data: dict,
    ) -> int:
        """Record a completed trade in the database. Returns trade ID."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO backtest_trades
                    (symbol, entry_time, exit_time, entry_price, exit_price,
                     pnl_pct, pnl_usd, outcome, position_size, shares_traded, signal_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol, entry_time, exit_time, entry_price, exit_price,
                    pnl_pct, pnl_usd, outcome, position_size, shares_traded,
                    json.dumps(signal_data, default=str),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    # ──────────────────────────────────────────────
    # Self-Learning: Mistake Journal
    # ──────────────────────────────────────────────

    def _record_mistakes(self, trade_id: int, signal_data: dict, outcome: TradeOutcome) -> None:
        """Record mistake patterns from a losing trade."""
        now = datetime.now(timezone.utc).isoformat()
        mistakes: list[tuple[str, str, str]] = []

        readiness = float(signal_data.get("readiness_score", 0))
        cascade = signal_data.get("cascade_status", "unknown")
        cross_exchange = signal_data.get("cross_exchange", False)
        timing = signal_data.get("timing", "unknown")
        anti_chase = signal_data.get("anti_chase_extension", 0)
        btc_trend = signal_data.get("market_conditions", {}).get("btc_trend", "unknown")
        market_sentiment = signal_data.get("market_conditions", {}).get("overall_market_sentiment", "unknown")

        # Pattern: low readiness
        if readiness < 75:
            desc = f"Readiness score {readiness:.1f} below 75 — signal too weak."
            adj = f"Consider raising entry_ready_minimum by +1"
            mistakes.append(("low_readiness", desc, adj))
            self._mistake_patterns["low_readiness"] = self._mistake_patterns.get("low_readiness", 0) + 1

        # Pattern: cross_exchange False
        if not cross_exchange:
            desc = "cross_exchange=False — signal not confirmed across exchanges."
            adj = "Increase weight of cross_exchange confirmation."
            mistakes.append(("cross_exchange_false", desc, adj))
            self._mistake_patterns["cross_exchange_false"] = self._mistake_patterns.get("cross_exchange_false", 0) + 1

        # Pattern: cascade incomplete
        if cascade and cascade != "complete":
            desc = f"cascade_status='{cascade}' — cascade not fully formed."
            adj = "Require full cascade before entry."
            mistakes.append(("cascade_incomplete", desc, adj))
            self._mistake_patterns["cascade_incomplete"] = self._mistake_patterns.get("cascade_incomplete", 0) + 1

        # Pattern: bad timing
        if timing and timing not in ("good", "optimal"):
            desc = f"timing='{timing}' — poor entry timing."
            adj = "Add timing filter to block suboptimal entries."
            mistakes.append(("bad_timing", desc, adj))
            self._mistake_patterns["bad_timing"] = self._mistake_patterns.get("bad_timing", 0) + 1

        # Pattern: anti-chase extension high (chasing)
        try:
            anti_chase_val = float(anti_chase)
            if anti_chase_val > 0.5:
                desc = f"anti_chase_extension={anti_chase_val} — signal likely chasing price."
                adj = "Block entries when anti_chase_extension > 0.5."
                mistakes.append(("chasing_price", desc, adj))
                self._mistake_patterns["chasing_price"] = self._mistake_patterns.get("chasing_price", 0) + 1
        except (TypeError, ValueError):
            pass

        # Pattern: adverse market conditions
        if btc_trend == "bearish" or market_sentiment == "fearful":
            desc = f"Market adverse: BTC trend={btc_trend}, sentiment={market_sentiment}."
            adj = "Add market regime filter."
            mistakes.append(("adverse_market", desc, adj))
            self._mistake_patterns["adverse_market"] = self._mistake_patterns.get("adverse_market", 0) + 1

        # Pattern: timeout loss (signal never hit any TP)
        if outcome == TradeOutcome.LOSS_TIMEOUT:
            desc = "Trade timed out without hitting any TP or SL — signal lacked momentum."
            adj = "Reduce holding period or tighten TPs."
            mistakes.append(("timeout_loss", desc, adj))
            self._mistake_patterns["timeout_loss"] = self._mistake_patterns.get("timeout_loss", 0) + 1

        # Record all mistakes
        with self._connect() as conn:
            for pattern_type, description, adjustment in mistakes:
                conn.execute(
                    """
                    INSERT INTO backtest_mistakes
                        (trade_id, pattern_type, description, adjustment, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (trade_id, pattern_type, description, adjustment, now),
                )
            conn.commit()

        logger.info(
            f"Recorded {len(mistakes)} mistake patterns for trade #{trade_id}. "
            f"Total patterns: {self._mistake_patterns}"
        )

    def _recalibrate(self) -> None:
        """
        Adjust calibration based on accumulated mistake patterns.

        - If losing trades cluster around low readiness scores, raise the threshold.
        - If cross_exchange=False is a frequent pattern, increase calibration adjustment.
        """
        total_mistakes = sum(self._mistake_patterns.values())
        if total_mistakes == 0:
            return

        old_adjustment = self.calibration_adjustment
        old_threshold = self.entry_ready_minimum

        # Low readiness pattern: raise threshold
        low_readiness_count = self._mistake_patterns.get("low_readiness", 0)
        if low_readiness_count >= 3:
            ratio = low_readiness_count / max(self.losses, 1)
            if ratio > 0.3:
                self.entry_ready_minimum = min(85.0, self.entry_ready_minimum + 1.0)
                logger.info(
                    f"Calibration: raising entry_ready_minimum to {self.entry_ready_minimum} "
                    f"(low_readiness pattern: {low_readiness_count}/{self.losses} losses)"
                )

        # Cross_exchange False pattern: increase calibration adjustment
        cross_ex_count = self._mistake_patterns.get("cross_exchange_false", 0)
        if cross_ex_count >= 3:
            ratio = cross_ex_count / max(self.losses, 1)
            if ratio > 0.3:
                self.calibration_adjustment = min(10.0, self.calibration_adjustment + 0.5)
                logger.info(
                    f"Calibration: raising calibration_adjustment to {self.calibration_adjustment} "
                    f"(cross_exchange_false pattern: {cross_ex_count}/{self.losses} losses)"
                )

        # Chasing price pattern: raise threshold
        chasing_count = self._mistake_patterns.get("chasing_price", 0)
        if chasing_count >= 3:
            self.entry_ready_minimum = min(85.0, self.entry_ready_minimum + 0.5)
            logger.info(
                f"Calibration: raising entry_ready_minimum to {self.entry_ready_minimum} "
                f"(chasing_price pattern: {chasing_count} occurrences)"
            )

        # Bad timing pattern: raise threshold
        timing_count = self._mistake_patterns.get("bad_timing", 0)
        if timing_count >= 3:
            self.entry_ready_minimum = min(85.0, self.entry_ready_minimum + 0.5)
            logger.info(
                f"Calibration: raising entry_ready_minimum to {self.entry_ready_minimum} "
                f"(bad_timing pattern: {timing_count} occurrences)"
            )

        # If win rate is improving, slightly relax thresholds
        if self.total_trades >= 20:
            win_rate = (self.wins / self.total_trades) * 100
            if win_rate >= 55:
                self.entry_ready_minimum = max(
                    DEFAULT_ENTRY_READY_MINIMUM,
                    self.entry_ready_minimum - 0.5,
                )
                self.calibration_adjustment = max(0.0, self.calibration_adjustment - 0.2)
                logger.info(
                    f"Calibration: win rate {win_rate:.1f}% is healthy — "
                    f"relaxing thresholds slightly."
                )

        # Save calibration
        if old_adjustment != self.calibration_adjustment or old_threshold != self.entry_ready_minimum:
            self._save_calibration()

    # ──────────────────────────────────────────────
    # Performance Metrics
    # ──────────────────────────────────────────────

    def compute_metrics(self, buy_hold_btc_start: Optional[float] = None) -> PerformanceMetrics:
        """
        Compute comprehensive performance metrics.

        Args:
            buy_hold_btc_start: Optional BTC price at start for buy-and-hold comparison.
                             If None, comparison is skipped.

        Returns:
            PerformanceMetrics dataclass.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pnl_usd, pnl_pct, outcome FROM backtest_trades ORDER BY id ASC"
            ).fetchall()

        if not rows:
            return PerformanceMetrics(current_capital=self.current_capital)

        pnls = [float(r["pnl_usd"]) for r in rows]
        win_pnls = [p for p in pnls if p > 0]
        loss_pnls = [p for p in pnls if p < 0]

        wins = len(win_pnls)
        losses = len(loss_pnls)
        total = len(pnls)
        win_rate = (wins / total) * 100 if total > 0 else 0.0

        avg_win = statistics.mean(win_pnls) if win_pnls else 0.0
        avg_loss = statistics.mean(loss_pnls) if loss_pnls else 0.0

        gross_profit = sum(win_pnls)
        gross_loss = abs(sum(loss_pnls))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Expectancy: average PnL per trade
        expectancy = statistics.mean(pnls) if pnls else 0.0

        # Sharpe ratio (simplified, using per-trade returns)
        if len(pnls) >= 2:
            std_dev = statistics.stdev(pnls)
            if std_dev > 0:
                # Annualized approximation: mean / std * sqrt(trades)
                sharpe = (statistics.mean(pnls) / std_dev) * math.sqrt(total)
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        # Max drawdown from equity curve
        peak = self.initial_capital
        max_dd = 0.0
        max_dd_pct = 0.0
        for point in self.equity_curve:
            cap = float(point["capital"])
            if cap > peak:
                peak = cap
            dd = peak - cap
            dd_pct = (dd / peak) * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

        total_pnl = sum(pnls)

        # Buy-and-hold BTC comparison
        buy_hold_return = 0.0
        if buy_hold_btc_start and buy_hold_btc_start > 0:
            # Get current BTC price from the latest candle in last trade's signal_data
            with self._connect() as conn:
                last_trade = conn.execute(
                    "SELECT signal_data FROM backtest_trades ORDER BY id DESC LIMIT 1"
                ).fetchone()
            if last_trade:
                try:
                    sd = json.loads(last_trade["signal_data"])
                    # Try to extract current BTC price from signal data
                    current_btc = sd.get("market_conditions", {}).get("btc_price")
                    if current_btc:
                        buy_hold_return = ((float(current_btc) - buy_hold_btc_start) / buy_hold_btc_start) * 100
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

        return PerformanceMetrics(
            total_trades=total,
            wins=wins,
            losses=losses,
            win_rate=round(win_rate, 2),
            average_win=round(avg_win, 4),
            average_loss=round(avg_loss, 4),
            profit_factor=round(profit_factor, 4) if profit_factor != float("inf") else float("inf"),
            expectancy=round(expectancy, 4),
            sharpe_ratio=round(sharpe, 4),
            max_drawdown=round(max_dd, 4),
            max_drawdown_pct=round(max_dd_pct, 2),
            current_capital=round(self.current_capital, 4),
            total_pnl=round(total_pnl, 4),
            buy_hold_return_pct=round(buy_hold_return, 2),
        )

    # ──────────────────────────────────────────────
    # Mistake Analysis
    # ──────────────────────────────────────────────

    def get_top_mistakes(self, limit: int = 10) -> list[dict]:
        """Get the most frequent mistake patterns."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    pattern_type,
                    COUNT(*) as count,
                    GROUP_CONCAT(DISTINCT description) as descriptions,
                    GROUP_CONCAT(DISTINCT adjustment) as adjustments
                FROM backtest_mistakes
                GROUP BY pattern_type
                ORDER BY count DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_weekly_mistake_report(self) -> list[dict]:
        """Get mistakes from the last 7 days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    m.pattern_type,
                    m.description,
                    m.adjustment,
                    m.created_at,
                    t.symbol,
                    t.outcome,
                    t.pnl_usd
                FROM backtest_mistakes m
                JOIN backtest_trades t ON m.trade_id = t.id
                WHERE m.created_at >= ?
                ORDER BY m.created_at DESC
                """,
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ──────────────────────────────────────────────
    # Report Generation
    # ──────────────────────────────────────────────

    def generate_report(self, buy_hold_btc_start: Optional[float] = None) -> str:
        """
        Generate an HTML summary report suitable for Telegram.

        Args:
            buy_hold_btc_start: Optional BTC price at start for buy-and-hold comparison.

        Returns:
            HTML string.
        """
        metrics = self.compute_metrics(buy_hold_btc_start)
        top_mistakes = self.get_top_mistakes(limit=5)
        weekly_mistakes = self.get_weekly_mistake_report()

        # Build equity curve description
        if self.equity_curve:
            start_cap = self.equity_curve[0]["capital"]
            end_cap = self.equity_curve[-1]["capital"]
            equity_desc = f"${start_cap:.2f} → ${end_cap:.2f}"
            n_points = len(self.equity_curve)
        else:
            equity_desc = "No equity data yet"
            n_points = 0

        # Calibration suggestions
        calib_suggestions = []
        for mistake in top_mistakes:
            if mistake.get("adjustments"):
                calib_suggestions.append({
                    "pattern": mistake["pattern_type"],
                    "count": mistake["count"],
                    "suggestion": mistake["adjustments"].split(",")[0] if mistake["adjustments"] else "",
                })

        # Build HTML
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WaterfallHunter Backtest Report</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d1117;
            color: #c9d1d9;
            margin: 0;
            padding: 20px;
            max-width: 720px;
        }}
        .header {{
            text-align: center;
            padding: 20px 0;
            border-bottom: 1px solid #30363d;
        }}
        .header h1 {{
            color: #58a6ff;
            font-size: 24px;
            margin: 0;
        }}
        .header .subtitle {{
            color: #8b949e;
            font-size: 14px;
            margin-top: 5px;
        }}
        .section {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 16px;
            margin: 12px 0;
        }}
        .section h2 {{
            color: #58a6ff;
            font-size: 18px;
            margin: 0 0 12px 0;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
        }}
        .metric {{
            background: #21262d;
            border-radius: 6px;
            padding: 10px;
        }}
        .metric .label {{
            color: #8b949e;
            font-size: 12px;
            text-transform: uppercase;
        }}
        .metric .value {{
            color: #c9d1d9;
            font-size: 20px;
            font-weight: bold;
            margin-top: 4px;
        }}
        .metric .value.positive {{ color: #3fb950; }}
        .metric .value.negative {{ color: #f85149; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}
        th, td {{
            text-align: left;
            padding: 8px;
            border-bottom: 1px solid #30363d;
        }}
        th {{
            color: #8b949e;
            text-transform: uppercase;
            font-size: 11px;
        }}
        .equity-desc {{
            color: #3fb950;
            font-size: 22px;
            font-weight: bold;
        }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: bold;
        }}
        .badge.win {{ background: #1a4326; color: #3fb950; }}
        .badge.loss {{ background: #4a1e1e; color: #f85149; }}
        .calibration {{
            background: #1a1f2e;
            border-left: 3px solid #58a6ff;
            padding: 12px;
            margin: 8px 0;
            border-radius: 4px;
        }}
        .footer {{
            text-align: center;
            color: #484f58;
            font-size: 12px;
            padding: 20px 0;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 WaterfallHunter Backtest Report</h1>
        <div class="subtitle">Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</div>
    </div>

    <div class="section">
        <h2>💰 Capital Overview</h2>
        <div class="metrics-grid">
            <div class="metric">
                <div class="label">Starting Capital</div>
                <div class="value">${self.initial_capital:.2f}</div>
            </div>
            <div class="metric">
                <div class="label">Current Capital</div>
                <div class="value {'positive' if metrics.current_capital > self.initial_capital else 'negative'}">
                    ${metrics.current_capital:.2f}
                </div>
            </div>
            <div class="metric">
                <div class="label">Total PnL</div>
                <div class="value {'positive' if metrics.total_pnl > 0 else 'negative'}">
                    ${metrics.total_pnl:.2f}
                </div>
            </div>
            <div class="metric">
                <div class="label">Return %</div>
                <div class="value {'positive' if metrics.total_pnl > 0 else 'negative'}">
                    {((metrics.current_capital / self.initial_capital) - 1) * 100:.2f}%
                </div>
            </div>
        </div>
        <div style="margin-top: 12px;">
            <strong>Equity Curve:</strong> <span class="equity-desc">{equity_desc}</span>
            <span style="color:#8b949e; font-size:12px;">({n_points} data points)</span>
        </div>
    </div>

    <div class="section">
        <h2>📈 Trade Performance</h2>
        <div class="metrics-grid">
            <div class="metric">
                <div class="label">Total Trades</div>
                <div class="value">{metrics.total_trades}</div>
            </div>
            <div class="metric">
                <div class="label">Win Rate</div>
                <div class="value {'positive' if metrics.win_rate >= 50 else 'negative'}">
                    {metrics.win_rate:.1f}%
                </div>
            </div>
            <div class="metric">
                <div class="label">Wins</div>
                <div class="value positive">{metrics.wins}</div>
            </div>
            <div class="metric">
                <div class="label">Losses</div>
                <div class="value negative">{metrics.losses}</div>
            </div>
            <div class="metric">
                <div class="label">Avg Win</div>
                <div class="value positive">${metrics.average_win:.2f}</div>
            </div>
            <div class="metric">
                <div class="label">Avg Loss</div>
                <div class="value negative">${metrics.average_loss:.2f}</div>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>📊 Risk Metrics</h2>
        <div class="metrics-grid">
            <div class="metric">
                <div class="label">Sharpe Ratio</div>
                <div class="value">{metrics.sharpe_ratio:.2f}</div>
            </div>
            <div class="metric">
                <div class="label">Profit Factor</div>
                <div class="value {'positive' if metrics.profit_factor >= 1 else 'negative'}">
                    {metrics.profit_factor:.2f}
                </div>
            </div>
            <div class="metric">
                <div class="label">Expectancy</div>
                <div class="value {'positive' if metrics.expectancy >= 0 else 'negative'}">
                    ${metrics.expectancy:.2f}
                </div>
            </div>
            <div class="metric">
                <div class="label">Max Drawdown</div>
                <div class="value negative">
                    ${metrics.max_drawdown:.2f} ({metrics.max_drawdown_pct:.1f}%)
                </div>
            </div>
        </div>
    </div>
"""

        # Buy-and-hold comparison
        if buy_hold_btc_start:
            strategy_return = ((metrics.current_capital / self.initial_capital) - 1) * 100
            html += f"""
    <div class="section">
        <h2>🆚 Strategy vs Buy & Hold BTC</h2>
        <div class="metrics-grid">
            <div class="metric">
                <div class="label">Strategy Return</div>
                <div class="value {'positive' if strategy_return > 0 else 'negative'}">{strategy_return:.2f}%</div>
            </div>
            <div class="metric">
                <div class="label">Buy & Hold BTC</div>
                <div class="value {'positive' if metrics.buy_hold_return_pct > 0 else 'negative'}">
                    {metrics.buy_hold_return_pct:.2f}%
                </div>
            </div>
        </div>
    </div>
"""

        # Top mistakes
        if top_mistakes:
            html += """
    <div class="section">
        <h2>⚠️ Top Mistake Patterns</h2>
        <table>
            <thead>
                <tr><th>Pattern</th><th>Count</th><th>Description</th><th>Suggestion</th></tr>
            </thead>
            <tbody>
"""
            for m in top_mistakes:
                html += f"""
                <tr>
                    <td><strong>{m['pattern_type']}</strong></td>
                    <td>{m['count']}</td>
                    <td>{(m['descriptions'] or '').split(',')[0][:80]}</td>
                    <td>{(m['adjustments'] or '').split(',')[0][:80]}</td>
                </tr>
"""
            html += """
            </tbody>
        </table>
    </div>
"""

        # Calibration suggestions
        if calib_suggestions:
            html += """
    <div class="section">
        <h2>🔧 Calibration Suggestions</h2>
"""
            for s in calib_suggestions:
                html += f"""
        <div class="calibration">
            <strong>{s['pattern']}</strong> ({s['count']} occurrences)<br>
            <span style="color:#8b949e;">{s['suggestion']}</span>
        </div>
"""
            html += f"""
        <div class="calibration">
            <strong>Current entry_ready_minimum:</strong> {self.entry_ready_minimum:.1f}<br>
            <strong>Current calibration_adjustment:</strong> {self.calibration_adjustment:.1f}<br>
            <strong>Adjusted threshold:</strong> {self.entry_ready_minimum + self.calibration_adjustment:.1f}
        </div>
    </div>
"""

        # Weekly mistake summary
        if weekly_mistakes:
            html += """
    <div class="section">
        <h2>📅 Weekly Mistake Journal (Last 7 Days)</h2>
        <table>
            <thead>
                <tr><th>Time</th><th>Symbol</th><th>Pattern</th><th>Outcome</th><th>PnL</th></tr>
            </thead>
            <tbody>
"""
            for w in weekly_mistakes[:15]:
                outcome_class = "win" if w.get("outcome", "").startswith("win_") else "loss"
                html += f"""
                <tr>
                    <td>{w.get('created_at', '')[:16]}</td>
                    <td>{w.get('symbol', '')}</td>
                    <td>{w.get('pattern_type', '')}</td>
                    <td><span class="badge {outcome_class}">{w.get('outcome', '')}</span></td>
                    <td style="color: {'#3fb950' if float(w.get('pnl_usd', 0)) >= 0 else '#f85149'};">
                        ${float(w.get('pnl_usd', 0)):.2f}
                    </td>
                </tr>
"""
            html += """
            </tbody>
        </table>
    </div>
"""

        html += f"""
    <div class="footer">
        WaterfallHunter Backtester | {metrics.total_trades} trades simulated |
        DB: {self.db_path}
    </div>
</body>
</html>"""

        return html

    # ──────────────────────────────────────────────
    # Utility
    # ──────────────────────────────────────────────

    def reset(self) -> None:
        """Reset the backtester to initial state (clears all data)."""
        with self._connect() as conn:
            # Delete children first to satisfy FK constraints
            conn.execute("DELETE FROM backtest_mistakes")
            conn.execute("DELETE FROM backtest_trades")
            conn.execute("DELETE FROM backtest_equity")
            conn.execute("DELETE FROM backtest_calibration")
            conn.commit()
        self.current_capital = self.initial_capital
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.max_drawdown = 0.0
        self.peak_capital = self.initial_capital
        self.equity_curve = []
        self._mistake_patterns = {}
        self.calibration_adjustment = DEFAULT_CALIBRATION_ADJUSTMENT
        self.entry_ready_minimum = DEFAULT_ENTRY_READY_MINIMUM
        self._record_equity()
        logger.info("Backtester reset to initial state.")

    def get_equity_curve(self) -> list[dict]:
        """Return the equity curve as a list of dicts."""
        return self.equity_curve

    def get_trade_history(self, limit: int = 100) -> list[dict]:
        """Get recent trade history."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM backtest_trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


# ──────────────────────────────────────────────
# Module-level convenience
# ──────────────────────────────────────────────

def create_backtester(db_path: str = "/app/data/backtest.db") -> Backtester:
    """Factory function to create a Backtester instance."""
    return Backtester(db_path=db_path)
