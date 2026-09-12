# WaterfallHunter Senior Agent Council v1 — Design Spec

## Goal

Build a deterministic, auditable multi-agent engineering/research pack that can coordinate ChatGPT/Codex-style agents, repository skills, MCP/connectors, host tooling, web research, tests, hooks and release gates to improve WaterfallHunter's ability to identify meme/perpetual markets approaching a short-side waterfall move.

The pack is not a promise of profit and must never manufacture a scientific champion from sparse evidence. Its job is to maximize defensible net edge while preserving the existing short-only, signal-only safety model.

## Baseline identity

- Canonical repository: `cavack/wfh`.
- Pack base SHA: `a3b45dc13158878fa3f64fddb0a12a7631b85a3c`.
- Production revision observed at design time: `4940daf58ef40831fccab98afa0e245602e50875`.
- Current policy remains `ENTRY_READY >= 78`, `FORMING >= 55`, Anti-Chase `1.2 ATR`.
- `LIVE_TRADING_ENABLED=false`; no order-placement capability is added.

## Existing scientific evidence

The 2026-08-31 rapid-delivery evidence is development-grade, not promotion evidence: 204,338 rows, 773 episodes, 281 symbols and only ~15.94 days of Tier-2 span. `OOS_VALIDATION.v1` was correctly blocked rather than consuming holdout to rank sparse variants. `BEST_DEVELOPMENT_CONFIG.v1` explicitly states `NO_SCIENTIFIC_CHAMPION` and production policy remains frozen.
## Council architecture

The pack uses a Council + adversarial review model rather than one mega-agent or uncoordinated parallel editors. Canonical WaterfallHunter skills remain the source of specialist rules; the Council adds role ownership, routing, artifact contracts and deterministic gates without duplicating the skill bodies.

| Role | Canonical skill ownership | Primary responsibility |
|---|---|---|
| Chief Orchestrator | `engineering-orchestrator` | current SHA, task graph, owners, evidence taxonomy |
| Architecture Adversary | `repository-architecture-auditor` | coupling/debt/current-state audit |
| Market Evidence Forensics | `market-data-evidence-quality` | identity, timestamps, data quality, microstructure |
| Strategy Owner | `strategy-score-lifecycle` | Score/lifecycle/gates/Anti-Chase/leverage semantics |
| Quant Validation Lead | `scientific-backtest-validation` | point-in-time replay, WFO/OOS, uncertainty, promotion |
| Runtime Lead | `runtime-reliability-performance` | latency, memory, concurrency, backpressure, soak |
| Backend/Data Lead | `backend-data-architecture` | services, workers, SQLite, rollups, migrations |
| Contract Guardian | `api-contract-schema-guardian` | API/SSE/OpenAPI/generated consumer contracts |
| Decision UX Lead | `frontend-dashboard-ux` | dashboard/browser/mobile evidence presentation |
| Security Lead | `security-supply-chain` | dependencies, secrets, containers, repository security |
| Incident/Telemetry Lead | `observability-incident-response` | logs, metrics, alerts, SLO and recurrence detection |
| Regression Lead | `verification-regression` | RED→GREEN and blast-radius verification |
| Release Certifier | `release-production-certification` | exact-head merge/deploy/restore/soak certification |

Two non-owning adversaries are defined as review personas: False-Positive Hunter attacks accepted signals and False-Negative Hunter attacks rejected near-misses. They may propose hypotheses but cannot edit canonical model policy without Strategy + Quant ownership.
## Tool and MCP layer

The pack records capability classes instead of hard-coding credentials or assuming every client exposes every connector.

- GitHub connector: exact SHA, branches, commits, PRs, issues, reviews and CI evidence.
- Remote Desktop Commander MCP: host filesystem, terminal, Docker/runtime evidence and local research artifacts.
- Web research: official exchange documentation and peer-reviewed/preprint market-microstructure research with source provenance.
- CodeRabbit: independent diff review when authenticated/available; it is a reviewer, not model-promotion authority.
- Mermaid/diagram tooling: architecture and experiment-flow documentation when useful.
- Repository-native stack: pytest, Playwright, Docker Compose, Prometheus/Grafana/Alertmanager, dependency audits, CodeQL/Sonar and existing recovery/deploy scripts.
- Optional public market/research connectors such as Binance/CCXT may supply research evidence only through the canonical data-quality contract; they cannot silently become production truth.

No secrets are stored in the Council manifest. Connector availability is detected at execution time and missing tools degrade explicitly to `UNAVAILABLE` rather than guessed evidence.

## Evidence taxonomy and artifact contract

Every Council finding is one of `VERIFIED_FACT`, `REPRODUCED_DEFECT`, `INFERENCE`, `DEBT`, or `PROPOSAL`. Every experiment records base SHA, dataset hash, configuration ID, point-in-time assumptions, split policy, costs, outputs and disposition.

The machine-readable Council manifest maps each role to its canonical skill, allowed write boundaries, required reviewers, required tools and blocking gates. The CLI validates that manifest and generates a deterministic task route; it does not call an LLM or change production by itself.
## Model-improvement campaign

The Council starts from the frozen production champion and treats all alternatives as challengers. It first reconstructs the selection history so previously viewed holdout evidence is never reused as an untouched final set.

