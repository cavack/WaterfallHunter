# WaterfallHunter — Canonical Project Source

## Canonical repository

The official and only authoritative repository for WaterfallHunter is:

`https://github.com/cavack/WFH-ORG`

This repository is the canonical source of truth for the project. Old repositories, snapshots, ZIP archives, exported copies, or historical working directories must not be treated as the primary project source.

## Current project status

- The project is under active development and is not considered feature-complete.
- The operational version is running on an Ubuntu server.
- The canonical branch is `main`.
- Audits, code changes, feature development, debugging, tests, CI/CD, Docker, deployment, and runtime investigations must start from the current `main` branch of `cavack/WFH-ORG`.
- Historical repositories such as `cavack/WaterfallHunter`, old snapshots, and ZIP exports must not be used as the implementation baseline.
- Production or server changes must not be based only on historical chat context or stale code copies. The current repository state has priority.

## Project structure reference

The current repository is organized approximately as follows:

- `backend/src/waterfallhunter/core/`
  - Core signal logic, scoring, state lifecycle, derivatives, microstructure, anti-chase logic, ranking, execution suitability, evidence, and outcome tracking.
- `backend/src/waterfallhunter/discovery/`
  - Market discovery and discovery-provider integrations.
- `backend/src/waterfallhunter/routes_*.py`
  - API endpoints for feature replay, historical outcomes, execution suitability, and production evidence.
- `backend/tests/`
  - Backend, regression, runtime-safety, and evidence tests.
- `frontend/`
  - Next.js dashboard and signal/evidence/ranking visualizations.
- `scripts/`
  - Backtesting, calibration, and slippage-profile utilities.
- `research/`
  - Research notes and research universes.
- `deploy/`
  - Prometheus, Grafana, and Alertmanager configuration.
- `watchdog/`
  - Runtime/health watchdog.
- `docker-compose.yml`
  - Primary runtime composition.
- `docker-compose.shadow.yml`
  - Shadow/simulated validation runtime overrides.

This structure may evolve as development continues. Before any audit or implementation change, inspect the current `main` branch rather than relying on this document alone.

## Conflict rule

If historical ChatGPT context, server copies, local files, old repositories, or prior documentation conflict with the current contents of `cavack/WFH-ORG`, the current repository state takes precedence unless an explicit migration or rollback decision says otherwise.

**Canonical Source of Truth: `cavack/WFH-ORG`**
