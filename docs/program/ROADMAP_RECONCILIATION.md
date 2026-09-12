# Roadmap Reconciliation Matrix

| Existing item | Current implementation/evidence | v6.1 requirement | Status | Dependency / next action |
| --- | --- | --- | --- | --- |
| Natural outcomes and execution evidence | Ledger, outcomes, replay, execution history exist; historical runtime evidence is experimental-heavy | Pure strict cohort before production calibration | PARTIAL | Unified metadata and cohort separation |
| L4 calibration / walk-forward / holdout | Tooling and historical plans exist | Strict-only OOS, no synthetic L2, explicit provenance | NEEDS_RECALIBRATION | Wave 5 after cohort purity |
| L5 historical outcome / net EV | Historical outcomes exist | Deterministic ISOLATED portfolio, costs, total ordering | PARTIAL | Wave 3 |
| Lifecycle persistence / stale safety | Persistent lifecycle and immutable ledger exist | Lifecycle V2 shadow with true pre-trigger ARMED | PARTIAL | Golden Corpus then Wave 4 |
| L6 final ranking | ScoreV2/final ranking exist | Remove false probability semantics; separate gates/score/probability | NEEDS_RECALIBRATION | Wave 1 |
| L7 dashboard | Operational Next.js dashboard exists | Decision Terminal, typed contracts, stale retention, Backtest Lab | PARTIAL | Wave 2 |
| Canary trading | `LIVE_TRADING_ENABLED=false` | Remains signal-only; separate future approval | BLOCKED | No action in this program |
| Runtime provenance | Deployment revision historically unverifiable | RuntimeFingerprint and end-to-end artifact chain | PARTIAL | Wave 0 |
| Golden regression behavior | Canonical-main deterministic fixture corpus is tracked; legacy runtime evidence is not source-accessible | Separate legacy and canonical corpora, canonical hashes | PARTIAL | Authorized legacy evidence capture remains a hard model-change gate |
| Telegram platform | Best-effort notifier exists | Persistent state machine, idempotency, retry/dead letter | PARTIAL | Wave 2 |
