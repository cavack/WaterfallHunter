# Score V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the directional live score with a versioned Score V2 that assigns 100 points only to a complete, fresh USDT-perpetual evidence packet and calibrates score thresholds against real historical Binance data.

**Architecture:** A pure `ScoreV2` component owns hard-gate evaluation and the 35/20/20/15/5/5 breakdown. Live exchange adapters provide normalized real data to the scorer, while the historical runner computes only feature-equivalent technical and derivatives components and marks unavailable execution features explicitly. The validator remains the owner of state transitions and only consumes the scorer's public result.

**Tech Stack:** Python 3.12, FastAPI, CCXT 4.5, Binance public USD-M futures endpoints, standard-library research runner, pytest, Prometheus client, Next.js 14, Fallow.

## Global Constraints

- Only active linear USDT-settled perpetual contracts are eligible.
- Only fresh, real, source-attributed exchange data may be used; unavailable data is never converted to zero.
- `LIVE_TRADING_ENABLED=false` remains unchanged; no order-placement endpoint is added.
- Gemini/Ollama remain advisory-only and receive no score weight or veto authority.
- Do not modify security hardening, database lifecycle semantics, candle validity rules, or microstructure calculations except to consume their existing public metrics.
- LBank/external price compatibility is an integrity gate only; it receives no directional score points.
- Historical L2/orderbook/trade features are reported unavailable rather than simulated.
- The workspace has no Git metadata; do not initialize a repository or attempt commits.

---

### Task 1: Introduce a pure Score V2 contract

**Files:**
- Create: `backend/src/waterfallhunter/core/score_v2.py`
- Create: `backend/tests/test_score_v2.py`

**Interfaces:**
- Produces: `ScoreV2.evaluate(candles: dict, microstructure: dict, derivatives: dict, cross_exchange_confirmed: bool, price_location: dict) -> dict`.
- Produces: `{"score_version":"score_v2","is_valid":bool,"score":float|None,"components":dict,"gates":dict,"reason":str|None}`.
- Consumes: existing candle details, existing normalized microstructure fields, and normalized derivatives fields only.

- [ ] **Step 1: Write the failing score-contract tests**

```python
from waterfallhunter.core.score_v2 import ScoreV2


def complete_packet():
    return {
        "candles": {
            "4h": {"valid": True, "hype_context": True, "support_broken": True, "lower_high": True,
                   "setup": "FAILED_PULLBACK", "bearish_close": True, "volume_acceleration": True},
            "1h": {"valid": True, "two_closed_candles": True, "lower_high": True, "reclaim": True,
                   "repump": False, "rsi_rollover": True, "bearish_close": True, "volume_acceleration": True},
            "15m": {"valid": True, "two_closed_candles": True, "lower_high": True, "reclaim": True,
                    "repump": False, "rsi_rollover": True, "bearish_close": True, "volume_acceleration": True},
            "5m": {"valid": True, "two_closed_candles": True, "lower_high": True, "reclaim": True,
                   "repump": False, "rsi_rollover": True, "bearish_close": True, "volume_acceleration": True},
        },
        "microstructure": {"approved": True, "spoofing_detected": False, "sell_flow_usdt": 60, "buy_flow_usdt": 40,
                             "footprint": {"available": True, "aggressive_selling": True}, "bid_depth_usdt": 1000,
                             "ask_depth_usdt": 1000, "spread_pct": 0.05, "slippage_pct": 0.05},
        "derivatives": {"available": True, "funding_rate": 0.0001, "funding_percentile": 0.9,
                          "oi_change_1h_pct": -1, "taker_buy_sell_ratio": 0.8,
                          "top_trader_long_short_ratio": 1.2},
    }


def test_complete_score_v2_packet_has_fixed_component_total():
    packet = complete_packet()
    result = ScoreV2().evaluate(packet["candles"], packet["microstructure"], packet["derivatives"], True, {"below_vwap": True})
    assert result["is_valid"] is True
    assert result["score_version"] == "score_v2"
    assert result["components"] == {
        "structural_post_pump": 35.0, "entry_timing": 20.0, "execution_microstructure": 20.0,
        "derivatives_confirmation": 15.0, "cross_exchange_confirmation": 5.0, "same_contract_price_location": 5.0,
    }
    assert result["score"] == 100.0


def test_incomplete_derivative_packet_is_unavailable_not_a_zero_component():
    packet = complete_packet()
    packet["derivatives"] = {"available": False, "reason": "missing valid funding rate"}
    result = ScoreV2().evaluate(packet["candles"], packet["microstructure"], packet["derivatives"], True, {"below_vwap": True})
    assert result["is_valid"] is False
    assert result["score"] is None
    assert result["reason"] == "incomplete fresh derivatives packet"
```

