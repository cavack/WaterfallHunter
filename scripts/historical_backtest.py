#!/usr/bin/env python3
"""Walk-forward research using only Binance USDT-perpetual public OHLCV."""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from io import BytesIO, TextIOWrapper
from pathlib import Path
from statistics import mean
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.request import urlopen
from zipfile import ZipFile

try:
    from scripts.backtest_metrics import empirical_slippage_cost_r, performance_metrics
except ModuleNotFoundError:
    from backtest_metrics import empirical_slippage_cost_r, performance_metrics

try:
    from waterfallhunter.core.channel_strategy import CHANNEL_STRATEGY_ID, channel_stages
    from waterfallhunter.core.score_v2 import ScoreV2
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))
    from waterfallhunter.core.channel_strategy import CHANNEL_STRATEGY_ID, channel_stages
    from waterfallhunter.core.score_v2 import ScoreV2

BASE_URL = "https://fapi.binance.com/fapi/v1"
ARCHIVE_URL = "https://data.binance.vision/data/futures/um/monthly/klines"
METRICS_ARCHIVE_URL = "https://data.binance.vision/data/futures/um/daily/metrics"
FUNDING_RATE_ENDPOINT = "/fundingRate"
FUNDING_RATE_DOC_URL = "https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History"
COMMISSION_RATE_DOC_URL = "https://developers.binance.com/docs/derivatives/usds-margined-futures/account/rest-api/User-Commission-Rate"
FIVE_MINUTES = 300_000
TEN_MINUTES = 600_000
ONE_HOUR = 3_600_000
EIGHT_HOURS = 8 * ONE_HOUR
HISTORICAL_SCORE_V2_AVAILABLE_MAXIMUM = 75.0
APPROVED_SCORE_V2_WEIGHTS = {
    "structural_post_pump": 35.0,
    "entry_timing": 20.0,
    "execution_microstructure": 20.0,
    "derivatives_confirmation": 15.0,
    "cross_exchange_confirmation": 5.0,
    "same_contract_price_location": 5.0,
}
TIMEFRAMES = {"5m": 1, "15m": 3, "1h": 12, "4h": 48}
WEIGHTS = {"5m": 5.0, "15m": 8.0, "1h": 10.0, "4h": 12.0}
EVIDENCE_WINDOW = 100


def expectancy_r(outcomes, reward_r: float):
    settled = [outcome for outcome in outcomes if outcome in {"win", "loss"}]
    if not settled:
        return None
    return sum(reward_r if outcome == "win" else -1.0 for outcome in settled) / len(settled)


def chronological_splits(trades):
    ordered = sorted(trades, key=lambda trade: trade["timestamp"])
    train_end = len(ordered) // 2
    validation_end = len(ordered) * 5 // 6
    return {
        "train": ordered[:train_end],
        "validation": ordered[train_end:validation_end],
        "holdout": ordered[validation_end:],
    }


def purged_time_splits(trades, start_ms: int, end_ms: int, outcome_horizon_ms: int):
    if start_ms >= end_ms or outcome_horizon_ms < 0:
        raise ValueError("invalid research window")
    train_boundary = start_ms + (end_ms - start_ms) // 2
    validation_boundary = start_ms + (end_ms - start_ms) * 5 // 6
    ordered = sorted(trades, key=lambda trade: trade["timestamp"])
    return {
        "train": [trade for trade in ordered if trade["timestamp"] < train_boundary - outcome_horizon_ms],
        "validation": [trade for trade in ordered if train_boundary <= trade["timestamp"] < validation_boundary - outcome_horizon_ms],
        "holdout": [trade for trade in ordered if trade["timestamp"] >= validation_boundary],
    }


def summarize(trades, reward_r: float):
    settled = [trade for trade in trades if trade["outcome"] in {"win", "loss"}]
    wins = sum(trade["outcome"] == "win" for trade in settled)
    realized = [float(trade["realized_r"]) for trade in trades if trade.get("realized_r") is not None]
    settled_expectancy = expectancy_r([trade["outcome"] for trade in trades], reward_r)
    gross_performance = performance_metrics(trades, return_field="realized_r")
    net_performance = performance_metrics(trades, return_field="net_realized_r")
    slippage_adjusted_performance = performance_metrics(trades, return_field="slippage_adjusted_realized_r")
    return {
        "signals": len(trades),
        "settled": len(settled),
        "wins": wins,
        "win_rate": round(wins / len(settled), 4) if settled else None,
        "timeouts": sum(trade["outcome"] == "timeout" for trade in trades),
        "settled_expectancy_r": round(settled_expectancy, 4) if settled_expectancy is not None else None,
        "realized_expectancy_r": round(mean(realized), 4) if realized else None,
        "gross_performance": gross_performance,
        "net_performance": net_performance,
        "slippage_adjusted_performance": slippage_adjusted_performance,
        "max_drawdown_pct": net_performance.get("max_drawdown_pct") if net_performance.get("available") else None,
        "profit_factor": net_performance.get("profit_factor") if net_performance.get("available") else None,
        "net_expectancy_r": net_performance.get("expectancy_r") if net_performance.get("available") else None,
    }


def promotion_eligibility(summary: dict, signals_per_day: float, *, validation_summary: dict | None = None,
                          reward_r: float | None = None, strategy_equivalent: bool = False,
                          minimum_holdout_settled: int = 30, minimum_validation_settled: int = 50) -> dict:
    """Refuse live promotion unless the research and live contracts are equivalent."""
    validation_summary = validation_summary or {}
    reasons = []
    if not strategy_equivalent:
        reasons.append("historical strategy is not feature-equivalent to the live pipeline")
    if reward_r is None or reward_r < 1.0:
        reasons.append("cost-adjusted reward is below 1R")
    if validation_summary.get("settled", 0) < minimum_validation_settled:
        reasons.append("insufficient settled validation trades")
    if validation_summary.get("realized_expectancy_r") is None or validation_summary["realized_expectancy_r"] <= 0:
        reasons.append("validation expectancy is not positive")
    if summary.get("settled", 0) < minimum_holdout_settled:
        reasons.append("insufficient settled holdout trades")
    if summary.get("win_rate") is None or summary["win_rate"] < 0.70:
        reasons.append("holdout win rate is below 70%")
    if summary.get("realized_expectancy_r") is None or summary["realized_expectancy_r"] <= 0.0:
        reasons.append("holdout expectancy is not positive")
    validation_net = validation_summary.get("net_performance")
    holdout_net = summary.get("net_performance")
    if not isinstance(validation_net, dict) or validation_net.get("available") is not True:
        reasons.append("validation performance is not net of complete real execution costs")
    elif validation_net.get("cost_basis") != "realized":
        reasons.append("validation execution costs are modeled rather than realized")
    elif validation_net.get("expectancy_r") is None or validation_net["expectancy_r"] <= 0:
        reasons.append("validation net expectancy is not positive")
    if not isinstance(holdout_net, dict) or holdout_net.get("available") is not True:
        reasons.append("holdout performance is not net of complete real execution costs")
    elif holdout_net.get("cost_basis") != "realized":
        reasons.append("holdout execution costs are modeled rather than realized")
    else:
        if holdout_net.get("expectancy_r") is None or holdout_net["expectancy_r"] <= 0:
            reasons.append("holdout net expectancy is not positive")
        if holdout_net.get("max_drawdown_pct") is None or holdout_net["max_drawdown_pct"] > 20.0:
            reasons.append("holdout max drawdown exceeds 20%")
        if holdout_net.get("profit_factor") is None or holdout_net["profit_factor"] < 1.5:
            reasons.append("holdout profit factor is below 1.5")
    if signals_per_day < 2.0:
        reasons.append("signal density is below 2/day")
    eligible = not reasons
    return {
        "eligible": eligible,
        "minimum_holdout_settled": minimum_holdout_settled,
        "minimum_validation_settled": minimum_validation_settled,
        "minimum_win_rate": 0.70,
        "minimum_realized_expectancy_r": 0.0,
        "minimum_signals_per_day": 2.0,
        "minimum_reward_r": 1.0,
        "maximum_max_drawdown_pct": 20.0,
        "minimum_profit_factor": 1.5,
        "requires_complete_net_execution_costs": True,
        "strategy_equivalent": strategy_equivalent,
        "reasons": reasons,
    }


