# Balanced Signal Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic regime/setup/trigger model and a chronological backtest protocol that increases simulated-alert coverage without weakening live execution gates.

**Architecture:** Keep the existing live validator as the sole owner of state transitions. Add pure, testable structure evaluation to the candle analyzer; have the validator consume its regime/setup/trigger evidence without creating synthetic market data. Extend the research runner with fixed-universe chronological windows, structural stops, bounded parameter grids, and complete provenance artifacts.

**Tech Stack:** Python 3.12, FastAPI, CCXT live adapters, standard-library research runner, pytest, Docker Compose.

## Global Constraints

- Only active linear USDT-settled perpetual contracts are eligible.
- Use only real exchange data; missing, duplicate, gapped, invalid, open, stale, or unavailable data fails closed.
- Keep `LIVE_TRADING_ENABLED=false`; no order-placement API may be added.
- Gemini/Ollama remain advisory-only and cannot create a trigger.
- Preserve existing container security, lifecycle database code, WebSocket layer, and frontend transport.
- TP used for acceptance must be at least 1R net of configured costs; simultaneous TP/SL candles resolve as Stop-first.
- Historical footprint/orderbook data must not be simulated; live footprint remains a required live-data gate only when available.

---

### Task 1: Add pure regime/setup/trigger evidence

**Files:**
- Modify: `backend/src/waterfallhunter/core/candle_analyzer.py`
- Create: `backend/tests/test_candle_analyzer.py`

**Interfaces:**
- Produces: `_evaluate(candles) -> dict` containing `regime_bearish: bool`, `setup: str | None`, `trigger_ready: bool`, `dynamic_support: float`, and existing evidence keys.
- Consumes: lists of closed `[timestamp, open, high, low, close, volume]` candles only.

- [ ] **Step 1: Write failing structural tests**

```python
from waterfallhunter.core.candle_analyzer import MultiTimeframeAnalyzer


def make_gapped_rows():
    return [[1_000_000, 1, 2, 1, 1.5, 10], [1_900_000, 1.5, 2, 1, 1.2, 10]]


def make_closed_candles_with_failed_pullback():
    rows = [[index * 300_000, 10, 10.2, 9.8, 10.0, 10] for index in range(20)]
    rows[-4:] = [
        [4_800_000, 10.0, 10.1, 9.7, 9.8, 10],
        [5_100_000, 9.8, 9.9, 9.3, 9.4, 10],
        [5_400_000, 9.4, 9.8, 9.2, 9.55, 10],
        [5_700_000, 9.55, 9.7, 9.0, 9.2, 30],
    ]
    return rows


def test_failed_pullback_requires_broken_support_and_rejection():
    analyzer = MultiTimeframeAnalyzer()
    candles = make_closed_candles_with_failed_pullback()
    result = analyzer._evaluate(candles)
    assert result["regime_bearish"] is True
    assert result["setup"] == "FAILED_PULLBACK"


def test_open_or_gapped_candles_are_rejected():
    analyzer = MultiTimeframeAnalyzer()
    assert analyzer._closed_candles(make_gapped_rows(), "5m") is None
```

- [ ] **Step 2: Run the focused test and confirm the first assertion fails**

Run: `PYTHONPATH=backend/src pytest backend/tests/test_candle_analyzer.py -v`

Expected: FAIL because `regime_bearish` and `trigger_ready` are absent.

- [ ] **Step 3: Implement minimal evidence derivation**

```python
support = min(row[3] for row in candles[-23:-3])
support_broken = previous[4] < support and latest[4] < support
failed_pullback = support_broken and reclaim_bar[2] >= support and reclaim_bar[4] < support and lower_high
strong_breakdown = support_broken and volume_acceleration and bearish_close
regime_bearish = support_broken and lower_high
trigger_ready = two_closed and lower_high and bearish_close and (reclaim or repump)
```

Return these fields with the existing RSI, volume, reclaim, repump and setup evidence. Do not read a clock, exchange, or open candle inside `_evaluate`.

- [ ] **Step 4: Run the focused tests**

Run: `PYTHONPATH=backend/src pytest backend/tests/test_candle_analyzer.py -v`

Expected: PASS.

- [ ] **Step 5: Commit when Git metadata is available**

```bash
git add backend/src/waterfallhunter/core/candle_analyzer.py backend/tests/test_candle_analyzer.py
git commit -m "feat: expose deterministic regime and trigger evidence"
```

If the workspace remains outside a Git repository, record that the commit step is unavailable and do not initialize a repository.

### Task 2: Gate live state transitions with staged evidence

**Files:**
- Modify: `backend/src/waterfallhunter/core/multi_exchange_validator.py`
- Create: `backend/tests/test_multi_exchange_validator.py`

**Interfaces:**
- Consumes: candle detail dictionaries from `MultiTimeframeAnalyzer.analyze_candles` and real microstructure results.
- Produces: `metrics["strategy_stages"]` with `regime`, `setup`, `trigger`, and `passed`; preserves existing `quality_gates` and `score_components`.

- [ ] **Step 1: Write failing stage-gate tests**