- [ ] **Step 2: Run the score-contract tests and confirm RED**

Run:

```bash
docker run --rm --user 0:0 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -e PYTHONPATH=/project/backend/src:/project -v /srv/waterfallhunter:/project:ro \
  -w /project/backend waterfallhunter-waterfall-backend \
  pytest -q -p no:cacheprovider tests/test_score_v2.py
```

Expected: collection fails because `waterfallhunter.core.score_v2` does not exist.

- [ ] **Step 3: Implement the minimal pure scorer**

```python
class ScoreV2:
    version = "score_v2"

    def evaluate(self, candles, microstructure, derivatives, cross_exchange_confirmed, price_location):
        gates = self._gates(candles, microstructure, derivatives, cross_exchange_confirmed)
        if not all(gates.values()):
            return {"score_version": self.version, "is_valid": False, "score": None,
                    "components": {}, "gates": gates, "reason": self._reason(gates)}
        components = {
            "structural_post_pump": self._structural(candles),
            "entry_timing": self._timing(candles),
            "execution_microstructure": self._execution(microstructure),
            "derivatives_confirmation": self._derivatives(derivatives),
            "cross_exchange_confirmation": 5.0,
            "same_contract_price_location": 5.0 if price_location["below_vwap"] else 0.0,
        }
        return {"score_version": self.version, "is_valid": True, "score": round(sum(components.values()), 2),
                "components": components, "gates": gates, "reason": None}
```

Implement the six component maxima exactly as `35/20/20/15/5/5`. Make each component a bounded pure function. A `FAILED_PULLBACK` receives the structural setup points before a continuation. Do not read clocks, call exchanges, or access global state in this module.

- [ ] **Step 4: Run focused tests and the existing validator tests**

Run:

```bash
docker run --rm --user 0:0 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -e PYTHONPATH=/project/backend/src:/project -v /srv/waterfallhunter:/project:ro \
  -w /project/backend waterfallhunter-waterfall-backend \
  pytest -q -p no:cacheprovider tests/test_score_v2.py tests/test_multi_exchange_validator.py
```

Expected: all selected tests pass.

---

### Task 2: Complete the live Binance derivatives packet through canonical identifiers

**Files:**
- Modify: `backend/src/waterfallhunter/core/derivatives.py`
- Modify: `backend/src/waterfallhunter/core/multi_exchange.py`
- Modify: `backend/src/waterfallhunter/core/multi_exchange_validator.py`
- Modify: `backend/tests/test_derivatives.py`
- Modify: `backend/tests/test_multi_exchange.py`

**Interfaces:**
- Consumes: `exchange.markets[mapped_symbol]["id"]` for the raw Binance USD-M symbol.
- Produces: a complete derivative context with `funding_percentile`, `oi_change_1h_pct`, `taker_buy_sell_ratio`, and `top_trader_long_short_ratio`.
- Produces: an unavailable context with explicit `reason` and `fallback_attempts` when no compatible venue supplies the complete packet.

- [ ] **Step 1: Write the failing canonical-packet tests**