def request(path: str, **params):
    url = f"{BASE_URL}{path}?{urlencode(params)}"
    for attempt in range(6):
        try:
            with urlopen(url, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in {418, 429} or attempt == 5:
                raise
            retry_after = exc.headers.get("Retry-After")
            time.sleep(float(retry_after) if retry_after else 2 ** attempt)
        except URLError:
            if attempt == 5:
                raise
            time.sleep(2 ** attempt)


def source_url(path: str, **params) -> str:
    return f"{BASE_URL}{path}?{urlencode(params)}"


def universe(count: int, minimum_volume: float):
    instruments = {item["symbol"] for item in request("/exchangeInfo")["symbols"]
                   if item["contractType"] == "PERPETUAL" and item["quoteAsset"] == "USDT" and item["status"] == "TRADING"}
    tickers = request("/ticker/24hr")
    eligible = [item for item in tickers if item["symbol"] in instruments and float(item["lastPrice"]) > 0
                and float(item["lastPrice"]) <= 1 and float(item["quoteVolume"]) >= minimum_volume]
    eligible.sort(key=lambda item: float(item["quoteVolume"]), reverse=True)
    selected = [item["symbol"] for item in eligible]
    for forced in ("BTCUSDT", "ETHUSDT"):
        if forced in instruments:
            selected = [symbol for symbol in selected if symbol != forced]
    return (["BTCUSDT", "ETHUSDT"] + selected)[:count]


def dashboard_watchlist(url: str):
    with urlopen(url, timeout=30) as response:
        payload = json.load(response)
    candidates = payload.get("candidates")
    if not isinstance(candidates, dict):
        raise ValueError("dashboard response does not contain a candidate map")
    instruments = {
        item["symbol"] for item in request("/exchangeInfo")["symbols"]
        if item["contractType"] == "PERPETUAL" and item["quoteAsset"] == "USDT" and item["status"] == "TRADING"
    }
    selected, unavailable = [], []
    for lbank_symbol in sorted(candidates):
        symbol = f"{lbank_symbol.upper().split('/')[0]}USDT"
        if symbol in instruments:
            selected.append(symbol)
        else:
            unavailable.append(lbank_symbol)
    if not selected:
        raise ValueError("no dashboard candidates map to Binance USDT perpetual history")
    return selected, unavailable


def _valid_candles(rows, start_ms: int, end_ms: int, *, require_quote_volume: bool = False):
    rows = [row for row in rows if start_ms <= row[0] and row[0] + FIVE_MINUTES <= end_ms]
    if len(rows) < 100 or any(later[0] - earlier[0] != FIVE_MINUTES for earlier, later in zip(rows, rows[1:])):
        return None
    try:
        invalid = any(
            len(row) < (7 if require_quote_volume else 6)
            or row[0] <= 0 or min(row[1], row[2], row[3], row[4]) <= 0 or row[5] < 0
            or (require_quote_volume and row[6] < 0)
            or row[2] < max(row[1], row[4]) or row[3] > min(row[1], row[4])
            for row in rows
        )
    except (TypeError, ValueError):
        return None
    if invalid:
        return None
    return rows


def _rest_candles(symbol: str, start_ms: int, end_ms: int):
    rows, cursor = [], start_ms
    while cursor < end_ms:
        chunk = request("/klines", symbol=symbol, interval="5m", startTime=cursor, endTime=end_ms, limit=1500)
        if not chunk:
            break
        rows.extend([[int(row[0]), *map(float, row[1:6]), float(row[7])] for row in chunk])
        cursor = int(chunk[-1][0]) + FIVE_MINUTES
        time.sleep(0.25)
    return _valid_candles(rows, start_ms, end_ms, require_quote_volume=True)


def _month_keys(start_ms: int, end_ms: int):
    current = datetime.fromtimestamp(start_ms / 1000, UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while int(current.timestamp() * 1000) < end_ms:
        yield current.strftime("%Y-%m")
        current = current.replace(year=current.year + 1, month=1) if current.month == 12 else current.replace(month=current.month + 1)


def _archive_candles(symbol: str, start_ms: int, end_ms: int):
    rows = []
    for month in _month_keys(start_ms, end_ms):
        month_start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
        next_month = month_start.replace(year=month_start.year + 1, month=1) if month_start.month == 12 else month_start.replace(month=month_start.month + 1)
        segment_start = max(start_ms, int(month_start.timestamp() * 1000))
        segment_end = min(end_ms, int(next_month.timestamp() * 1000))
        url = f"{ARCHIVE_URL}/{symbol}/5m/{symbol}-5m-{month}.zip"
        try:
            with urlopen(url, timeout=30) as response:
                payload = response.read()
        except HTTPError as exc:
            if exc.code == 404:
                fallback = _rest_candles(symbol, segment_start, segment_end)
                if fallback is None:
                    return None
                rows.extend(fallback)
                continue
            raise
        with ZipFile(BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.endswith(".csv")]
            if len(names) != 1:
                return None
            with archive.open(names[0]) as raw:
                for record in csv.reader(TextIOWrapper(raw, encoding="utf-8")):
                    try:
                        rows.append([int(record[0]), *map(float, record[1:6]), float(record[7])])
                    except (IndexError, TypeError, ValueError):
                        continue
    return _valid_candles(rows, start_ms, end_ms, require_quote_volume=True)


def candles(symbol: str, start_ms: int, end_ms: int, cache_dir: Path | None = None):
    cache_path = cache_dir / f"{symbol}_{start_ms}_{end_ms}.json" if cache_dir else None
    if cache_path and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            validated = _valid_candles(cached, start_ms, end_ms, require_quote_volume=True)
            if validated is not None:
                return validated
        except (OSError, ValueError, TypeError):
            pass
    try:
        rows = _archive_candles(symbol, start_ms, end_ms)
    except (OSError, URLError, ValueError):
        rows = None
    if rows is None:
        rows = _rest_candles(symbol, start_ms, end_ms)
    if rows is None:
        return None
    if cache_path:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(rows, separators=(",", ":")))
    return rows


def _metric_day(symbol: str, day: datetime, cache_dir: Path | None = None):
    stamp = day.strftime("%Y-%m-%d")
    cache_path = cache_dir / f"{symbol}_{stamp}.json" if cache_dir else None
    url = f"{METRICS_ARCHIVE_URL}/{symbol}/{symbol}-metrics-{stamp}.zip"
    if cache_path and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if isinstance(cached, list):
                for row in cached:
                    if isinstance(row, dict):
                        row.setdefault("source_url", url)
                return cached
        except (OSError, ValueError, TypeError):
            return None
    try:
        with urlopen(url, timeout=30) as response:
            payload = response.read()
        with ZipFile(BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.endswith(".csv")]
            if len(names) != 1:
                return None
            with archive.open(names[0]) as raw:
                rows = list(csv.DictReader(TextIOWrapper(raw, encoding="utf-8")))
    except (HTTPError, OSError, URLError, ValueError):
        return None
    parsed = []
    for row in rows:
        try:
            parsed.append({
                "timestamp": int(datetime.strptime(row["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp() * 1000),
                "open_interest_usdt": float(row["sum_open_interest_value"]),
                "top_trader_long_short_ratio": float(row["count_toptrader_long_short_ratio"]),
                "taker_long_short_volume_ratio": float(row["sum_taker_long_short_vol_ratio"]),
                "source_url": url,
            })
        except (KeyError, TypeError, ValueError):
            return None
    if not parsed:
        return None
    if cache_path:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(parsed, separators=(",", ":")))
        except OSError:
            pass
    return parsed


class HistoricalFunding:
    """Funding observations sourced from Binance USD-M at or before entry only."""

    def __init__(self, cache_dir: Path, start_ms: int | None, end_ms: int | None):
        self.cache_dir = cache_dir
        self.start_ms = start_ms
        self.end_ms = end_ms
        self._symbols = {}

    def _rows(self, symbol: str):
        if symbol in self._symbols:
            return self._symbols[symbol]
        if self.start_ms is None or self.end_ms is None or self.start_ms >= self.end_ms:
            self._symbols[symbol] = None
            return None
        start_ms = self.start_ms - 90 * EIGHT_HOURS
        params = {"symbol": symbol, "startTime": start_ms, "endTime": self.end_ms, "limit": 1000}
        url = source_url(FUNDING_RATE_ENDPOINT, **params)
        cache_path = self.cache_dir / f"{symbol}_{start_ms}_{self.end_ms}.json"
        rows = []
        coverage_complete = False
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
            except (OSError, ValueError, TypeError):
                cached = None
            if isinstance(cached, dict):
                cached_rows = cached.get("rows")
                if isinstance(cached_rows, list):
                    rows = cached_rows
                    coverage_complete = (
                        cached.get("requested_start_ms") == start_ms
                        and isinstance(cached.get("coverage_end_ms"), int)
                        and cached["coverage_end_ms"] >= self.end_ms
                    )
            elif isinstance(cached, list):
                rows = cached

        timestamps = []
        for row in rows:
            try:
                timestamps.append(int(row["fundingTime"]))
            except (KeyError, TypeError, ValueError):
                self._symbols[symbol] = None
                return None
        if not coverage_complete:
            cursor = max(start_ms, max(timestamps, default=start_ms - 1) + 1)
            while cursor <= self.end_ms:
                page_params = {"symbol": symbol, "startTime": cursor, "endTime": self.end_ms, "limit": 1000}
                page_url = source_url(FUNDING_RATE_ENDPOINT, **page_params)
                try:
                    page = request(FUNDING_RATE_ENDPOINT, **page_params)
                except (HTTPError, OSError, URLError, ValueError):
                    self._symbols[symbol] = None
                    return None
                if not isinstance(page, list):
                    self._symbols[symbol] = None
                    return None
                if not page:
                    coverage_complete = True
                    break
                page_rows = []
                for row in page:
                    if not isinstance(row, dict):
                        self._symbols[symbol] = None
                        return None
                    item = dict(row)
                    item["_source_url"] = page_url
                    page_rows.append(item)
                try:
                    page_last = max(int(row["fundingTime"]) for row in page_rows)
                except (KeyError, TypeError, ValueError):
                    self._symbols[symbol] = None
                    return None
                if page_last < cursor:
                    self._symbols[symbol] = None
                    return None
                rows.extend(page_rows)
                cursor = page_last + 1
                if len(page_rows) < page_params["limit"]:
                    coverage_complete = True
                    break
            if not coverage_complete:
                coverage_complete = cursor > self.end_ms
            if coverage_complete:
                try:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps({
                        "schema_version": "binance_funding_history_v2",
                        "requested_start_ms": start_ms,
                        "coverage_end_ms": self.end_ms,
                        "rows": rows,
                    }, separators=(",", ":")))
                except OSError:
                    pass
        if not coverage_complete:
            self._symbols[symbol] = None
            return None

        parsed = []
        seen = {}
        for row in rows if isinstance(rows, list) else []:
            try:
                timestamp, rate = int(row["fundingTime"]), float(row["fundingRate"])
            except (KeyError, TypeError, ValueError):
                self._symbols[symbol] = None
                return None
            if timestamp <= 0 or not math.isfinite(rate):
                self._symbols[symbol] = None
                return None
            mark_price = None
            if row.get("markPrice") is not None:
                try:
                    mark_price = float(row["markPrice"])
                except (TypeError, ValueError):
                    self._symbols[symbol] = None
                    return None
                if not math.isfinite(mark_price) or mark_price <= 0:
                    self._symbols[symbol] = None
                    return None
            normalized = {
                "timestamp": timestamp,
                "funding_rate": rate,
                "mark_price": mark_price,
                "source_url": row.get("_source_url") or url,
            }
            previous = seen.get(timestamp)
            if previous is not None and previous != normalized:
                self._symbols[symbol] = None
                return None
            seen[timestamp] = normalized
        parsed.extend(seen.values())
        parsed.sort(key=lambda item: item["timestamp"])
        self._symbols[symbol] = parsed or None
        return self._symbols[symbol]

    def at_or_before(self, symbol: str, timestamp: int):
        rows = self._rows(symbol)
        if not rows:
            return None
        positions = [row["timestamp"] for row in rows]
        index = bisect.bisect_right(positions, timestamp) - 1
        if index < 0:
            return None
        current = rows[index]
        if timestamp - current["timestamp"] > EIGHT_HOURS:
            return None
        history = [row for row in rows[:index + 1] if row["timestamp"] >= timestamp - 90 * EIGHT_HOURS]
        return {"current": current, "history": history}

    def between(self, symbol: str, start_exclusive_ms: int, end_inclusive_ms: int):
        """Return complete funding charges after entry and no later than exit."""
        if not isinstance(start_exclusive_ms, int) or not isinstance(end_inclusive_ms, int):
            raise ValueError("funding window timestamps must be integers")
        if end_inclusive_ms < start_exclusive_ms:
            raise ValueError("funding window ends before entry")
        rows = self._rows(symbol)
        if rows is None:
            return None
        positions = [row["timestamp"] for row in rows]
        start = bisect.bisect_right(positions, start_exclusive_ms)
        end = bisect.bisect_right(positions, end_inclusive_ms)
        return rows[start:end]


class HistoricalDerivatives:
    def __init__(self, cache_dir: Path, funding_cache_dir: Path | None = None,
                 start_ms: int | None = None, end_ms: int | None = None):
        self.cache_dir = cache_dir
        self._days = {}
        self._funding = HistoricalFunding(funding_cache_dir or cache_dir, start_ms, end_ms)

    def _rows(self, symbol: str, timestamp: int):
        day = datetime.fromtimestamp(timestamp / 1000, UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        key = (symbol, day.date().isoformat())
        if key not in self._days:
            self._days[key] = _metric_day(symbol, day, self.cache_dir)
        return self._days[key]

    def _rows_for_entry(self, symbol: str, timestamp: int):
        current_day = datetime.fromtimestamp(timestamp / 1000, UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        previous_day = current_day - timedelta(days=1)
        rows = []
        for day in (previous_day, current_day):
            key = (symbol, day.date().isoformat())
            if key not in self._days:
                self._days[key] = _metric_day(symbol, day, self.cache_dir)
            if self._days[key]:
                rows.extend(self._days[key])
        return sorted(rows, key=lambda row: row["timestamp"])

    def at_or_before(self, symbol: str, timestamp: int):
        rows = self._rows_for_entry(symbol, timestamp)
        if not rows:
            return None
        positions = [row["timestamp"] for row in rows]
        index = bisect.bisect_right(positions, timestamp) - 1
        if index < 0:
            return None
        row = rows[index]
        return row if timestamp - row["timestamp"] <= TEN_MINUTES else None

    def score_v2_at_or_before(self, symbol: str, timestamp: int):
        funding = self._funding.at_or_before(symbol, timestamp)
        current = self.at_or_before(symbol, timestamp)
        previous = self.at_or_before(symbol, timestamp - ONE_HOUR)
        if not funding or not current or not previous:
            return None
        context = score_v2_derivatives_context(
            funding_rate=funding["current"]["funding_rate"],
            funding_history=funding["history"],
            oi_current=current.get("open_interest_usdt"),
            oi_one_hour_ago=previous.get("open_interest_usdt"),
            taker_ratio=current.get("taker_long_short_volume_ratio"),
            top_ratio=current.get("top_trader_long_short_ratio"),
            entry_timestamp=timestamp,
            timestamps={
                "funding": funding["current"]["timestamp"],
                "oi_current": current["timestamp"],
                "oi_one_hour_ago": previous["timestamp"],
                "taker": current["timestamp"],
                "top_trader": current["timestamp"],
            },
            source_urls={
                "funding": funding["current"]["source_url"],
                "metrics": current.get("source_url"),
            },
        )
        if context is not None:
            context["taker_ratio_change_1h"] = round(
                float(current["taker_long_short_volume_ratio"])
                - float(previous["taker_long_short_volume_ratio"]),
                6,
            )
        return context

    def funding_between(self, symbol: str, start_exclusive_ms: int, end_inclusive_ms: int):
        return self._funding.between(symbol, start_exclusive_ms, end_inclusive_ms)


def derivatives_context(current: dict | None, previous: dict | None):
    if not current or not previous:
        return None
    current_oi, previous_oi = current.get("open_interest_usdt"), previous.get("open_interest_usdt")
    if not isinstance(current_oi, (int, float)) or not isinstance(previous_oi, (int, float)) or previous_oi <= 0:
        return None
    taker_ratio = current.get("taker_long_short_volume_ratio")
    top_trader_ratio = current.get("top_trader_long_short_ratio")
    if not isinstance(taker_ratio, (int, float)) or not isinstance(top_trader_ratio, (int, float)):
        return None
    return {
        "oi_change_1h_pct": round((current_oi / previous_oi - 1.0) * 100.0, 4),
        "taker_long_short_volume_ratio": round(taker_ratio, 6),
        "top_trader_long_short_ratio": round(top_trader_ratio, 6),
    }


def score_v2_derivatives_context(*, funding_rate, funding_history, oi_current, oi_one_hour_ago,
                                 taker_ratio, top_ratio, entry_timestamp: int | None = None,
                                 timestamps: dict | None = None, source_urls: dict | None = None):
    """Return complete real Score V2 derivatives only; missing data is a rejection."""
    if not _valid_source_urls(source_urls):
        return None
    numeric = (funding_rate, oi_current, oi_one_hour_ago, taker_ratio, top_ratio)
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in numeric):
        return None
    if oi_current <= 0 or oi_one_hour_ago <= 0 or taker_ratio <= 0 or top_ratio <= 0:
        return None
    history = []
    history_timestamps = []
    for item in funding_history if isinstance(funding_history, list) else []:
        value = item.get("funding_rate") if isinstance(item, dict) else item
        timestamp = item.get("timestamp") if isinstance(item, dict) else None
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            return None
        history.append(float(value))
        if timestamp is not None:
            if not isinstance(timestamp, int) or timestamp <= 0:
                return None
            history_timestamps.append(timestamp)
    if len(history) < 2:
        return None
    if entry_timestamp is not None:
        required = {"funding", "oi_current", "oi_one_hour_ago", "taker", "top_trader"}
        if not isinstance(entry_timestamp, int) or not isinstance(timestamps, dict) or set(timestamps) != required:
            return None
        try:
            observed = {name: int(timestamps[name]) for name in required}
        except (TypeError, ValueError):
            return None
        if any(value > entry_timestamp or value <= 0 for value in observed.values()):
            return None
        if entry_timestamp - observed["funding"] > EIGHT_HOURS:
            return None
        if any(entry_timestamp - observed[name] > TEN_MINUTES for name in ("oi_current", "taker", "top_trader")):
            return None
        if abs((entry_timestamp - ONE_HOUR) - observed["oi_one_hour_ago"]) > TEN_MINUTES:
            return None
        if history_timestamps and (any(value > entry_timestamp for value in history_timestamps) or history_timestamps != sorted(history_timestamps)):
            return None
    percentile = sum(value <= funding_rate for value in history) / len(history)
    return {
        "funding_rate": round(float(funding_rate), 10),
        "funding_percentile": round(percentile, 6),
        "oi_change_1h_pct": round((oi_current / oi_one_hour_ago - 1.0) * 100.0, 4),
        "taker_buy_sell_ratio": round(float(taker_ratio), 6),
        "top_trader_long_short_ratio": round(float(top_ratio), 6),
        "timestamps": dict(timestamps or {}),
        "source_urls": dict(source_urls or {}),
    }


def _valid_source_urls(source_urls: object) -> bool:
    if not isinstance(source_urls, dict) or set(source_urls) != {"funding", "metrics"}:
        return False
    for value in source_urls.values():
        if not isinstance(value, str) or not value:
            return False
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            return False
    return True


def _historical_component(points: float | None, maximum: float, unavailable_reason: str | None = None) -> dict:
    if unavailable_reason is not None:
        return {"available": False, "points": None, "maximum": maximum, "reason": unavailable_reason}
    return {"available": True, "points": round(float(points or 0.0), 2), "maximum": maximum}


def historical_score_v2_components(checks: dict, derivatives: dict, *, below_vwap: bool | None) -> dict:
    """Score only Score V2 fields whose historic source exists at the entry timestamp."""
    try:
        h4 = checks["4h"]
        flags = {timeframe: checks[timeframe]["flags"] for timeframe in ("1h", "15m", "5m")}
        structural = (
            (8.0 if h4["hype_context"] else 0.0)
            + (7.0 if h4["support_broken"] else 0.0)
            + (5.0 if h4["flags"]["lower_high"] else 0.0)
            + (10.0 if h4["failed_pullback"] else 0.0)
            + (3.0 if h4["flags"]["bearish_close"] else 0.0)
            + (2.0 if h4["flags"]["volume_acceleration"] else 0.0)
        )
        timing_weights = {"1h": 8.0, "15m": 7.0, "5m": 5.0}
        timing = sum(
            weight for timeframe, weight in timing_weights.items()
            if all(flags[timeframe][field] for field in (
                "two_bearish", "lower_high", "reclaim_or_repump", "rsi_rollover", "bearish_close", "volume_acceleration"
            ))
        )
    except (KeyError, TypeError):
        return {
            "score_version": "score_v2_historical_available_v1",
            "available_score": None, "available_maximum": None,
            "components": {"structural_post_pump": _historical_component(None, 35.0, "incomplete historical candle packet")},
            "reason": "incomplete historical candle packet",
        }
    derivative_fields = ("funding_rate", "funding_percentile", "oi_change_1h_pct", "taker_buy_sell_ratio", "top_trader_long_short_ratio")
    if not all(isinstance(derivatives.get(name), (int, float)) and math.isfinite(derivatives[name]) for name in derivative_fields):
        return {
            "score_version": "score_v2_historical_available_v1",
            "available_score": None, "available_maximum": None,
            "components": {"derivatives_confirmation": _historical_component(None, 15.0, "incomplete historical derivatives packet")},
            "reason": "incomplete historical derivatives packet",
        }
    derivative_score = ScoreV2()._derivatives(
        derivatives,
        {"1h": {"bearish_close": bool(checks["1h"]["flags"]["bearish_close"])}},
    )
    price = _historical_component(5.0 if below_vwap is True else 0.0, 5.0) if isinstance(below_vwap, bool) else _historical_component(
        None, 5.0, "insufficient completed same-contract VWAP history"
    )
    components = {
        "structural_post_pump": _historical_component(structural, 35.0),
        "entry_timing": _historical_component(timing, 20.0),
        "execution_microstructure": _historical_component(None, 20.0, "historical L2/trades unavailable"),
        "derivatives_confirmation": _historical_component(derivative_score, 15.0),
        "cross_exchange_confirmation": _historical_component(None, 5.0, "single-venue historical source only"),
        "same_contract_price_location": price,
    }
    available = [item["points"] for item in components.values() if item["available"]]
    maximum = sum(item["maximum"] for item in components.values() if item["available"])
    return {
        "score_version": "score_v2_historical_available_v1",
        "available_score": round(sum(available), 2),
        "available_maximum": round(maximum, 2),
        "components": components,
        "reason": None,
    }


def historical_below_vwap(rows, entry_index: int, window_bars: int = 288) -> bool | None:
    history = rows[entry_index - window_bars:entry_index] if entry_index >= window_bars else []
    if len(history) != window_bars:
        return None
    try:
        volume = sum(row[5] for row in history)
        vwap = sum(row[4] * row[5] for row in history) / volume
        latest_close = history[-1][4]
    except (IndexError, TypeError, ValueError, ZeroDivisionError):
        return None
    return bool(math.isfinite(vwap) and math.isfinite(latest_close) and latest_close < vwap)


def historical_quote_volume(rows, entry_index: int, window_bars: int = 288) -> float | None:
    history = rows[entry_index - window_bars:entry_index] if entry_index >= window_bars else []
    if len(history) != window_bars or any(len(row) < 7 for row in history):
        return None
    try:
        value = sum(float(row[6]) for row in history)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def historical_score_v2_configuration(calibration_path: str | None, requested_threshold: float) -> dict:
    if calibration_path is None:
        return {
            "identifier": "unselected_candidate_pool",
            "weights": dict(APPROVED_SCORE_V2_WEIGHTS),
            "historical_available_threshold": requested_threshold,
            "selection_source": "not_selected",
            "holdout_used_for_selection": False,
            "validation_summary": {},
            "holdout_summary": {},
        }
    try:
        calibration = json.loads(Path(calibration_path).read_text())
        selected = calibration["selected"]
        holdout = calibration["holdout"]
        weights = selected["weights"]
        threshold = float(selected["historical_available_threshold"])
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError("invalid historical Score V2 calibration report") from None
    if (
        selected.get("selection_source") != "walk_forward_development_oos"
        or selected.get("holdout_used_for_selection") is not False
        or weights != APPROVED_SCORE_V2_WEIGHTS
        or not 0.0 <= threshold <= HISTORICAL_SCORE_V2_AVAILABLE_MAXIMUM
        or not isinstance(selected.get("name"), str)
    ):
        raise ValueError("calibration report is not a development walk-forward approved Score V2 configuration")
    if requested_threshold != 0.0 and requested_threshold != threshold:
        raise ValueError("requested historical threshold conflicts with selected calibration")
    return {
        "identifier": selected["name"], "weights": dict(weights),
        "historical_available_threshold": threshold, "selection_source": "walk_forward_development_oos",
        "holdout_used_for_selection": False,
        "validation_summary": dict(selected.get("validation") or {}),
        "holdout_summary": dict(holdout or {}),
    }


def long_unwind_passes(context: dict | None):
    if context is None:
        return False
    return bool(
        context["oi_change_1h_pct"] < 0.0
        and context["taker_long_short_volume_ratio"] < 1.0
        and context["top_trader_long_short_ratio"] > 1.0
    )


def resample(rows, multiplier: int):
    period = FIVE_MINUTES * multiplier
    buckets = defaultdict(list)
    for row in rows:
        buckets[row[0] - row[0] % period].append(row)
    result = []
    for start, group in sorted(buckets.items()):
        if len(group) != multiplier or group[0][0] != start or any(later[0] - earlier[0] != FIVE_MINUTES for earlier, later in zip(group, group[1:])):
            continue
        result.append([start, group[0][1], max(row[2] for row in group), min(row[3] for row in group), group[-1][4], sum(row[5] for row in group)])
    return result


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    changes = [later - earlier for earlier, later in zip(closes[-period - 1:-1], closes[-period:])]
    gains, losses = sum(max(change, 0) for change in changes), sum(max(-change, 0) for change in changes)
    if not losses:
        return 100.0 if gains else 50.0
    return 100.0 - 100.0 / (1.0 + (gains / period) / (losses / period))


def ema(values, period: int):
    if len(values) < period:
        return None
    value = mean(values[:period])
    multiplier = 2.0 / (period + 1)
    for price in values[period:]:
        value += multiplier * (price - value)
    return value


def is_bearish_trend(closes):
    fast, slow = ema(closes, 20), ema(closes, 50)
    return bool(fast is not None and slow is not None and closes[-1] < fast < slow)


def market_regime_at(rows, close_time: int):
    candles_4h = resample(rows, TIMEFRAMES["4h"])
    close_times = [row[0] + FIVE_MINUTES * TIMEFRAMES["4h"] for row in candles_4h]
    endpoint = bisect.bisect_right(close_times, close_time)
    return is_bearish_trend([row[4] for row in candles_4h[:endpoint]])


def evidence(rows, minimum_strength):
    if len(rows) < 20:
        return None
    previous, reclaim_bar, latest = rows[-3:]
    closes = [row[4] for row in rows]
    prior_rsi, current_rsi = rsi(closes[:-1]), rsi(closes)
    baseline_volume = mean(row[5] for row in rows[-13:-3])
    flags = {
        "two_bearish": reclaim_bar[4] < reclaim_bar[1] and latest[4] < latest[1],
        "lower_high": latest[2] < reclaim_bar[2],
        "volume_acceleration": latest[5] > baseline_volume and latest[5] > reclaim_bar[5],
        "rsi_rollover": prior_rsi is not None and current_rsi is not None and prior_rsi > current_rsi and current_rsi <= 55,
        "reclaim_or_repump": (
            previous[4] < rows[-4][3] and reclaim_bar[2] >= rows[-4][3] and latest[4] < rows[-4][3]
        ) or (reclaim_bar[2] > previous[2] and reclaim_bar[4] > previous[4] and latest[4] < reclaim_bar[4]),
        "bearish_close": latest[4] < latest[1],
    }
    weights = {"two_bearish": 18, "lower_high": 18, "volume_acceleration": 16, "rsi_rollover": 16, "reclaim_or_repump": 20, "bearish_close": 12}
    strength = sum(weight for name, weight in weights.items() if flags[name])
    passed = strength >= minimum_strength and flags["two_bearish"] and flags["lower_high"]
    support = min(row[3] for row in rows[-23:-3])
    support_broken = previous[4] < support and latest[4] < support
    failed_pullback = support_broken and reclaim_bar[2] >= support and reclaim_bar[4] < support and flags["lower_high"]
    pre_pump_base = min(row[3] for row in rows[-100:-40]) if len(rows) >= EVIDENCE_WINDOW else None
    pump_peak = max(row[2] for row in rows[-80:-3])
    pump_pct = (pump_peak / pre_pump_base - 1.0) * 100.0 if pre_pump_base and pre_pump_base > 0 else None
    pre_pump_volume = mean(row[5] for row in rows[-100:-40]) if len(rows) >= EVIDENCE_WINDOW else None
    pump_volume = max(row[5] for row in rows[-80:-3])
    volume_climax = bool(pre_pump_volume and pump_volume >= pre_pump_volume * 1.8)
    volume_decay = bool(pump_volume > 0 and mean(row[5] for row in rows[-3:]) <= pump_volume * 0.7)
    return {
        "strength": strength,
        "passed": passed,
        "flags": flags,
        "support": support,
        "support_broken": support_broken,
        "failed_pullback": failed_pullback,
        "pump_pct": round(pump_pct, 4) if pump_pct is not None else None,
        "volume_climax": volume_climax,
        "volume_decay": volume_decay,
        "hype_context": bool(pump_pct is not None and pump_pct >= 20.0 and volume_climax),
    }


def strategy_stages(checks, strategy: str):
    if strategy == "legacy":
        passed = [name for name, value in checks.items() if value["passed"]]
        return {
            "regime": "4h" in passed,
            "setup": "1h" in passed,
            "trigger": len(passed) >= 2,
        }
    if strategy != "waterfall_v2":
        raise ValueError(f"unsupported strategy: {strategy}")

    flags = {name: value["flags"] for name, value in checks.items()}
    return {
        "regime": all(flags["4h"][name] for name in ("lower_high", "bearish_close", "rsi_rollover")),
        "setup": all(flags["1h"][name] for name in ("two_bearish", "lower_high", "reclaim_or_repump")),
        "trigger": (
            all(flags["15m"][name] for name in ("two_bearish", "lower_high", "volume_acceleration", "bearish_close"))
            and all(flags["5m"][name] for name in ("lower_high", "bearish_close"))
        ),
    }


def post_hype_stages(checks):
    h4 = checks["4h"]
    trigger_15m = checks["15m"]["flags"]
    trigger_5m = checks["5m"]["flags"]
    return {
        "hype": h4["hype_context"],
        "damage": h4["support_broken"] and h4["flags"]["lower_high"],
        "setup": h4["failed_pullback"],
        "trigger": (
            all(trigger_15m[name] for name in ("two_bearish", "lower_high", "volume_acceleration", "bearish_close"))
            and all(trigger_5m[name] for name in ("lower_high", "bearish_close"))
        ),
    }


def signal_evidence(checks):
    return {
        timeframe: {
            name: bool(value)
            for name, value in checks[timeframe]["flags"].items()
        }
        for timeframe in TIMEFRAMES
    }


def outcome_details(rows, entry_index, stop_pct: float, target_pct: float, horizon_bars: int = 288):
    if horizon_bars < 1:
        raise ValueError("outcome horizon must include at least one candle")
    if entry_index < 0 or entry_index >= len(rows):
        raise ValueError("outcome entry is outside available candles")
    available_horizon = min(horizon_bars, len(rows) - entry_index)
    entry = rows[entry_index][1]
    stop, target = entry * (1.0 + stop_pct / 100.0), entry * (1.0 - target_pct / 100.0)
    for row in rows[entry_index:entry_index + available_horizon]:
        hit_stop, hit_target = row[2] >= stop, row[3] <= target
        if hit_stop and hit_target:
            return {
                "outcome": "loss", "realized_r": -1.0, "exit_price": stop,
                "exit_timestamp": row[0] + FIVE_MINUTES,
                "funding_cutoff_timestamp": row[0],
                "exit_reason": "same_candle_stop_and_target_conservative_loss",
            }
        if hit_stop:
            return {
                "outcome": "loss", "realized_r": -1.0, "exit_price": stop,
                "exit_timestamp": row[0] + FIVE_MINUTES,
                "funding_cutoff_timestamp": row[0], "exit_reason": "stop",
            }
        if hit_target:
            return {
                "outcome": "win", "realized_r": target_pct / stop_pct, "exit_price": target,
                "exit_timestamp": row[0] + FIVE_MINUTES,
                "funding_cutoff_timestamp": row[0], "exit_reason": "target",
            }
    last = rows[entry_index + available_horizon - 1]
    return {
        "outcome": "timeout",
        "realized_r": (entry - last[4]) / entry / (stop_pct / 100.0),
        "exit_price": last[4],
        "exit_timestamp": last[0] + FIVE_MINUTES,
        "funding_cutoff_timestamp": last[0] + FIVE_MINUTES,
        "exit_reason": "holding_horizon_mark_to_market",
    }


def outcome(rows, entry_index, stop_pct: float, target_pct: float, horizon_bars: int = 288):
    return outcome_details(rows, entry_index, stop_pct, target_pct, horizon_bars)["outcome"]


def realized_r(rows, entry_index, stop_pct: float, target_pct: float, horizon_bars: int = 288):
    return outcome_details(rows, entry_index, stop_pct, target_pct, horizon_bars)["realized_r"]


def modeled_round_trip_fee_r(profile: dict, *, stop_pct: float, entry_price: float,
                              exit_price: float, venue: str = "binance") -> float:
    required = {
        "schema_version", "venue", "product", "liquidity", "taker_commission_rate",
        "basis", "source_url",
    }
    if not isinstance(profile, dict) or not required.issubset(profile):
        raise ValueError("incomplete fee profile")
    if profile["schema_version"] != "binance_usdm_fee_model_v1":
        raise ValueError("unsupported fee profile schema")
    if profile["venue"] != venue or profile["product"] != "USDT perpetual" or profile["liquidity"] != "taker":
        raise ValueError("fee profile does not match the execution contract")
    if profile["basis"] != "modeled_official_api_example_not_account_specific":
        raise ValueError("fee profile must declare its modeled basis")
    parsed = urlparse(profile["source_url"])
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("fee profile requires HTTPS provenance")
    rate = profile["taker_commission_rate"]
    numeric = (rate, stop_pct, entry_price, exit_price)
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in numeric):
        raise ValueError("invalid fee profile or trade prices")
    if rate < 0 or stop_pct <= 0 or entry_price <= 0 or exit_price <= 0:
        raise ValueError("invalid fee profile or trade prices")
    stop_fraction = stop_pct / 100.0
    return round(float(rate) * (1.0 + exit_price / entry_price) / stop_fraction, 10)


def historical_short_funding_r(events: list, *, entry_price: float, stop_pct: float) -> float:
    if not isinstance(events, list) or entry_price <= 0 or stop_pct <= 0:
        raise ValueError("invalid historical funding input")
    contribution = 0.0
    last_timestamp = None
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("invalid historical funding event")
        timestamp = event.get("timestamp")
        rate = event.get("funding_rate")
        mark_price = event.get("mark_price")
        if (not isinstance(timestamp, int) or timestamp <= 0 or
                any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
                    for value in (rate, mark_price)) or mark_price <= 0):
            raise ValueError("incomplete historical funding event")
        if last_timestamp is not None and timestamp <= last_timestamp:
            raise ValueError("historical funding events are not strictly ordered")
        last_timestamp = timestamp
        contribution += float(rate) * float(mark_price) / entry_price
    return round(contribution / (stop_pct / 100.0), 10)


def evaluate(symbol: str, rows, minimum_strength: int, minimum_confirmed: int, stop_pct: float, target_pct: float,
             strategy: str = "legacy", trigger_interval: int = 48, cooldown_hours: int = 24,
             macro_rows=None, hold_hours: int = 24, derivatives: HistoricalDerivatives | None = None,
             derivatives_filter: str = "off", signal_start_ms: int | None = None,
             historical_available_threshold: float = 0.0,
             emit_candidate_pool: bool = False,
             minimum_quote_volume: float = 0.0,
             slippage_profile: dict | None = None,
             fee_profile: dict | None = None,
             executable_notional: float = 50.0):
    if trigger_interval not in {1, 3, 12, 48}:
        raise ValueError("trigger interval must close a 5m, 15m, 1h, or 4h candle")
    if cooldown_hours < 1:
        raise ValueError("cooldown hours must be at least one")
    if hold_hours < 1:
        raise ValueError("hold hours must be at least one")
    if not isinstance(historical_available_threshold, (int, float)) or not 0.0 <= historical_available_threshold <= HISTORICAL_SCORE_V2_AVAILABLE_MAXIMUM:
        raise ValueError("invalid historical Score V2 available-component threshold")
    horizon_bars = hold_hours * 12
    grouped = {name: resample(rows, multiplier) for name, multiplier in TIMEFRAMES.items()}
    close_times = {name: [row[0] + FIVE_MINUTES * TIMEFRAMES[name] for row in items] for name, items in grouped.items()}
    evidence_cache = {name: {} for name in TIMEFRAMES}

    def latest_evidence(name: str, close_time: int):
        endpoint = bisect.bisect_right(close_times[name], close_time)
        if endpoint not in evidence_cache[name]:
            evidence_cache[name][endpoint] = evidence(
                grouped[name][max(0, endpoint - EVIDENCE_WINDOW):endpoint], minimum_strength
            )
        return evidence_cache[name][endpoint]

    signals, cooldown_until = [], 0
    required_history = EVIDENCE_WINDOW if strategy in {"post_hype_v1", "channel_v1"} else 20
    for i in range(48 * required_history - 1, len(rows) - horizon_bars, trigger_interval):
        close_time = rows[i][0] + FIVE_MINUTES
        if signal_start_ms is not None and close_time < signal_start_ms:
            continue
        if close_time < cooldown_until and not emit_candidate_pool:
            continue
        h4 = latest_evidence("4h", close_time)
        if h4 is None:
            continue
        if strategy == "channel_v1" and not (h4["hype_context"] and h4["support_broken"] and h4["flags"]["lower_high"]):
            continue
        checks = {"4h": h4}
        checks.update({name: latest_evidence(name, close_time) for name in ("5m", "15m", "1h")})
        if any(value is None for value in checks.values()):
            continue
        if strategy == "post_hype_v1":
            stages = post_hype_stages(checks)
        elif strategy == "channel_v1":
            stages = channel_stages(checks)
        else:
            stages = strategy_stages(checks, strategy)
        passed = [name for name, value in checks.items() if value["passed"]]
        score = sum(WEIGHTS[name] * checks[name]["strength"] / 100.0 for name in TIMEFRAMES)
        if strategy == "legacy":
            eligible = "1h" in passed and "4h" in passed and len(passed) >= minimum_confirmed
        elif strategy in {"waterfall_v2", "channel_v1"}:
            eligible = all(stages.values())
        else:
            eligible = all(stages.values())
        macro_confirmed = True if macro_rows is None else all(market_regime_at(market_rows, close_time) for market_rows in macro_rows)
        eligible = eligible and macro_confirmed
        if not eligible:
            continue
        quote_volume_24h = historical_quote_volume(rows, i + 1)
        if quote_volume_24h is None or quote_volume_24h < minimum_quote_volume:
            continue
        derivative_data = None
        historical_score = None
        if derivatives_filter != "off":
            if derivatives_filter == "score_v2":
                derivative_data = derivatives.score_v2_at_or_before(symbol, close_time) if derivatives else None
                if derivative_data is None:
                    continue
                if derivative_data["taker_buy_sell_ratio"] >= 1.0:
                    continue
                historical_score = historical_score_v2_components(
                    checks, derivative_data, below_vwap=historical_below_vwap(rows, i + 1),
                )
                if historical_score["available_score"] is None:
                    continue
                if not emit_candidate_pool and historical_score["available_score"] < historical_available_threshold:
                    continue
            else:
                current = derivatives.at_or_before(symbol, close_time) if derivatives else None
                previous = derivatives.at_or_before(symbol, close_time - ONE_HOUR) if derivatives else None
                derivative_data = derivatives_context(current, previous)
            if derivatives_filter == "long_unwind_v1" and not long_unwind_passes(derivative_data):
                continue
        resolution = outcome_details(rows, i + 1, stop_pct, target_pct, horizon_bars)
        result = resolution["outcome"]
        gross_realized_r = round(resolution["realized_r"], 6)
        trade = {"symbol": symbol, "timestamp": close_time, "score": round(score, 2),
                        "strategy": strategy, "stages": stages,
                        "setup_type": stages.get("setup_type"),
                        "evidence": signal_evidence(checks),
                        "derivatives": derivative_data,
                        "historical_score_v2": historical_score,
                        "candidate_pool_event": bool(emit_candidate_pool),
                        "historical_24h_quote_volume_usdt": round(quote_volume_24h, 4),
                        "execution_features": "unavailable_no_historical_l2_or_trades",
                        "strategy_equivalent": False,
                        "macro_confirmed": macro_confirmed,
                        "exit_timestamp": resolution["exit_timestamp"],
                        "exit_price": round(resolution["exit_price"], 12),
                        "exit_reason": resolution["exit_reason"],
                        "outcome": result,
                        "realized_r": gross_realized_r}
        if slippage_profile is not None:
            slippage_r = empirical_slippage_cost_r(
                slippage_profile,
                stop_pct=stop_pct,
                executable_notional=executable_notional,
                venue="binance",
                minimum_quote_volume_usdt=minimum_quote_volume,
            )
            trade["slippage_adjusted_realized_r"] = round(gross_realized_r - slippage_r, 6)
            funding_events = derivatives.funding_between(
                symbol, close_time, resolution["funding_cutoff_timestamp"],
            ) if derivatives is not None and fee_profile is not None else None
            if fee_profile is not None and funding_events is not None:
                fee_r = modeled_round_trip_fee_r(
                    fee_profile, stop_pct=stop_pct, entry_price=rows[i + 1][1],
                    exit_price=resolution["exit_price"], venue="binance",
                )
                funding_r = historical_short_funding_r(
                    funding_events, entry_price=rows[i + 1][1], stop_pct=stop_pct,
                )
                net_realized_r = round(gross_realized_r - fee_r - slippage_r + funding_r, 10)
                trade["net_realized_r"] = net_realized_r
                trade["execution_costs"] = {
                    "complete": True,
                    "basis": "modeled",
                    "fee_r": fee_r,
                    "funding_r": funding_r,
                    "slippage_r": slippage_r,
                    "funding_observations": funding_events,
                    "provenance": {
                        "fee": fee_profile["source_url"],
                        "funding": FUNDING_RATE_DOC_URL,
                        "slippage": slippage_profile["source_url"],
                    },
                    "reason": "fee uses an explicit research model; funding and slippage use historical observations",
                }
            else:
                trade["execution_costs"] = {
                    "complete": False,
                    "basis": "incomplete",
                    "fee_r": None,
                    "funding_r": None,
                    "slippage_r": slippage_r,
                    "provenance": {"slippage": slippage_profile["source_url"]},
                    "reason": "fee profile or historical funding coverage unavailable",
                }
        signals.append(trade)
        if not emit_candidate_pool:
            cooldown_until = close_time + cooldown_hours * 3_600_000
    return signals


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, required=True, choices=(3, 5, 7, 10, 30, 90, 180))
    parser.add_argument("--symbols", type=int, default=50)
    parser.add_argument("--minimum-volume", type=float, default=5_000_000.0)
    parser.add_argument("--minimum-strength", type=int, default=55)
    parser.add_argument("--minimum-confirmed", type=int, default=3, choices=(2, 3, 4))
    parser.add_argument("--symbols-file", help="newline-delimited fixed USDT-perpetual symbols")
    parser.add_argument("--dashboard-url", help="current LBank dashboard candidates endpoint for a live watchlist snapshot")
    parser.add_argument("--stop-pct", type=float, default=2.15)
    parser.add_argument("--target-pct", type=float, default=4.5)
    parser.add_argument("--max-hold-hours", type=int, default=24)
    parser.add_argument("--strategy", choices=("legacy", "waterfall_v2", "post_hype_v1", CHANNEL_STRATEGY_ID), default=CHANNEL_STRATEGY_ID)
    parser.add_argument("--trigger-interval-minutes", type=int, default=240, choices=(5, 15, 60, 240))
    parser.add_argument("--cooldown-hours", type=int, default=24)
    parser.add_argument("--macro-regime", choices=("off", "btc_eth_4h"), default="off")
    parser.add_argument("--output", default="research/backtests")
    parser.add_argument("--cache-dir", default="research/cache/binance_5m")
    parser.add_argument("--derivatives-cache-dir", default="research/cache/binance_metrics")
    parser.add_argument("--funding-cache-dir", default="research/cache/binance_funding")
    parser.add_argument("--derivatives-filter", choices=("off", "long_unwind_v1", "score_v2"), default="off")
    parser.add_argument("--historical-score-v2-available-threshold", type=float, default=0.0)
    parser.add_argument("--score-v2-calibration-report")
    parser.add_argument("--end-ms", type=int, help="exclusive UTC 5m boundary; fixes one immutable research window")
    parser.add_argument("--warmup-days", type=int, default=0, help="completed historical days fetched before the measured window")
    parser.add_argument("--emit-candidate-pool", action="store_true")
    parser.add_argument("--slippage-profile")
    parser.add_argument("--fee-profile", help="versioned research-only taker fee model")
    parser.add_argument("--execution-notional-usdt", type=float, default=50.0)
    args = parser.parse_args()
    end_ms = args.end_ms or int(time.time() // 300 * 300_000)
    if end_ms % FIVE_MINUTES:
        raise SystemExit("end-ms must be aligned to a 5m UTC boundary")
    if args.warmup_days < 0:
        raise SystemExit("warmup-days cannot be negative")
    if not 0.0 <= args.historical_score_v2_available_threshold <= HISTORICAL_SCORE_V2_AVAILABLE_MAXIMUM:
        raise SystemExit("historical Score V2 available threshold must be within 0..75")
    if args.score_v2_calibration_report and args.derivatives_filter != "score_v2":
        raise SystemExit("a historical Score V2 calibration requires --derivatives-filter score_v2")
    if args.strategy == CHANNEL_STRATEGY_ID and args.warmup_days < 17:
        raise SystemExit("channel_v1 requires at least 17 completed warmup days")
    if args.emit_candidate_pool and (
        args.derivatives_filter != "score_v2"
        or args.score_v2_calibration_report
        or args.historical_score_v2_available_threshold != 0.0
    ):
        raise SystemExit("candidate pool requires score_v2, threshold 0, and no selected calibration")
    slippage_profile = None
    slippage_profile_sha256 = None
    if args.slippage_profile:
        try:
            profile_bytes = Path(args.slippage_profile).read_bytes()
            slippage_profile = json.loads(profile_bytes)
            empirical_slippage_cost_r(
                slippage_profile,
                stop_pct=args.stop_pct,
                executable_notional=args.execution_notional_usdt,
                venue="binance",
                minimum_quote_volume_usdt=args.minimum_volume,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise SystemExit(f"invalid empirical slippage profile: {exc}")
        slippage_profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
    fee_profile = None
    fee_profile_sha256 = None
    if args.fee_profile:
        if slippage_profile is None:
            raise SystemExit("fee profile requires an empirical slippage profile")
        if args.derivatives_filter == "off":
            raise SystemExit("fee profile requires historical funding coverage")
        try:
            fee_profile_bytes = Path(args.fee_profile).read_bytes()
            fee_profile = json.loads(fee_profile_bytes)
            modeled_round_trip_fee_r(
                fee_profile, stop_pct=args.stop_pct, entry_price=1.0, exit_price=1.0,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise SystemExit(f"invalid fee profile: {exc}")
        fee_profile_sha256 = hashlib.sha256(fee_profile_bytes).hexdigest()
    try:
        selected_score_v2_configuration = historical_score_v2_configuration(
            args.score_v2_calibration_report, args.historical_score_v2_available_threshold,
        ) if args.derivatives_filter == "score_v2" else None
    except ValueError as exc:
        raise SystemExit(str(exc))
    signal_start_ms = end_ms - args.days * 86_400_000
    start_ms = signal_start_ms - args.warmup_days * 86_400_000
    unavailable_dashboard_symbols = []
    if args.symbols_file and args.dashboard_url:
        raise SystemExit("choose either symbols-file or dashboard-url")
    if args.symbols_file:
        selected = [line.strip().upper() for line in Path(args.symbols_file).read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
        if not selected:
            raise SystemExit("symbols file is empty")
    elif args.dashboard_url:
        selected, unavailable_dashboard_symbols = dashboard_watchlist(args.dashboard_url)
    else:
        selected = universe(args.symbols, args.minimum_volume)
    macro_rows = None
    derivatives = HistoricalDerivatives(
        Path(args.derivatives_cache_dir), Path(args.funding_cache_dir), start_ms, end_ms,
    ) if args.derivatives_filter != "off" else None
    if args.macro_regime == "btc_eth_4h":
        macro_rows = []
        for symbol in ("BTCUSDT", "ETHUSDT"):
            rows = candles(symbol, start_ms, end_ms, Path(args.cache_dir))
            if rows is None:
                raise SystemExit(f"missing or invalid completed market-regime data for {symbol}")
            macro_rows.append(rows)
    results, rejected = [], []
    for index, symbol in enumerate(selected, start=1):
        rows = candles(symbol, start_ms, end_ms, Path(args.cache_dir))
        if rows is None:
            rejected.append(symbol)
            continue
        results.extend(evaluate(
            symbol, rows, args.minimum_strength, args.minimum_confirmed, args.stop_pct, args.target_pct,
            strategy=args.strategy, trigger_interval=args.trigger_interval_minutes // 5,
            cooldown_hours=args.cooldown_hours,
            macro_rows=macro_rows,
            hold_hours=args.max_hold_hours,
            derivatives=derivatives,
            derivatives_filter=args.derivatives_filter,
            signal_start_ms=signal_start_ms,
            historical_available_threshold=(selected_score_v2_configuration or {}).get("historical_available_threshold", 0.0),
            emit_candidate_pool=args.emit_candidate_pool,
            minimum_quote_volume=args.minimum_volume,
            slippage_profile=slippage_profile,
            fee_profile=fee_profile,
            executable_notional=args.execution_notional_usdt,
        ))
        print(f"[{index}/{len(selected)}] {symbol}: {sum(item['symbol'] == symbol for item in results)} signals", flush=True)
    reward_r = args.target_pct / args.stop_pct
    overall = summarize(results, reward_r)
    time_splits = purged_time_splits(results, signal_start_ms, end_ms, outcome_horizon_ms=args.max_hold_hours * 3_600_000)
    splits = {name: summarize(items, reward_r) for name, items in time_splits.items()}
    daily = defaultdict(int)
    for item in results:
        daily[datetime.fromtimestamp(item["timestamp"] / 1000, UTC).date().isoformat()] += 1
    report = {
        "source": "Binance USDⓈ-M perpetual public 5m klines",
        "source_provenance": {
            "klines_endpoint": f"{BASE_URL}/klines",
            "funding_endpoint": f"{BASE_URL}{FUNDING_RATE_ENDPOINT}",
            "metrics_archive": METRICS_ARCHIVE_URL,
            "interval": "5m", "product": "USDT perpetual",
        },
        "generated_at": datetime.now(UTC).isoformat(), "days": args.days, "symbols": selected,
        "window": {"start_ms": signal_start_ms, "end_ms": end_ms}, "warmup_start_ms": start_ms,
        "minimum_24h_quote_volume_usdt": args.minimum_volume, "rejected_symbols": rejected,
        "dashboard_unavailable_symbols": unavailable_dashboard_symbols,
        "minimum_strength": args.minimum_strength, "minimum_confirmed_timeframes": args.minimum_confirmed,
        "strategy": args.strategy, "trigger_interval_minutes": args.trigger_interval_minutes,
        "cooldown_hours": args.cooldown_hours, "macro_regime": args.macro_regime,
        "derivatives_filter": args.derivatives_filter,
        "historical_score_v2_configuration": selected_score_v2_configuration,
        "historical_score_v2_contract": {
            "score_version": "score_v2_historical_available_v1",
            "available_component_maximum": HISTORICAL_SCORE_V2_AVAILABLE_MAXIMUM,
            "unavailable_components": ["execution_microstructure", "cross_exchange_confirmation"],
        } if args.derivatives_filter == "score_v2" else None,
        "execution_features": "unavailable_no_historical_l2_or_trades",
        "strategy_equivalent": False,
        "candidate_pool_complete": bool(args.emit_candidate_pool),
        "candidate_pool_contract": "pre_threshold_pre_cooldown_v1" if args.emit_candidate_pool else None,
        "execution_notional_usdt": args.execution_notional_usdt,
        "empirical_slippage_profile": {
            "path": args.slippage_profile,
            "sha256": slippage_profile_sha256,
            "profile": slippage_profile,
        } if slippage_profile is not None else None,
        "fee_profile": {
            "path": args.fee_profile,
            "sha256": fee_profile_sha256,
            "profile": fee_profile,
        } if fee_profile is not None else None,
        "net_ev_contract": {
            "schema_version": "historical_net_ev_v1",
            "cost_basis": "modeled",
            "fee": "versioned taker fee model, not account-specific realized commission",
            "funding": "public historical funding charges after entry through exit cutoff",
            "slippage": "empirical same-venue same-notional observational profile",
            "promotion_permitted": False,
        } if fee_profile is not None else None,
        "max_hold_hours": args.max_hold_hours,
        "stop_pct": args.stop_pct, "target_pct": args.target_pct,
        **overall,
        "reward_r": round(reward_r, 4),
        "splits": splits,
        "signals_per_day": round(len(results) / args.days, 2), "daily_signals": dict(daily), "trades": results,
    }
    report["promotion_eligibility"] = promotion_eligibility(
        report["splits"]["holdout"], report["signals_per_day"],
        validation_summary=report["splits"]["validation"], reward_r=reward_r,
        strategy_equivalent=False,
    )
    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"binance_perp_{args.days}d_{int(time.time())}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({key: report[key] for key in ("days", "signals", "settled", "wins", "win_rate", "settled_expectancy_r", "realized_expectancy_r", "timeouts", "signals_per_day", "promotion_eligibility", "rejected_symbols")}, ensure_ascii=False))
    print(path)


if __name__ == "__main__":
    main()
