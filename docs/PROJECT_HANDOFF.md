# WaterfallHunter Project Handoff

## What this is

WaterfallHunter is a `SIGNAL_ONLY` SHORT signal system for linear USDT perpetual futures, focused on pre-trigger/early long-liquidation cascade conditions. It does not place orders.

## Source of truth

- Repository: `https://github.com/cavack/WFH-ORG`
- Production branch: `main`
- Runtime checkout: `/srv/waterfallhunter/app`
- Public dashboard edge: nginx -> `127.0.0.1:3000`
- Product invariant: `LIVE_TRADING_ENABLED=false`

Historical chat, ZIP files, old server copies, stale branches, or old repositories are not implementation authority when they conflict with current `main`.

## Decision contract

Only canonical `ENTRY_READY` is a proactive entry signal. `FORMING` is not ready; `ACTIVE` is an already-emitted setup in progress; lifecycle `TRIGGERED` alone is never an entry instruction. The user-facing score is `entry_readiness`.

The protected `entry_policy_v1` calibration is `ENTRY_READY >= 78`, `FORMING >= 55`, and Anti-Chase at `1.2 ATR`. Anti-Chase converts only otherwise `FORMING`/`ENTRY_READY`/`ACTIVE` evidence to `LATE`; sub-`FORMING`, stale, or invalid evidence remains `NO_TRADE` or the applicable fail-closed state. Genuine lifecycle `EXHAUSTED` remains terminal `LATE` even when freshness or another blocker applies. `LATE` packets carry `late_origin` provenance while `lifecycle_state` continues to report the current evaluation; lifecycle/origin/blocker changes are material persistence events. See `docs/MODEL_CHANGELOG.md` before changing decision semantics.

## Adaptive evidence runtime

The hunter uses bounded, per-symbol single-flight deadline scheduling rather than waiting for an all-candidate batch barrier. Near-trigger states are evaluated on shorter deadlines, while fairness prevents WATCH starvation. PRE-TRIGGER/ARMED use direct hot WebSocket ticker/order-book/trade evidence. FUEL-RICH may use a bounded shared multi-symbol evidence pool only on exchanges with explicit native watch+unwatch support for all three evidence kinds; unsupported venues remain on REST fallback. Shared pools are capped at 64 symbols per exchange and feed the same causal TTL/history caches used by direct streams. They must use dedicated per-venue CCXT Pro clients; do not reuse the direct PRE-TRIGGER/ARMED client because shared dynamic unwatch can otherwise cancel a newly promoted direct subscription. Missing, stale, future-dated, incomplete, unsubscribed-symbol, or invalid evidence remains fail-closed. Validated closed OHLCV is cached only inside its current closed-candle bucket.

Runtime fan-out is observable through the existing WebSocket metrics plus `waterfall_websocket_shared_evidence_tasks` and `waterfall_websocket_shared_evidence_subscribers`. The adaptive runtime release target is global usable analysis p95 `<180s`; failing that target is a runtime defect, not justification to loosen strategy thresholds.

Leverage is advisory-only and never a strategy/signal gate. Canonical non-actionable decisions (`FORMING`, `LATE`, `NO_TRADE`, `INVALIDATED`, `EXPIRED`) report `NOT_RECOMMENDED` with no leverage value. `UNAVAILABLE` is reserved for an actionable decision whose required causal leverage evidence cannot be established. Numeric leverage for actionable evidence continues to use the existing adaptive policy and exchange/risk ceilings.

## Runtime services

Canonical application/observability services are backend, frontend, watchdog, Prometheus, Grafana, and Alertmanager, fronted by host nginx. Docker Compose manages containers; systemd asserts/recoveries the stack after boot.

## Data

The production SQLite database is persistent evidence and must be preserved. Never substitute a fresh DB without an explicit data-loss decision. Use certified backup + migration rehearsal before schema/cutover/destructive cleanup.

## Notifications and AI

Telegram signal delivery is durable and release-cutover scoped. AI/Gemini is optional advisory only and cannot alter decisions. No Ollama/local-model runtime is required unless current main explicitly reintroduces and validates it.

## Start here for any new task

1. `git fetch origin && git checkout main && git pull --ff-only` (or create an isolated worktree from current main).
2. Read `docs/ARCHITECTURE.md`, `docs/MODEL.md`, and the domain-specific doc for your change.
3. Run `make validate` before and after meaningful changes.
4. For releases run `./scripts/validate_clean_install.sh` on a clean exact SHA.
5. For production, follow `docs/DEPLOYMENT.md` and `docs/BACKUP_RESTORE.md`.

## Operations quick reference

```bash
systemctl status waterfallhunter.service
systemctl status waterfallhunter-healthcheck.timer
cd /srv/waterfallhunter/app && docker compose ps
curl -fsS http://127.0.0.1:3000/dashboard/ >/dev/null
```

Secrets are host/GitHub Environment owned and are never included in this handoff.
Runtime freshness follow-up (2026-09-02): Production evidence after shared-evidence membership-delta stabilization showed FUEL-RICH/PRE-TRIGGER freshness within target but global p95 still failed because absolute state priority starved cheap WATCH evaluations. The follow-up scheduler fix reserves at most one existing evaluation slot for due WATCH work when no WATCH is in flight; total concurrency remains 12 and strategy/calibration semantics are unchanged.

Release-image isolation follow-up (2026-09-02): a concurrent development build overwrote the shared `waterfallhunter-waterfall-backend` tag during an otherwise guarded Production deploy. The deployer now pins both target and previous runtime images to release-specific tags, uses a transient image override for cutover/rollback, and promotes a persistent `production-images.override.yml` only after exact runtime revision verification. `production_compose.sh` consumes that persistent override on systemd restarts.
Runtime memory follow-up (2026-09-02): after Wave B/C, Production hit the 2 GiB backend cgroup ceiling twice (12:00 and 12:46 UTC). The retained state was traced to long-lived CCXT Pro multi-symbol clients: Binance keys streams by the complete dynamic symbol set, so FUEL-RICH membership churn can accumulate internal subscription/cache state even after WaterfallHunter cache pruning. The transport now purges retired symbols from CCXT caches and recycles each shared evidence client after a bounded number of post-connect membership transitions; an empty pool also retires its client. This changes transport lifecycle only, not strategy scores, lifecycle states, thresholds, Anti-Chase, or SIGNAL_ONLY safety.
Dashboard/runtime follow-up (2026-09-03): the live Dashboard SSE and polling surface uses a bounded projection of the canonical candidate/EntryDecision state instead of repeatedly transporting full research diagnostics. Full raw candidate diagnostics remain available only through the explicit on-demand `/api/candidates/raw` snapshot. Connected SSE clients may trigger the expensive all-candidate dashboard aggregation at most once per 5 seconds while the transport heartbeat remains every 15 seconds; frontend `Live stream` requires a schema-valid application event rather than socket-open alone and falls back to polling when events stop. Microstructure acquisition also reuses independently valid fresh WebSocket order-book snapshot history and fresh trades before fetching only the missing half through REST. These changes are runtime/presentation only: the same causal freshness checks, source waterfall, ScoreV2, EntryDecision, lifecycle, Anti-Chase, eligibility, persistence-before-notification and SIGNAL_ONLY boundaries remain authoritative. Production acceptance still requires global usable analysis p95 `<180s` plus bounded backlog/memory/restart evidence; payload reduction or fewer REST calls alone is not release success.