```python
def test_binance_packet_uses_market_id_and_rejects_a_missing_taker_ratio():
    result = DerivativesAnalyzer().evaluate_packet(
        exchange="binance", mapped_symbol="1000PEPE/USDT:USDT", market_id="1000PEPEUSDT",
        funding_history=[0.00005, 0.0001, 0.0002], current_funding=0.0002,
        current_oi=1_000_000, oi_one_hour_ago=1_020_000,
        taker_buy_sell_ratio=None, top_trader_long_short_ratio=1.3, retrieved_at=1_700_000_000.0,
    )
    assert result["available"] is False
    assert result["reason"] == "missing valid taker buy/sell ratio"
    assert result["market_id"] == "1000PEPEUSDT"


def test_complete_packet_contains_no_default_derivative_values():
    result = DerivativesAnalyzer().evaluate_packet(
        exchange="binance", mapped_symbol="1000PEPE/USDT:USDT", market_id="1000PEPEUSDT",
        funding_history=[0.00005, 0.0001, 0.0002], current_funding=0.0002,
        current_oi=1_000_000, oi_one_hour_ago=1_020_000,
        taker_buy_sell_ratio=0.8, top_trader_long_short_ratio=1.3, retrieved_at=1_700_000_000.0,
    )
    assert result["available"] is True
    assert result["funding_percentile"] == 1.0
    assert result["oi_change_1h_pct"] == -1.9608
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
docker run --rm --user 0:0 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -e PYTHONPATH=/project/backend/src:/project -v /srv/waterfallhunter:/project:ro \
  -w /project/backend waterfallhunter-waterfall-backend \
  pytest -q -p no:cacheprovider tests/test_derivatives.py::test_binance_packet_uses_market_id_and_rejects_a_missing_taker_ratio
```

Expected: FAIL because `evaluate_packet` does not exist.

- [ ] **Step 3: Fetch and normalize only real Binance USD-M public data**

```python
raw_market_id = market["id"]
funding_history = await exchange.fapiPublicGetFundingRate({"symbol": raw_market_id, "limit": 90})
taker_rows = await exchange.fapiDataGetTakerlongshortRatio({"symbol": raw_market_id, "period": "5m", "limit": 1})
top_rows = await exchange.fapiDataGetTopLongShortAccountRatio({"symbol": raw_market_id, "period": "5m", "limit": 1})
oi_rows = await exchange.fapiDataGetOpenInterestHist({"symbol": raw_market_id, "period": "5m", "limit": 13})
```

Require all response rows to have finite values and timestamps within the freshness window. Calculate funding percentile from the returned settled funding history and current funding without interpolation. Calculate the one-hour OI change from the newest and oldest real OI observations covering at least 55 minutes. Source priority is Binance first; only implement another venue adapter when it can supply all five normalized fields, not a partial substitute.

- [ ] **Step 4: Wire the complete packet to the validator**

Replace the existing partial derivative context call with the new packet. Store the source name, mapped symbol, raw market identifier, retrieval timestamp, fallback attempts, and explicit missing reason. Do not score or transition a candidate when the packet is unavailable.

- [ ] **Step 5: Run focused tests and a real read-only Binance check**

Run:

```bash
docker run --rm --user 0:0 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -e PYTHONPATH=/project/backend/src:/project -v /srv/waterfallhunter:/project:ro \
  -w /project/backend waterfallhunter-waterfall-backend \
  pytest -q -p no:cacheprovider tests/test_derivatives.py tests/test_multi_exchange.py tests/test_multi_exchange_validator.py
```

Then run a read-only `1000PEPE/USDT:USDT` request and assert `available=true`, `market_id=1000PEPEUSDT`, and every required derivative field is finite.

---

### Task 3: Replace live score assembly and expose unavailable states honestly

**Files:**
- Modify: `backend/src/waterfallhunter/core/multi_exchange_validator.py`
- Modify: `backend/src/waterfallhunter/core/dashboard.py`
- Modify: `backend/src/waterfallhunter/main.py`
- Modify: `backend/tests/test_multi_exchange_validator.py`
- Modify: `backend/tests/test_dashboard.py`

**Interfaces:**
- Consumes: `ScoreV2.evaluate` output.
- Produces: `metrics["score_version"]`, `metrics["score_components"]`, `metrics["quality_gates"]`, and an explicit analysis reason.
- Produces: score `None` only with a non-empty unavailable reason; never `0` as a missing-data substitute.

- [ ] **Step 1: Write the failing validator integration test**

```python
def test_score_v2_replaces_price_dislocation_and_preserves_integrity_gate(validator):
    result = validator._merge_score_v2(
        candles=complete_candles(), microstructure=complete_microstructure(), derivatives=complete_derivatives(),
        cross_exchange_confirmed=True, ticker={"last": 90, "vwap": 100}, reference_price=100,
    )
    assert result["score_version"] == "score_v2"
    assert "price_dislocation" not in result["score_components"]
    assert result["score_components"]["same_contract_price_location"] == 5.0
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
docker run --rm --user 0:0 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -e PYTHONPATH=/project/backend/src:/project -v /srv/waterfallhunter:/project:ro \
  -w /project/backend waterfallhunter-waterfall-backend \
  pytest -q -p no:cacheprovider tests/test_multi_exchange_validator.py::test_score_v2_replaces_price_dislocation_and_preserves_integrity_gate
```

