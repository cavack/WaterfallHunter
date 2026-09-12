"""
backtester_v2.py — WaterfallHunter Professional Backtester
==========================================================

Capital management rules:
  - $100 initial capital
  - Max 30% capital exposure ($30 across all open positions)
  - Max 3 simultaneous positions
  - Dynamic leverage 4x-18x based on signal readiness
  - Target: 2-6 signals/day, 70% win rate, 70% profit

Leverage calculation:
  - Readiness 50-60 → 4x-6x (low confidence)
  - Readiness 60-70 → 6x-10x (medium confidence)
  - Readiness 70-80 → 10x-14x (high confidence)
  - Readiness 80-100 → 14x-18x (very high confidence)

Position sizing:
  - Max 10% capital per position (30% / 3 positions)
  - Risk amount = (capital * 10%) / leverage
  - Position size = risk_amount * leverage
"""

from __future__ import annotations

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

logger = logging.getLogger("waterfallhunter.backtester_v2")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(name)s][%(levelname)s] %(message)s"))
    logger.addHandler(_h)

# ─── Constants ───

INITIAL_CAPITAL: float = 100.0
MAX_EXPOSURE_PCT: float = 30.0        # 30% of capital max across all positions
MAX_SIMULTANEOUS_POSITIONS: int = 3
LEVERAGE_MIN: int = 4
LEVERAGE_MAX: int = 14
RISK_PER_TRADE_PCT: float = 10.0     # 10% per position (30% / 3)
TP1_CLOSE_PCT = 0.50                 # close 50% at TP1
TP2_CLOSE_PCT = 0.25                 # close 25% at TP2
TP3_CLOSE_PCT = 0.25                 # close remaining 25% at TP3
MAX_HOLD_CANDLES = 96                 # max 96 candles (4h on 5min timeframe)


