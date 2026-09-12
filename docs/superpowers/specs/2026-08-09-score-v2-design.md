# Score V2 Design

## Objective

Replace the current mixed live score with a versioned, deterministic 0–100 short-setup score. The score must use only fresh, source-attributed USDT-settled perpetual data. It must not use inter-exchange price differences as directional alpha, fabricate a missing value, or change the signal-only trading lock.

## Non-Negotiable Constraints

- `LIVE_TRADING_ENABLED=false` remains unchanged. No order-placement API is added.
- Eligible markets are active, linear, USDT-settled perpetual contracts only.
- A missing, invalid, stale, duplicate, gapped, open, unmapped, or source-incompatible data item fails closed.
- Gemini and Ollama remain advisory-only and receive no score weight or veto authority.
- LBank is a catalogue/reference source. Its price can validate source compatibility but cannot create a directional price-dislocation score.
- DEX, whale, and on-chain context remain unweighted until a verified token-contract and chain mapping exists for the exact perpetual contract.

## Data Contract

Every scored candidate records a `score_version`, a retrieval timestamp, and an attributed source for each component. The derivative packet is complete only when one compatible USDT-perpetual venue supplies all of:

- current funding rate;
- current open interest;
- fresh open-interest history;
- five-minute taker buy/sell ratio;
- five-minute top-trader long/short account ratio.

For Binance, use the canonical contract identifier from `market.id`, not a derived base-asset string. A source with an incomplete derivative packet cannot be substituted with zeroes, an old cached packet, or a value from a different asset.

## Score V2

| Component | Points | Evidence |
| --- | ---: | --- |
| Structural post-pump thesis | 35 | 4h hype context, structural damage, broken support, lower high, and setup priority for failed pullback |
| Entry timing | 20 | Closed 1h/15m/5m rejection sequence, reclaim or repump failure, RSI rollover, bearish close, and volume acceleration |
| Execution and microstructure | 20 | Fresh raw-trade sell flow, footprint imbalance, bid/ask depth, spread, executable slippage, churn, and spoofing state |
| Derivatives confirmation | 15 | Funding, OI change, taker flow, and top-trader positioning from one complete fresh packet |
| Cross-exchange confirmation | 5 | Price-compatible secondary venue plus completed-candle breakdown confirmation |
| Same-contract price location | 5 | Current contract price relative to that contract's own VWAP |
| **Total** | **100** | |

The old `price_dislocation` component is removed. Price compatibility remains a hard integrity gate, not a score component.

### Hard Gates

The following conditions are never compensated by points:

- all four candle timeframes are complete and valid;
- the channel stage chain has passed: hype, damage, setup, trigger;
- the primary orderbook and raw trades are fresh and meet exchange filters;
- spoofing is absent, executable depth is sufficient, and spread/slippage are within the current hard limits;
- the secondary exchange confirms the completed-candle breakdown;
- the derivative packet is complete and fresh for a Score V2 candidate.

A candidate failing a data gate is reported as `DATA_UNAVAILABLE` or `ANALYSIS_PENDING`, with its source failure reason. It is not assigned a partial score.

### Component Semantics

Structural and timing points are awarded only from closed candles. Failed pullback is the preferred setup. A continuing breakdown can qualify only when the higher-timeframe damage remains intact.

Execution points use contract-size-normalized notional values. Spoofing remains a hard rejection. Spread and slippage score execution quality but do not turn an unexecutable orderbook into a valid signal.

Derivatives points describe long-crowding and unwind evidence rather than an unconditional direction. Positive funding, long-heavy top-trader accounts, seller-dominant taker flow, and falling OI after structural failure are evaluated together. A negative funding rate is not a short confirmation and is treated as potential short-crowding risk.

## Historical Parity and Calibration

The backtest must calculate the same candle and derivative features from real Binance USD-M public history. It must use chronological purged train, validation, and holdout windows. Historical level-2 orderbook and raw-trade data are not fabricated; consequently, a backtest does not claim complete live-pipeline equivalence until an equivalent historical execution dataset exists.

Weight and threshold selection uses validation only. The holdout window remains untouched until selection is complete. The selection objective is positive cost-adjusted expectancy with stable results across symbols and time periods, not maximum win rate alone.

A Score V2 configuration is ineligible for live state-transition promotion unless it has:

- at least 50 settled validation outcomes;
- at least 30 settled holdout outcomes;
- positive validation and holdout realized expectancy after configured costs;
- net reward of at least 1R;
- no material validation-to-holdout degradation;
- explicit reporting of signal density, timeout rate, and rejected/missing-source symbols.

The system may display calibrated scores before promotion, but the current state thresholds are not replaced until these conditions are met.

## Versioning and Observability

Metrics expose `score_version`, the full component breakdown, derivative packet provenance, source failures, and gate results. Dashboard and Telegram output show the version and never present an unavailable component as zero. Prometheus counters track complete and incomplete derivative packets by source and reason.

## Out of Scope

- Unverified whale labels, chain-address guesses, opaque third-party signals, and stock-market datasets.
- AI-generated score values or AI vetoes.
- Live execution, leverage changes, security changes, database lifecycle migrations, or changes to Gemini/Ollama routing.