Expected: FAIL because `_merge_score_v2` does not exist.

- [ ] **Step 3: Integrate Score V2 without altering candle or microstructure internals**

Make `cross_check_symbol` retain the current source-selection and price-compatibility check. Build `price_location` only from the selected contract's current ticker and VWAP. Pass the existing candle details and microstructure output plus the complete derivative packet to `ScoreV2`. Set state eligibility only when `ScoreV2.is_valid` is true. Keep current state thresholds until Task 4 reports a qualified calibration.

- [ ] **Step 4: Compact the new metric contract for API/SSE consumers**

Expose only these derivative and score fields: version, score, components, gates, source, mapped symbol, market ID, timestamps, freshness reason, funding rate/percentile, OI change, taker ratio, and top-trader ratio. Keep raw exchange responses server-only.

- [ ] **Step 5: Run contract tests**

Run:

```bash
docker run --rm --user 0:0 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -e PYTHONPATH=/project/backend/src:/project -v /srv/waterfallhunter:/project:ro \
  -w /project/backend waterfallhunter-waterfall-backend \
  pytest -q -p no:cacheprovider tests/test_score_v2.py tests/test_multi_exchange_validator.py tests/test_dashboard.py
```

Expected: all selected tests pass.

---

### Task 4: Add feature-equivalent historical derivatives and calibration reporting

**Files:**
- Modify: `scripts/historical_backtest.py`
- Create: `scripts/calibrate_score_v2.py`
- Modify: `backend/tests/test_historical_backtest.py`
- Create: `backend/tests/test_score_v2_calibration.py`
- Create: `research/backtests/score_v2_methodology.md`

**Interfaces:**
- Produces: real historical Funding, OI, taker ratio, and top-trader ratio at or before an entry timestamp.
- Produces: a calibration JSON that contains every tested weight vector, validation metrics, untouched holdout metrics, source windows, rejected symbols, and promotion eligibility.
- Consumes: only public Binance USD-M historical endpoints and existing real five-minute candles.

- [ ] **Step 1: Write the failing historical-derivatives test**

```python
def test_historical_derivatives_rejects_a_timestamp_without_all_real_features():
    context = score_v2_derivatives_context(
        funding_rate=0.0001, funding_history=[0.00005, 0.0001],
        oi_current=100, oi_one_hour_ago=101, taker_ratio=None, top_ratio=1.2,
    )
    assert context is None


def test_calibration_never_uses_holdout_to_select_weights():
    selected = select_weights(train=[{"score": 80, "outcome": "win"}], validation=[{"score": 80, "outcome": "win"}],
                              holdout=[{"score": 80, "outcome": "loss"}])
    assert selected["selection_source"] == "validation"
    assert selected["holdout_used_for_selection"] is False
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```bash
PYTHONPATH=. docker run --rm --user 0:0 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -e PYTHONPATH=/project/backend/src:/project -v /srv/waterfallhunter:/project:ro \
  -w /project/backend waterfallhunter-waterfall-backend \
  pytest -q -p no:cacheprovider tests/test_historical_backtest.py tests/test_score_v2_calibration.py
```

Expected: FAIL because Score V2 historical helpers and calibration script do not exist.

- [ ] **Step 3: Implement real historical sources and feature parity**

Use the existing Binance daily metrics archive for OI/taker/top-trader observations. Retrieve Funding Rate history from Binance USD-M's public funding endpoint bounded to the fixed research window. Reject a timestamp when any Score V2 derivatives field is absent or older than its defined historical observation window. Preserve source URLs and timestamps in every report.

Do not synthesize execution/microstructure features. Historical output must label execution score as unavailable and set `strategy_equivalent=false` until a real historical L2/trade source is implemented.

- [ ] **Step 4: Implement bounded validation-only calibration**

Use a finite list of candidate weights that always sums to 100 and keeps component maxima from the approved design. Rank only by validation: positive realized expectancy first, then lower validation-to-train degradation, then settled count. Evaluate the selected configuration once against holdout. The script must report an ineligible result rather than force a parameter set when the minimum settled, expectancy, reward, or density criteria fail.

- [ ] **Step 5: Run the immutable real-data research job**

Run one fixed 180-day, 50-symbol research window, followed by 90-day, 30-day, and 7-day reports using the same selected configuration and immutable end timestamp. Save every report under `research/backtests/score_v2/`.

Expected: each report identifies its exact source window, selected weights, all rejected symbols, settled outcomes, timeout rate, signals/day, validation metrics, holdout metrics, and promotion decision.

---

### Task 5: Add observability, dashboard rendering, and final verification

**Files:**
- Modify: `backend/src/waterfallhunter/main.py`
- Modify: `backend/src/waterfallhunter/core/dashboard.py`
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/app/globals.css` only if presentation needs a new semantic status style
- Modify: `backend/tests/test_dashboard.py`
- Create: `frontend/components/score-card.tsx`
- Create: `frontend/components/market-context.tsx`