class TradeOutcome(str, Enum):
    WIN_TP1 = "win_tp1"
    WIN_TP2 = "win_tp2"
    WIN_TP3 = "win_tp3"
    LOSS_SL = "loss_sl"
    LOSS_TIMEOUT = "loss_timeout"
    BREAKEVEN = "breakeven"


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
    leverage: int
    position_size_usd: float
    shares_traded: float
    signal_data: dict


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
    total_return_pct: float = 0.0
    avg_leverage: float = 0.0
    max_concurrent_positions: int = 0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS bt_v2_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    entry_time      TEXT NOT NULL,
    exit_time       TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    exit_price      REAL NOT NULL,
    pnl_pct         REAL NOT NULL,
    pnl_usd         REAL NOT NULL,
    outcome         TEXT NOT NULL,
    leverage        INTEGER NOT NULL DEFAULT 1,
    position_size_usd REAL NOT NULL DEFAULT 0,
    shares_traded   REAL NOT NULL DEFAULT 0,
    signal_data     TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS bt_v2_equity (
    timestamp       TEXT NOT NULL,
    capital         REAL NOT NULL,
    trade_count     INTEGER NOT NULL,
    open_positions  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bt_v2_calibration (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    key_name        TEXT UNIQUE NOT NULL,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bt_v2_mistakes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER NOT NULL,
    pattern_type    TEXT NOT NULL,
    description     TEXT NOT NULL,
    adjustment      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (trade_id) REFERENCES bt_v2_trades(id)
);

CREATE INDEX IF NOT EXISTS idx_v2_trades_symbol ON bt_v2_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_v2_trades_outcome ON bt_v2_trades(outcome);
"""


class BacktesterV2:
    """
    Professional backtester with:
    - $100 capital, 30% max exposure, 3 concurrent positions
    - Dynamic leverage 4x-18x based on readiness
    - Self-learning mistake journal
    - Historical backtest capability
    """

    def __init__(
        self,
        db_path: str = "/app/data/backtest_v2.db",
        initial_capital: float = INITIAL_CAPITAL,
    ) -> None:
        self.db_path = db_path
        self.initial_capital = float(initial_capital)
        self.current_capital: float = self.initial_capital
        self.total_trades: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.max_drawdown: float = 0.0
        self.peak_capital: float = self.initial_capital
        self.equity_curve: list[dict] = []
        self.open_positions: list[dict] = []  # track concurrent positions
        self._mistake_patterns: dict[str, int] = {}
        self._leverage_history: list[int] = []
        self._ensure_db()

    # ─── Database ───

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()
        self._load_state()

    def _load_state(self) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT timestamp, capital, trade_count, open_positions FROM bt_v2_equity ORDER BY timestamp ASC"
            ).fetchall()
            if rows:
                self.equity_curve = [dict(r) for r in rows]
                latest = rows[-1]
                self.current_capital = float(latest["capital"])
                self.total_trades = int(latest["trade_count"])
            else:
                self.current_capital = self.initial_capital
                self._record_equity()

            self.wins = conn.execute(
                "SELECT COUNT(*) FROM bt_v2_trades WHERE outcome LIKE 'win_%'"
            ).fetchone()[0]
            self.losses = conn.execute(
                "SELECT COUNT(*) FROM bt_v2_trades WHERE outcome LIKE 'loss_%'"
            ).fetchone()[0]

            # Max drawdown
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

            # Leverage history
            lev_rows = conn.execute("SELECT leverage FROM bt_v2_trades ORDER BY id ASC").fetchall()
            self._leverage_history = [int(r["leverage"]) for r in lev_rows]

            # Mistake patterns
            mistake_rows = conn.execute(
                "SELECT pattern_type, COUNT(*) as cnt FROM bt_v2_mistakes GROUP BY pattern_type"
            ).fetchall()
            self._mistake_patterns = {r["pattern_type"]: int(r["cnt"]) for r in mistake_rows}

    def _record_equity(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO bt_v2_equity (timestamp, capital, trade_count, open_positions) VALUES (?, ?, ?, ?)",
                (now, self.current_capital, self.total_trades, len(self.open_positions)),
            )
            conn.commit()
        self.equity_curve.append({
            "timestamp": now,
            "capital": self.current_capital,
            "trade_count": self.total_trades,
            "open_positions": len(self.open_positions),
        })

    # ─── Leverage Calculation ───

    def calculate_leverage(self, readiness: float, cascade_pass: bool, cross_exchange: bool) -> int:
        """
        Dynamic leverage 4x-18x based on signal confidence.

        Factors:
        - Readiness score (50-100)
        - Cascade status
        - Cross-exchange confirmation
        """
        # Base leverage from readiness
        if readiness >= 80:
            base_lev = 12
        elif readiness >= 75:
            base_lev = 10
        elif readiness >= 70:
            base_lev = 7
        elif readiness >= 60:
            base_lev = 5
        else:
            base_lev = 4

        # Bonus for strong confirmation
        if cascade_pass:
            base_lev += 2
        if cross_exchange:
            base_lev += 2

        # Cap at 18x
        return min(LEVERAGE_MAX, max(LEVERAGE_MIN, base_lev))

    # ─── Position Sizing ───

    def calculate_position_size(
        self, entry_price: float, stop_loss: float, leverage: int, readiness: float
    ) -> tuple[float, float, float]:
        """
        Calculate position size with leverage and exposure limits.

        Returns: (position_size_usd, shares, risk_amount_usd)
        """
        # Max 10% capital per position (30% / 3)
        max_per_position = self.current_capital * (RISK_PER_TRADE_PCT / 100.0)

        # Risk amount = position_notional / leverage
        # But we also cap by stop-loss distance
        sl_distance_pct = abs(entry_price - stop_loss) / entry_price if entry_price > 0 else 1.0

        # Position notional = max_per_position * leverage
        position_notional = max_per_position * leverage

        # Risk amount if SL hit = position_notional * sl_distance_pct
        risk_amount = position_notional * sl_distance_pct

        # If risk amount exceeds 10% of capital, reduce position
        max_risk = self.current_capital * 0.05  # max 5% actual loss per trade
        if risk_amount > max_risk:
            position_notional = max_risk / sl_distance_pct
            risk_amount = max_risk

        shares = position_notional / entry_price if entry_price > 0 else 0.0

        return (position_notional, shares, risk_amount)

    def can_open_position(self) -> bool:
        """Check if we can open a new position (max 3, max 30% exposure)."""
        if len(self.open_positions) >= MAX_SIMULTANEOUS_POSITIONS:
            return False

        # Calculate current exposure
        current_exposure = sum(p.get("position_size_usd", 0) for p in self.open_positions)
        max_exposure = self.current_capital * (MAX_EXPOSURE_PCT / 100.0)

        if current_exposure >= max_exposure:
            return False

        return True

    # ─── Trade Simulation ───

    def _simulate_trade(
        self,
        entry_price: float,
        stop_loss: float,
        tp1: float,
        tp2: float,
        tp3: float,
        shares: float,
        leverage: int,
        candle_history: list[dict],
    ) -> tuple[float, float, str, str, float]:
        """
        Walk through candle history to determine trade outcome.
        Returns: (exit_price, pnl_usd, outcome, exit_time, pnl_pct)
        """
        is_long = entry_price > stop_loss

        remaining_shares = shares
        realized_pnl = 0.0
        moved_sl_to_entry = False
        exit_time = ""
        candle_count = 0

        tp1_hit = False
        tp2_hit = False
        tp3_hit = False
        sl_hit = False
        final_close = entry_price

        for candle in candle_history:
            candle_count += 1
            if candle_count > MAX_HOLD_CANDLES:
                break

            high = float(candle.get("high", candle.get("h", 0)))
            low = float(candle.get("low", candle.get("l", 0)))
            close = float(candle.get("close", candle.get("c", 0)))
            ts = candle.get("timestamp", candle.get("time", candle.get("date", "")))
            if ts:
                exit_time = str(ts)
            final_close = close

            current_sl = entry_price if moved_sl_to_entry else stop_loss

            if is_long:
                if low <= current_sl:
                    sl_hit = True
                    break
                if not tp1_hit and high >= tp1:
                    tp1_hit = True
                    realized_pnl += (tp1 - entry_price) * remaining_shares * TP1_CLOSE_PCT
                    remaining_shares *= (1 - TP1_CLOSE_PCT)
                    moved_sl_to_entry = True
                    continue
                if tp1_hit and not tp2_hit and high >= tp2:
                    tp2_hit = True
                    realized_pnl += (tp2 - entry_price) * remaining_shares * TP2_CLOSE_PCT
                    remaining_shares *= (1 - TP2_CLOSE_PCT)
                    continue
                if tp2_hit and not tp3_hit and high >= tp3:
                    tp3_hit = True
                    realized_pnl += (tp3 - entry_price) * remaining_shares
                    remaining_shares = 0.0
                    break
            else:
                if high >= current_sl:
                    sl_hit = True
                    break
                if not tp1_hit and low <= tp1:
                    tp1_hit = True
                    realized_pnl += (entry_price - tp1) * remaining_shares * TP1_CLOSE_PCT
                    remaining_shares *= (1 - TP1_CLOSE_PCT)
                    moved_sl_to_entry = True
                    continue
                if tp1_hit and not tp2_hit and low <= tp2:
                    tp2_hit = True
                    realized_pnl += (entry_price - tp2) * remaining_shares * TP2_CLOSE_PCT
                    remaining_shares *= (1 - TP2_CLOSE_PCT)
                    continue
                if tp2_hit and not tp3_hit and low <= tp3:
                    tp3_hit = True
                    realized_pnl += (entry_price - tp3) * remaining_shares
                    remaining_shares = 0.0
                    break

        # Determine outcome
        if sl_hit:
            outcome = TradeOutcome.LOSS_SL
            exit_price = current_sl if 'current_sl' in dir() else stop_loss
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
            if remaining_shares > 0:
                if is_long:
                    realized_pnl += (final_close - entry_price) * remaining_shares
                else:
                    realized_pnl += (entry_price - final_close) * remaining_shares
        elif tp1_hit:
            outcome = TradeOutcome.WIN_TP1
            exit_price = tp1
            if remaining_shares > 0:
                if is_long:
                    realized_pnl += (final_close - entry_price) * remaining_shares
                else:
                    realized_pnl += (entry_price - final_close) * remaining_shares
        else:
            outcome = TradeOutcome.LOSS_TIMEOUT
            exit_price = final_close
            if remaining_shares > 0:
                if is_long:
                    realized_pnl += (final_close - entry_price) * remaining_shares
                else:
                    realized_pnl += (entry_price - final_close) * remaining_shares

        # PnL percentage relative to margin (not notional)
        margin = (shares * entry_price) / leverage if leverage > 0 else (shares * entry_price)
        pnl_pct = (realized_pnl / margin) * 100 if margin > 0 else 0.0

        return (exit_price, realized_pnl, outcome.value, exit_time, pnl_pct)

    # ─── Run Signal Trade ───

    async def run_signal_trade(
        self,
        symbol: str,
        signal_data: dict,
        candle_history: list[dict],
    ) -> Optional[TradeResult]:
        """Simulate a trade from a WaterfallHunter signal."""
        try:
            # Check if we can open position
            if not self.can_open_position():
                logger.info(f"Max positions/exposure reached — skipping {symbol}")
                return None

            readiness = float(signal_data.get("readiness_score", 0))
            if readiness < 50:
                logger.info(f"Readiness {readiness} < 50 — skipping {symbol}")
                return None

            # Extract trade plan
            tp = signal_data.get("trade_plan", {})
            entry_price = float(tp.get("entry_price", 0))
            stop_loss = float(tp.get("stop_loss", 0))
            tp1 = float(tp.get("take_profit_1", 0))
            tp2 = float(tp.get("take_profit_2", tp1))
            tp3 = float(tp.get("take_profit_3", tp2))

            if entry_price <= 0 or stop_loss <= 0:
                logger.warning(f"Invalid prices for {symbol}")
                return None

            # Get signal quality factors
            cascade_pass = signal_data.get("cascade_status", "") == "PASS"
            cross_exchange = signal_data.get("cross_exchange", False)

            # Calculate dynamic leverage
            leverage = self.calculate_leverage(readiness, cascade_pass, cross_exchange)

            # Calculate position size
            position_notional, shares, risk_amount = self.calculate_position_size(
                entry_price, stop_loss, leverage, readiness
            )

            if shares <= 0:
                logger.warning(f"Position size is zero — skipping {symbol}")
                return None

            # Simulate trade
            entry_time = datetime.now(timezone.utc).isoformat()
            exit_price, pnl_usd, outcome_str, exit_time, pnl_pct = self._simulate_trade(
                entry_price, stop_loss, tp1, tp2, tp3,
                shares, leverage, candle_history
            )

            # Track open position (for concurrent position tracking)
            self.open_positions.append({
                "symbol": symbol,
                "position_size_usd": position_notional,
                "entry_time": entry_time,
            })

            outcome = TradeOutcome(outcome_str)
            is_win = outcome.value.startswith("win_")

            # Record trade
            trade_id = self._record_trade(
                symbol, entry_time, exit_time, entry_price, exit_price,
                pnl_pct, pnl_usd, outcome_str, leverage,
                position_notional, shares, signal_data
            )

            # Update capital
            self.current_capital += pnl_usd
            self.total_trades += 1
            self._leverage_history.append(leverage)
            if is_win:
                self.wins += 1
            else:
                self.losses += 1
                self._record_mistakes(trade_id, signal_data, outcome, readiness, cascade_pass, cross_exchange)

            # Update drawdown
            if self.current_capital > self.peak_capital:
                self.peak_capital = self.current_capital
            dd = self.peak_capital - self.current_capital
            if dd > self.max_drawdown:
                self.max_drawdown = dd

            # Remove from open positions
            self.open_positions = [p for p in self.open_positions if p["symbol"] != symbol or p["entry_time"] != entry_time]

            self._record_equity()

            logger.info(
                f"Trade #{trade_id} {symbol}: {outcome_str} | "
                f"PnL: ${pnl_usd:.2f} ({pnl_pct:.1f}%) | "
                f"Lev: {leverage}x | "
                f"Capital: ${self.current_capital:.2f}"
            )

            return TradeResult(
                trade_id=trade_id, symbol=symbol, entry_time=entry_time,
                exit_time=exit_time, entry_price=entry_price, exit_price=exit_price,
                pnl_pct=pnl_pct, pnl_usd=pnl_usd, outcome=outcome_str,
                leverage=leverage, position_size_usd=position_notional,
                shares_traded=shares, signal_data=signal_data
            )

        except Exception as e:
            logger.error(f"Error running trade for {symbol}: {e}", exc_info=True)
            return None

    def _record_trade(self, symbol, entry_time, exit_time, entry_price, exit_price,
                      pnl_pct, pnl_usd, outcome, leverage, position_size_usd,
                      shares_traded, signal_data) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO bt_v2_trades
                   (symbol, entry_time, exit_time, entry_price, exit_price,
                    pnl_pct, pnl_usd, outcome, leverage, position_size_usd,
                    shares_traded, signal_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (symbol, entry_time, exit_time, entry_price, exit_price,
                 pnl_pct, pnl_usd, outcome, leverage, position_size_usd,
                 shares_traded, json.dumps(signal_data, default=str))
            )
            conn.commit()
            return int(cur.lastrowid)

    # ─── Self-Learning ───

    def _record_mistakes(self, trade_id, signal_data, outcome, readiness, cascade_pass, cross_exchange):
        now = datetime.now(timezone.utc).isoformat()
        mistakes = []

        if readiness < 65:
            mistakes.append(("low_readiness", f"Readiness {readiness:.1f} < 65", "Raise threshold"))
        if not cascade_pass:
            mistakes.append(("cascade_fail", "Cascade failed", "Require cascade PASS"))
        if not cross_exchange:
            mistakes.append(("no_cross", "No cross-exchange confirmation", "Require cross-exchange"))
        if outcome == TradeOutcome.LOSS_TIMEOUT:
            mistakes.append(("timeout", "Trade timed out", "Tighten TPs or reduce hold time"))
        if outcome == TradeOutcome.LOSS_SL:
            mistakes.append(("sl_hit", "Stop loss hit", "Review SL placement"))

        with self._connect() as conn:
            for pattern, desc, adj in mistakes:
                conn.execute(
                    """INSERT INTO bt_v2_mistakes (trade_id, pattern_type, description, adjustment, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (trade_id, pattern, desc, adj, now)
                )
                self._mistake_patterns[pattern] = self._mistake_patterns.get(pattern, 0) + 1
            conn.commit()

    # ─── Metrics ───

    def compute_metrics(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pnl_usd, pnl_pct, outcome, leverage FROM bt_v2_trades ORDER BY id ASC"
            ).fetchall()

        if not rows:
            return {
                "current_capital": self.current_capital,
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "total_return_pct": 0,
                "max_drawdown_pct": 0, "profit_factor": 0,
                "sharpe_ratio": 0, "avg_leverage": 0,
            }

        pnls = [float(r["pnl_usd"]) for r in rows]
        win_pnls = [p for p in pnls if p > 0]
        loss_pnls = [p for p in pnls if p < 0]
        leverages = [int(r["leverage"]) for r in rows]

        wins = len(win_pnls)
        losses = len(loss_pnls)
        total = len(pnls)
        win_rate = (wins / total) * 100 if total > 0 else 0.0
        avg_win = statistics.mean(win_pnls) if win_pnls else 0.0
        avg_loss = statistics.mean(loss_pnls) if loss_pnls else 0.0
        gross_profit = sum(win_pnls)
        gross_loss = abs(sum(loss_pnls))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        expectancy = statistics.mean(pnls) if pnls else 0.0

        if len(pnls) >= 2:
            std_dev = statistics.stdev(pnls)
            sharpe = (statistics.mean(pnls) / std_dev) * math.sqrt(total) if std_dev > 0 else 0.0
        else:
            sharpe = 0.0

        # Max drawdown
        peak = self.initial_capital
        max_dd_pct = 0.0
        for point in self.equity_curve:
            cap = float(point["capital"])
            if cap > peak:
                peak = cap
            dd_pct = ((peak - cap) / peak) * 100 if peak > 0 else 0.0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

        total_pnl = sum(pnls)
        total_return = (total_pnl / self.initial_capital) * 100
        avg_lev = statistics.mean(leverages) if leverages else 0.0

        return {
            "current_capital": round(self.current_capital, 2),
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "average_win": round(avg_win, 2),
            "average_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd_pct, 1),
            "total_return_pct": round(total_return, 1),
            "avg_leverage": round(avg_lev, 1),
            "total_pnl": round(total_pnl, 2),
        }

    def get_equity_curve(self) -> list[dict]:
        return self.equity_curve

    def get_trade_history(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bt_v2_trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_top_mistakes(self, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT pattern_type, COUNT(*) as cnt, 
                   GROUP_CONCAT(DISTINCT description) as desc
                   FROM bt_v2_mistakes GROUP BY pattern_type 
                   ORDER BY cnt DESC LIMIT ?""", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def reset(self) -> None:
        """Reset backtester to initial state."""
        self.current_capital = self.initial_capital
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.max_drawdown = 0.0
        self.peak_capital = self.initial_capital
        self.equity_curve = []
        self.open_positions = []
        self._leverage_history = []
        self._mistake_patterns = {}
        with self._connect() as conn:
            conn.execute("DELETE FROM bt_v2_trades")
            conn.execute("DELETE FROM bt_v2_equity")
            conn.execute("DELETE FROM bt_v2_mistakes")
            conn.commit()
        self._record_equity()
        logger.info("Backtester reset to initial state")

    def generate_report(self) -> str:
        """Generate HTML report."""
        m = self.compute_metrics()
        html = f"""
        <html><body style="font-family:monospace;background:#0f172a;color:#e2e8f0;padding:20px">
        <h2 style="color:#10b981">📊 Backtest Report V2</h2>
        <table style="border-collapse:collapse;width:100%">
        <tr><td style="color:#64748b">Capital</td><td>${m['current_capital']:.2f}</td></tr>
        <tr><td style="color:#64748b">Total Return</td><td style="color:{'#10b981' if m['total_return_pct']>0 else '#ef4444'}">{m['total_return_pct']:.1f}%</td></tr>
        <tr><td style="color:#64748b">Trades</td><td>{m['total_trades']}</td></tr>
        <tr><td style="color:#64748b">Win Rate</td><td style="color:#10b981">{m['win_rate']:.1f}%</td></tr>
        <tr><td style="color:#64748b">Profit Factor</td><td>{m['profit_factor']:.2f}</td></tr>
        <tr><td style="color:#64748b">Max Drawdown</td><td style="color:#ef4444">{m['max_drawdown_pct']:.1f}%</td></tr>
        <tr><td style="color:#64748b">Sharpe</td><td>{m['sharpe_ratio']:.2f}</td></tr>
        <tr><td style="color:#64748b">Avg Leverage</td><td>{m['avg_leverage']:.1f}x</td></tr>
        <tr><td style="color:#64748b">Expectancy</td><td>${m['expectancy']:.2f}/trade</td></tr>
        </table>
        </body></html>
        """
        return html
