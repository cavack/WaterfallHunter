# Canonical production cutover reconciliation

## Verified starting point
SQLite PR #15 merged as 75ee936c0998ea4dec43632e62df046032e7124a.
Exact-main CI 34221028689 passed all five required jobs.
Artifact 10053871027 has GitHub digest
sha256:d2082c55ac9aa53f06f4a6519540acb247444f5a1453068baa7eb8b10934c984.

Production remains on legacy revision 2828a59df4722fd8e34bc901261beeac1c631014.
The canonical dashboard is https://waterfall.booksreadlive.online/dashboard.
Deployment and Production certification have not occurred.

## Preserved PR #13
Source head: 47f55a97c674453f8e9b80c6a5245011393807d8.
Preservation directory:
/srv/waterfallhunter/runtime/repo-scope-reconcile/20260908T082749Z/pr13-preservation.
SHA256SUMS digest:
d31ca7c692955a7758ca9a1d2b3222d9dfc1c3fb35738151df2cd43abeed85c6.
All packet hashes were reverified. The unstaged patch and untracked inventory
are empty. The original dirty worktree remains preserved.

All twelve combined committed/staged paths were NEEDS_REAPPLICATION and
applied cleanly onto the post-SQLite main. The current CI dependency pins remain
from canonical main. The staged security corrections retain dispatch-specific
runner labels, exclusive provisioning lock, constrained runner privileges,
non-root reads of runner-controlled files, recovery report hash validation,
disabled runner self-update and idle runner retirement that preserves busy jobs.

## Canonical recovery
Application: cavack/WFH-ORG.
DR: cavack/WFH-ORG-dr (private, immutable releases enabled).
The reviewed restore script blob bb3aa003ef2a19dd4018661053669df4c677fa52 is
identical to the established legacy harness. Canonical harness revision
66d966ffe2053da2665612938b17c78f8ba05ba9 also pins crypto dependencies.
The new canonical restore-verification case reproduced
INDEPENDENT_RESTORE_WORKFLOW_IDENTITY_NOT_TRUSTED before the trust-map update.
Legacy verification remains readable for historical recovery evidence.
New backup publication and release recovery evaluation require canonical repos.

## Remaining release gates
Exact cutover head review and CI, calculated host capacity, canonical encrypted
backup and independent restore, exact-main recovery READY, official deployment,
active 20-30 minute runtime/dashboard soak, canonical Production certificate and
postdeployment DR certification remain required.
Legacy repositories must remain unarchived until both certifications pass.
LIVE_TRADING_ENABLED=false and telegram_signal_delivery_enabled=false remain
mandatory. No real orders are authorized.
