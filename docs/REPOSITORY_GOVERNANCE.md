# Repository Governance

This document records the repository controls that define the supported WaterfallHunter development and release path. The repository state and GitHub settings are authoritative when historical notes conflict with this document.

## Canonical source and ownership

- Canonical repository: `cavack/WFH-ORG`
- Default and production branch: `main`
- Code owner: `@cavack`
- Production checkout: `/srv/waterfallhunter/app`
- Product boundary: `SIGNAL_ONLY`; `LIVE_TRADING_ENABLED=false`
- Production secrets and runtime state are not stored in Git.

## Protected-main contract

`main` uses strict required status checks for `backend`, `frontend`, `dependency-audit`, `container-validation`, and `repository-hygiene`. Linear history is required, force pushes and branch deletion are blocked, administrator enforcement is enabled, and review conversations must be resolved.

The repository intentionally does not require a separate human approval count because the current ownership/workflow is single-maintainer and agent-assisted. That choice does not waive CI, review-thread resolution, exact-SHA verification, or production release gates.

## Pull requests and CI

Changes belong on short-lived branches or isolated worktrees. Pull requests must separate correctness fixes from calibration/research promotion and must record evidence appropriate to the affected surface. The PR template preserves the RED → fix → GREEN → regression → runtime/release evidence chain.

CI validates locked backend dependencies and tests, frontend contracts/typecheck/build/E2E, dependency audits, container artifacts and migrations, repository hygiene, and exact OCI revision provenance. Production credentials are not exposed to pull-request jobs.

## Production deployment trust path

A normal push to `main` verifies code but does not deploy it. Production deployment requires an explicit `workflow_dispatch` with `deploy_production=true` after the release/recovery gates are satisfied.

GitHub → Production uses a dedicated SSH identity stored in the GitHub `production` Environment. The deployment workflow pins `known_hosts`, stages the exact CI-tested artifact bundle, verifies its digests and revision, then invokes the guarded host deploy script.

Production host → GitHub uses authenticated HTTPS/`gh` for normal repository write operations. A separate repository-scoped, read-only SSH deploy key is configured as a fallback fetch path. The fallback cannot push and is not used by the GitHub Actions production deploy path.

## Runtime recovery ownership

`waterfallhunter.service` is intentionally a systemd `Type=oneshot` unit with `RemainAfterExit=yes`; it asserts the canonical Compose stack after boot. Application containers use `restart: unless-stopped`. `waterfallhunter-healthcheck.timer` runs a bounded health/recovery check every minute. Therefore adding `Restart=always` to the oneshot unit is neither required nor the intended recovery mechanism.

## Security controls

GitHub secret scanning, push protection, Dependabot security updates, branch protection, CODEOWNERS, dependency audits, repository hygiene checks, and Private Vulnerability Reporting are enabled or represented in the repository contract. Security findings must use the private reporting route in `SECURITY.md`.

## Community and support files

The repository maintains `README.md`, `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SUPPORT.md`, CODEOWNERS, Dependabot configuration, pull-request templates, and structured issue forms. These files describe the actual supported workflow; they are not substitutes for runtime or scientific evidence.

## Releases and version claims

Git history and exact deployment certificates are the forensic source of release provenance. Do not invent semantic versions, profitability claims, or a GitHub Release merely to satisfy a repository checklist. Create a release/tag only when the project has an explicit release identity and corresponding exact-SHA evidence.
