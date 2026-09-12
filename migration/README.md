# WFH-ORG Migration Workspace

This directory is the provenance control plane for the clean migration from `cavack/wfh` and `cavack/wfh-dr` into `cavack/WFH-ORG`. Legacy Git ancestry is not imported.

## Frozen source inputs

- Primary source: `cavack/wfh@3e88df0014c30a8aae656a78cc949689174a631f` — 519 tracked files.
- DR source: `cavack/wfh-dr@add3f01cf3b9f3e55d735294dae99d5a5792b5c2` — 3 tracked files.
- Fix evidence: PR #127 `9b97820b43503cd0ef1d07950cbbad2ea7a79b43`; PR #128 `6db3472980c9d16fe090317324b94f743edaa955`; PR #93 `3b0a4f70620edb1159efb44c1c8aaeeb05f6a4af` (closed/unmerged evidence only).

## Manifest v2 contract

`source-manifest.json` has two complementary inventories:

1. `files` — exactly one disposition record for every frozen legacy tracked file: `KEEP`, `FIX_FIRST`, `SUPERSEDED`, `DROP`, or `QUARANTINE`.
2. `target_files` — exactly one record for every final tracked WFH-ORG path, including Git mode, final blob SHA, accepted disposition, origin class, rationale, and verification evidence.

Strict verification compares `target_files` against the Git index with `git ls-files -s`, so added, removed, mode-changed, or post-review modified files fail closed. `migration/source-manifest.json` is the only self-hash exception because a manifest cannot contain its own final blob hash; the enclosing Git commit provides its immutable identity.

```bash
python3 scripts/verify_migration_manifest.py migration/source-manifest.json --target-root .
```

The frozen legacy repositories remain evidence/rollback references only. DR implementation is co-located in WFH-ORG; independent backup storage remains outside the canonical Git repository.