1. **Data/provenance gate:** verify instrument identity, delisting/survivorship rules, timestamp causality, candle closure, derivatives units, provider freshness, outcome availability and cost model.
2. **Failure attribution:** decompose accepted losers, rejected winners, `LATE`, stale/unavailable evidence, threshold distance and gate co-binding before searching parameters.
3. **Feature challenger registry:** preregister hypotheses and exact evaluation rules before opening final evaluation data.
4. **Development search:** bounded coarse-to-fine search and ablation on development/walk-forward folds only.
5. **Adversarial review:** FP/FN reviewers try to falsify each candidate using causal examples and neighboring parameter perturbations.
6. **Scientific gate:** purged/embargoed walk-forward plus an untouched chronological final evaluation; CPCV/PBO/DSR may supplement, not replace, causal time ordering.
7. **Shadow gate:** champion/challenger comparison on live signal-only observations before any policy promotion.
8. **Promotion/release:** only evidence-backed changes enter the normal regression/review/release path.

Candidate feature families may include order-flow/taker-flow dynamics, futures/spot/mark/index basis divergence, OI×funding×crowding stress, order-book liquidity deterioration, inferred cascade pressure, relative weakness/market beta and regime-specific interactions. These are `PROPOSAL` until point-in-time availability and OOS value are established.
## External research constraints

Recent literature supports testing—not blindly promoting—several microstructure hypotheses. 2026 Journal of Financial Markets evidence reports out-of-sample predictive information in cryptocurrency order flow. 2026 cascade studies report futures/spot divergence, mark-price undershoot, spread blowout and substantial cross-coin heterogeneity in meme perpetual liquidation dynamics. A 2024 controlled backtest-overfitting study favors combinatorial purged validation for reducing selection bias. The Council therefore treats fixed one-size-fits-all thresholds as a challenger hypothesis, not as a presumed defect.

External publications never override current repository evidence. Every imported idea must state temporal availability, data source, expected mechanism, likely failure mode and an ablation that could reject it.

## Objective function

The optimization target is not raw signal count or win rate. Candidate configurations are compared on a constrained vector that includes:

- net expected R after realistic fees, slippage and funding where applicable;
- profit factor, drawdown, win rate, MAE, MFE and time-to-target;
- decisive sample count and entry-ready frequency/day;
- false-positive and false-negative rates under a frozen label contract;
- symbol, venue and regime concentration;
- evidence coverage/freshness and unavailable-rate;
- parameter stability/plateau width and bootstrap uncertainty;
- operational latency, RSS/memory behavior and provider failure rate.

A candidate that improves quantity by weakening safety-critical evidence or one that wins only at a sharp isolated parameter optimum is rejected.
## Repository package

The initial implementation creates a thin orchestration layer rather than duplicating the thirteen canonical skills:

- `.agents/wfh-council/manifest.json` — machine-readable roles, skills, ownership, tools and gates.
- `.agents/wfh-council/COUNCIL.md` — human/agent operating contract.
- `.agents/wfh-council/TOOLS.md` — connector/MCP/tool capability matrix and safe fallbacks.
- `.agents/wfh-council/RESEARCH.md` — curated research hypotheses and provenance requirements.
- `scripts/wfh_council.py` — deterministic `doctor`, `route`, `validate` and `snapshot` commands.
- `.githooks/pre-commit` and `.githooks/pre-push` — opt-in local gates that call existing repository validators and Council validation; they never deploy.
- `scripts/install_wfh_council_hooks.sh` — idempotent opt-in hook installer using `core.hooksPath`.
- focused repository tests validating manifest integrity, protected invariants, routing and hook behavior.

The Council CLI outputs JSON when requested so ChatGPT/Codex/other agents can consume the same routing and health facts without scraping prose.

## Hooks and CI policy

Pre-commit performs fast deterministic validation only. Pre-push may run a wider Council/skill/protected-invariant check but must remain bounded and must never mutate Production. Existing CI remains authoritative for repository verification; Council checks are integrated as an additional fail-fast validation rather than replacing backend/frontend/container/security jobs.

Any future GitHub Actions workflow created specifically for model experimentation must be non-deploying by default, artifact-hash its inputs/results and require an explicit separate release path for promotion.
## Protected invariants

The pack itself must fail validation if a route attempts to bypass these constraints:

- no live order placement and `LIVE_TRADING_ENABLED=false` remains mandatory;
- missing/stale evidence remains unavailable rather than bullish/bearish;
- ScoreV2, lifecycle, EntryDecision, Anti-Chase, eligibility and leverage policy changes require Strategy ownership;
- profitability/promotion claims require Quant validation;
- immutable signal provenance and persistence-before-notification remain preserved;
- frontend does not become a second source of scoring/ranking truth;
- code completion requires Regression ownership;
- only Release Certifier may declare deployment/production states.

## Completion states

The Council campaign has two honest terminal outcomes:

1. **PROMOTED_CHAMPION:** a challenger passes preregistered development, untouched OOS, robustness, cost, shadow and release gates and reaches the appropriate release-certified production state.
2. **NO_PROMOTION_EVIDENCE:** the pack itself is complete and verified, but model promotion is rejected/blocked because data, sample size, uncertainty, stability or operational evidence is insufficient.

`NO_PROMOTION_EVIDENCE` is a successful scientific outcome, not permission to weaken gates. A backtest-only configuration is never called profitable or best-in-class production behavior.

## Initial execution disposition

Because current development artifacts explicitly report `NO_SCIENTIFIC_CHAMPION`, the first run of Council v1 will focus on reproducible baseline/provenance audit, current-runtime evidence, historical data coverage, prior-trial contamination map and preregistration of new challenger families. It will not change `78/55/1.2 ATR` merely to increase signal count.