**Interfaces:**
- Produces: compact dashboard fields for score version, component breakdown, gate status, and derivative provenance.
- Produces: Prometheus counters for complete/incomplete derivative packet outcomes by source and reason.
- Consumes: compact API/SSE payload only; frontend does not calculate a score.

- [ ] **Step 1: Write the failing compact-metrics test**

```python
def test_compact_metrics_exposes_score_v2_without_raw_exchange_payloads():
    compacted = compact_metrics({
        "score_version": "score_v2",
        "score_components": {"derivatives_confirmation": 12.0},
        "quality_gates": {"derivatives_complete": True},
        "derivatives": {"market_id": "1000PEPEUSDT", "raw": {"secret": "not exposed"}},
    })
    assert compacted["score_version"] == "score_v2"
    assert compacted["derivatives"]["market_id"] == "1000PEPEUSDT"
    assert "raw" not in compacted["derivatives"]
```

- [ ] **Step 2: Run focused test and confirm RED**

Run:

```bash
docker run --rm --user 0:0 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -e PYTHONPATH=/project/backend/src:/project -v /srv/waterfallhunter:/project:ro \
  -w /project/backend waterfallhunter-waterfall-backend \
  pytest -q -p no:cacheprovider tests/test_dashboard.py::test_compact_metrics_exposes_score_v2_without_raw_exchange_payloads
```

Expected: FAIL because the new score fields are not compacted yet.

- [ ] **Step 3: Implement backend observability and split frontend display components**

Add Prometheus counters with bounded labels: `source` and normalized reason code only. Split the current large dashboard render function into `ScoreCard` and `MarketContext` components. Render unavailable analysis as a reason-bearing state and render component points only when the backend declares `score_version=score_v2`. Do not derive, round into, or fill missing score components in the browser.

- [ ] **Step 4: Run backend, frontend, and Fallow quality gates**

Run:

```bash
docker run --rm --user 0:0 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -e PYTHONPATH=/project/backend/src:/project -v /srv/waterfallhunter:/project:ro \
  -w /project/backend waterfallhunter-waterfall-backend pytest -q -p no:cacheprovider tests
docker compose build waterfall-backend frontend
npx --yes fallow health --format json --quiet --explain --root /srv/waterfallhunter/frontend 2>/dev/null || true
```

Expected: backend tests and both production builds pass. Fallow reports no critical complexity finding in the dashboard render path.

- [ ] **Step 5: Perform read-only runtime verification after deployment authorization**

Run:

```bash
curl -fsS https://waterfall.booksreadlive.online/dashboard/api/health
curl -fsS https://waterfall.booksreadlive.online/dashboard/api/candidates | jq '.candidates | to_entries[] | select(.value.metrics.score_version == "score_v2") | .value.metrics | {score_version, score_components, quality_gates, derivatives}'
```

Expected: API health is healthy; each Score V2 candidate has finite component values summing to its score, full source provenance, and no raw exchange payload.

---

## Plan Self-Review

- The plan covers every approved Score V2 component, integrity gate, derivatives packet, historical calibration requirement, dashboard requirement, and observability requirement.
- No task introduces live execution, AI score authority, guessed chain mappings, stale substitutes, or a database lifecycle migration.
- The plan has an explicit failing test before every production behavior change and verifies the final integrated build and live read-only API contract.
- The lack of Git metadata is preserved as a constraint; no task initializes Git or claims a commit.
