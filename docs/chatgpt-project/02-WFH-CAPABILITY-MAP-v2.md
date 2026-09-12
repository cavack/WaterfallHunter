# WaterfallHunter Capability Map v2

Capabilities are discovered at run time. A connector or executable being present does not imply read/write authorization.

| Capability | Typical role | Authority rule |
|---|---|---|
| GitHub connector | repository SHA/PR/CI/reviews and repository writes | use connector authorization; never infer Production state |
| Remote Desktop Commander MCP | isolated worktrees, tests, host/runtime evidence | repository/host writes allowed only within task scope; Production mutation remains release-gated |
| Web research | current official docs and external research | read-only evidence; cannot override current repository/runtime facts |
| CodeRabbit / CodeQL / Sonar | independent/static review evidence | reviewer only; not completion or release authority |
| Docker | artifact/runtime verification | no Production mutation from Council manifest |
| Prometheus / Grafana / Alertmanager | metrics, presentation, alert state | reconcile presentation with primary metrics/runtime evidence |
| Playwright | browser/E2E verification | verification capability only |
| pytest | deterministic regression | verification capability only |
| Mermaid | diagrams | documentation artifact only |
| market-data connectors | research evidence | require instrument identity, timestamps, units, freshness, and reproducibility checks |

Expected states: `AVAILABLE`, `AUTHORIZED_READ`, `AUTHORIZED_WRITE`, `UNAVAILABLE`, `BLOCKED`.

No capability in this bundle receives live-order authority or independent Production mutation authority.
