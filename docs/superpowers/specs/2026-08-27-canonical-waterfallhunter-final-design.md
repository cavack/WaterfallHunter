# Canonical WaterfallHunter Final — Design

Date: 2026-08-27
Repository: `cavack/wfh`
Branch: `feat/canonical-entry-engine-final-20260827`
Baseline: `301e9549ec50aa8d5fdd128cc156f181004d30b9`

## Outcome

Deliver one canonical WaterfallHunter that can replace every server copy and be installed cleanly on a wiped host. The product remains SIGNAL_ONLY and never places exchange orders.

The main dashboard is a decision terminal, not a research lab. It exposes one actionable decision model, persistent decision history, exact signal details, and a bounded candidate list. Research/replay/backtest/engineering panels remain accessible separately.

## Fixed domain rules

- SHORT only.
- Linear USDT perpetual futures only.
- Existing catalogue eligibility and contract-identity rules remain canonical.
- Missing/stale data is unavailable, never silently bullish/bearish.
- Same economic contract must be used for cross-exchange evidence.
- PRE-TRIGGER/ARMED timing is preferred to chasing an already-extended move.
- Anti-chase is mandatory.
- `LIVE_TRADING_ENABLED=false` remains a hard startup invariant.
- Telegram is notification only; no exchange execution path is introduced.
## Canonical decision model

Every symbol receives exactly one decision packet:

`NO_TRADE | FORMING | ENTRY_READY | ACTIVE | LATE | INVALIDATED | EXPIRED`

Lifecycle remains evidence context (`WATCH/FUEL-RICH/PRE-TRIGGER/ARMED/TRIGGERED/...`) but is not itself the entry decision.

The decision engine uses hard invalidators plus weighted readiness. Hard invalidators are limited to objectively unsafe conditions: stale/missing mandatory market reference, invalid contract identity, unavailable critical execution data, explicit anti-chase hard block, contradictory fresh price/market identity, or invalidation after an emitted signal.

Evidence that is merely weak contributes less readiness instead of zeroing the whole model. This removes the systemic all-red/all-zero funnel behavior while preserving fail-closed safety.

Only one official score is user-facing: `entry_readiness` from 0–100. Watch Score, ScoreV2 and diagnostic component scores may remain internally for validation but are not presented as competing user decisions.

Initial decision bands are versioned policy, not hidden constants: ENTRY_READY >= 78 with mandatory timing/direction/execution checks; FORMING >= 55; otherwise NO_TRADE. Anti-chase can convert ENTRY_READY/FORMING to LATE.
## Cascade Intelligence v1 inside the model

Cascade Intelligence is integrated from this release, not postponed as a separate product. It consumes free/public exchange-native data where available and normalizes it into one canonical packet.

Core evidence families:
- liquidation flow: long/short notional, count, velocity, acceleration, burst ratio;
- trade flow: aggressive buy/sell notional, delta, rolling CVD, sell dominance;
- derivatives: OI change/acceleration, funding, top-trader positioning;
- order book: spread, imbalance, bid depletion, refill, bounded sell-impact simulation;
- cross-exchange agreement and freshness.

No fake latent heatmap is allowed. Observed liquidation density is labelled observed. Estimated future liquidation zones must be explicitly marked estimated and cannot be treated as direct venue facts.

Cascade evidence contributes to readiness only when its packet is fresh enough for the configured window. A missing optional provider lowers coverage but does not invent a bearish/neutral value.

## Signal quantity versus quality

The engine must not optimize for zero signals and must not flood the user. The dashboard exposes at most 3 ENTRY_READY signals and at most 6 nearest FORMING setups. Candidate ranking is by canonical entry readiness plus evidence freshness; there is no second public 0–100 ranking score.

The decision policy records the reasons that added readiness and the reasons that prevented promotion. This makes quantity tunable through a single versioned policy instead of accumulating hard gates.

## Persistent signal semantics

ENTRY_READY is an event. Once emitted, it never silently disappears. Every later transition is explicit and timestamped: ACTIVE, LATE, INVALIDATED, or EXPIRED. Trigger/lifecycle state may change independently, but the decision event history remains immutable/auditable.
## Telegram

Telegram delivery is part of the final release. Credentials must be validated at startup without logging secrets. STRICT/ENTRY_READY events are delivered through the durable outbox worker, with retry/dead-letter health exposed. The legacy direct-send path may remain for interactive commands, but signal delivery must be durable and testable.

The signal message includes: symbol, decision, readiness, lifecycle, entry zone, stop, TP1/TP2/TP3 where available, leverage, evidence freshness/coverage, concise reasons, OI/funding/CVD/liquidation/liquidity summaries, anti-chase, and AI advisory when available.

## AI advisory

AI is advisory only and cannot create ENTRY_READY. When configured it receives the canonical evidence packet rather than only price/top-of-book. Failure produces `UNAVAILABLE`, not a block. The dashboard states why AI is unavailable.

## Dashboard information architecture

Main decision page:
1. ENTRY READY (max 3)
2. FORMING (max 6)
3. Recently changed decisions
4. Compact all-candidates table with search/filter/pagination

Each signal card shows exact trade-plan fields and the evidence that caused/blocked the decision. No competing Watch Score/Ranking Score is shown.

Research/validation page contains recorder health, feature replay, lifecycle shadow, backtest lab, outcome evidence, historical validation and engineering diagnostics. These panels are not mixed into the entry workflow.

## Automatic validation

The same canonical decision engine must be callable from recorded/historical evidence. A scheduled validation worker produces rolling model metrics and keeps manual Backtest Lab as an advanced tool only. A feature cannot silently bypass the canonical decision contract.
## Clean deployment

The repository owns one clean-install path for Ubuntu: preflight, optional backup/export, destructive uninstall of legacy WaterfallHunter containers/releases only after explicit operator command, fresh checkout/release install, environment validation, schema migration, image build, startup, health verification, Telegram verification, and rollback metadata.

No deployment script may delete unrelated host data. Old WaterfallHunter release/worktree directories are reported separately from the canonical runtime path.

Required runtime checks before declaring ready:
- backend `/readyz` and `/api/health` healthy;
- frontend health and dashboard loads;
- notification configuration validated and durable worker enabled;
- database schema current and writable;
- hunter loop progressing;
- no stale/systemic-zero critical evidence alarm;
- `LIVE_TRADING_ENABLED=false`.

## Acceptance criteria

- Baseline tests remain green; new behavior is test-first.
- An emitted ENTRY_READY never disappears without an explicit later decision event.
- Main dashboard shows only one readiness score per symbol.
- At most 3 ENTRY_READY and 6 FORMING cards render; all candidates live in a compact table.
- Signal cards show entry/SL/TP/leverage plus decisive evidence and block reasons.
- Percentage/unit fields render with correct units.
- Systemic-zero detection covers critical breakdown/stage gates.
- Telegram durable delivery is enabled when valid credentials exist and exposes clear health when not.
- AI advisory uses canonical evidence and degrades to UNAVAILABLE.
- Cascade evidence participates in readiness with explicit coverage/freshness semantics.
- Clean install can build and boot a fresh host from repository + `.env` + persistent data backup.
- Full backend tests, frontend typecheck/build and docker-compose configuration pass before handoff.