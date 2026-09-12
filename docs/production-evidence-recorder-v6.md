# Production Evidence Recorder v6

## Contract

Every evaluation is written as an immutable, compressed packet under schema
`production_decision_evidence_v6`. The packet contains:

- closed OHLCV for 5m, 15m, 1h, and 4h;
- the confirmation exchange's closed 15m OHLCV;
- explicit primary and confirmation exchange and mapped-symbol identities;
- three order-book snapshots, fresh trades, market limits and precision;
- raw funding, taker, top-trader, and open-interest provider responses;
- normalized derivatives, candle features, all strategy stages and quality gates;
- score version, score components, final status, and an explicit decision reason;
- the exact triggered-path position source when that branch is attempted;
- a SHA-256 fingerprint of the active Python source tree and a non-secret
  allowlist of all effective decision settings.
- a separate final production event after AI veto, leverage calculation,
  stale-trigger suppression, or signal-ledger persistence is resolved.

An evaluation cannot be marked production-evidence complete without raw
confirmation OHLCV. Derivatives fallback attempts are retained even when a
provider returned no raw packet, because the attempted venue and exact failure
reason are part of the decision path.

API keys, Telegram credentials, and provider secrets are never serialized. A
configured/not-configured boolean is retained where provider availability can
change the decision path.

## Integrity

Packets are content-hashed before compression. SQLite triggers reject updates
and deletes. Final production events bypass only the ordinary 15-minute
deduplication key; they remain immutable and never replace an earlier packet.
The v5 coverage columns separately report decision provenance, raw
derivatives, and full production-evidence completeness. Failed evaluations are
still recorded with their available sources and exact failure reason; they are
not falsely marked complete.

## Safety

The recorder is fail-open and observational. It cannot modify candidate state,
ranking, thresholds, notification delivery, eligibility, or order execution.
`LIVE_TRADING_ENABLED=false` remains mandatory.