```python
def test_regime_without_trigger_cannot_arm(validator):
    status = validator._suggested_status(
        score=95.0,
        stages={"regime": True, "setup": "FAILED_PULLBACK", "trigger": False},
        microstructure_approved=True,
        cross_exchange_confirmed=True,
    )
    assert status == "WATCH"


def test_complete_stage_chain_can_arm_before_trigger_threshold(validator):
    status = validator._suggested_status(
        score=70.0,
        stages={"regime": True, "setup": "FAILED_PULLBACK", "trigger": True},
        microstructure_approved=True,
        cross_exchange_confirmed=True,
    )
    assert status == "ARMED"
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `PYTHONPATH=backend/src pytest backend/tests/test_multi_exchange_validator.py -v`

Expected: FAIL because `_suggested_status` does not exist.

- [ ] **Step 3: Implement a pure state-decision helper and wire it into `cross_check_symbol`**

```python
def _suggested_status(self, score, stages, microstructure_approved, cross_exchange_confirmed):
    complete = bool(stages["regime"] and stages["setup"] and stages["trigger"])
    if not (complete and microstructure_approved and cross_exchange_confirmed):
        return "WATCH"
    if score >= self.triggered_threshold:
        return "TRIGGERED"
    return "ARMED" if score >= self.armed_threshold else "WATCH"
```

Build `strategy_stages` from valid 4h regime, valid 1h setup, and valid 15m/5m trigger evidence. Keep the four-timeframe validity, live orderbook, spoofing, spread, slippage, exchange-filter and cross-exchange gates intact.

- [ ] **Step 4: Run the focused tests**

Run: `PYTHONPATH=backend/src pytest backend/tests/test_multi_exchange_validator.py -v`

Expected: PASS.

- [ ] **Step 5: Compile and run the backend health check**

Run: `python3 -m compileall -q backend/src && docker compose up -d --build waterfall-backend && docker compose ps waterfall-backend`

Expected: compile exits 0 and backend reports `healthy`.

### Task 3: Make backtests chronological, structural, and auditable

**Files:**
- Modify: `scripts/historical_backtest.py`
- Create: `backend/tests/test_historical_backtest.py`
- Create: `research/backtests/README.md`

**Interfaces:**
- Produces: JSON report fields `train`, `validation`, `holdout`, `expectancy_r`, `settled`, `timeouts`, `parameters`, and `source_provenance`.
- Consumes: a fixed symbol file and real Binance USDⓈ-M 5m candles.

- [ ] **Step 1: Write failing purity and outcome tests**

```python
from scripts.historical_backtest import expectancy_r, outcome

def test_same_bar_tp_and_stop_is_a_loss():
    rows = [[0, 100, 103, 95, 100, 1]]
    assert outcome(rows, 0, stop_pct=2.0, target_pct=4.0) == "loss"


def test_expectancy_uses_only_settled_outcomes():
    assert expectancy_r(["win", "loss", "timeout"], reward_r=2.0) == 0.5
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `PYTHONPATH=. pytest backend/tests/test_historical_backtest.py -v`

Expected: FAIL because `expectancy_r` is absent.

- [ ] **Step 3: Add chronological split and bounded grid reporting**

```python
def expectancy_r(outcomes, reward_r):
    settled = [item for item in outcomes if item in {"win", "loss"}]
    return None if not settled else sum(reward_r if item == "win" else -1.0 for item in settled) / len(settled)
```

For a six-month source run, split ordered signals into the oldest 50% train, next 33.33% validation, and final 16.67% holdout by timestamp. Rank candidate parameters by validation expectancy, then report the selected parameter unchanged on holdout. Reject a candidate if validation has fewer than 50 settled trades, target reward is less than 1R, or expectancy is not positive.

- [ ] **Step 4: Run the focused tests**

Run: `PYTHONPATH=. pytest backend/tests/test_historical_backtest.py -v`

Expected: PASS.

- [ ] **Step 5: Run the fixed-universe research job and inspect JSON**

Run: `python3 scripts/historical_backtest.py --days 180 --symbols-file research/top10_research_universe.txt --minimum-strength 55 --minimum-confirmed 2 --stop-pct 2.15 --target-pct 4.5 --output research/backtests`

Expected: exit 0, an output JSON containing source timestamps, all symbol names, source provenance, settled outcomes, timeouts, and split metrics. It may fail acceptance; that is a valid result.

### Task 4: Verify and review the integrated result

**Files:**
- Modify: `docs/superpowers/specs/2026-08-09-balanced-signal-quality-design.md` only if implementation changes an approved interface.
- Review: `backend/src/waterfallhunter/core/candle_analyzer.py`, `backend/src/waterfallhunter/core/multi_exchange_validator.py`, `scripts/historical_backtest.py`

**Interfaces:**
- Consumes: passing tests and generated real-data reports from Tasks 1-3.
- Produces: a verification summary that separates demonstrated results from unavailable historical-L2 limitations.

- [ ] **Step 1: Run all focused tests and Python compilation**

Run: `PYTHONPATH=backend/src pytest backend/tests/test_candle_analyzer.py backend/tests/test_multi_exchange_validator.py backend/tests/test_historical_backtest.py -v && python3 -m compileall -q backend/src scripts`

Expected: exit 0.

- [ ] **Step 2: Check live runtime behavior without placing an order**

Run: `docker exec -i waterfall-backend /opt/venv/bin/python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=10).read().decode())"`

Expected: a healthy response; no order endpoint is invoked.

- [ ] **Step 3: Run CodeRabbit only if the workspace becomes a Git repository and authentication is available**

Run: `coderabbit review --agent -t uncommitted`

Expected: NDJSON review output. If Git metadata or authentication is unavailable, report the exact prerequisite rather than claiming a CodeRabbit review.

- [ ] **Step 4: Record acceptance decision**

Report win rate, settled trades, expectancy, timeout count, signals/day, and whether all acceptance criteria passed. Do not deploy a research configuration to live state transitions unless every criterion passes.
