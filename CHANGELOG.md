# Changelog

WaterfallHunter uses release-oriented entries. Git history remains the forensic source for older implementation detail.

## Unreleased

### Repository and operations
- Added repository governance, support, conduct, and structured issue-reporting contracts.
- Documented the separated GitHub Actions → Production SSH trust path and Production → GitHub authenticated HTTPS plus read-only SSH fallback.
- Clarified that runtime recovery is intentionally layered across a systemd oneshot boot assertion, Docker `restart: unless-stopped`, and the bounded one-minute health-recovery timer.
- Enabled a real private vulnerability-reporting path to match `SECURITY.md`.


### Added
- Canonical Decision Terminal with one actionable `ENTRY_READY` state.
- Cascade Intelligence evidence inside the canonical decision engine.
- Durable canonical decision events and Telegram delivery path.
- Canonical repository/host handoff, operations, backup, and recovery documentation.

### Changed
- Product terminology standardized to `SIGNAL_ONLY` / `NO ORDER EXECUTION`.
- Research and validation surfaces are secondary to the Decision Terminal.
- Restored documented Anti-Chase ordering: extension can convert otherwise `FORMING`, `ENTRY_READY`, or `ACTIVE` evidence to `LATE`, while sub-`FORMING` or stale evidence is not mislabelled `LATE`; genuine lifecycle `EXHAUSTED` remains terminal `LATE` even when other blockers apply; `late_origin` now preserves terminal cause independently from current `lifecycle_state`; thresholds remain `78` / `55` / `1.2 ATR`.

### Safety
- `LIVE_TRADING_ENABLED=false` remains mandatory.
- No order placement or cancellation path is enabled.
