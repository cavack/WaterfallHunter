#!/bin/sh
set -eu
# WFH_EXTERNAL_COMMAND_ALLOWLIST: git chmod

ROOT=$(git rev-parse --show-toplevel)
GIT_DIR=$(git rev-parse --git-dir)
COMMON_DIR=$(git rev-parse --git-common-dir)
cd "$ROOT"

for HOOK in .githooks/pre-commit .githooks/pre-push; do
    if [ ! -f "$HOOK" ]; then
        echo "missing Council hook: $HOOK" >&2
        exit 2
    fi
    chmod +x "$HOOK"
done

GIT_DIR_ABS=$(cd "$GIT_DIR" && pwd -P)
COMMON_DIR_ABS=$(cd "$COMMON_DIR" && pwd -P)
if [ "$GIT_DIR_ABS" != "$COMMON_DIR_ABS" ]; then
    git config --local extensions.worktreeConfig true
    git config --worktree core.hooksPath .githooks
else
    git config --local core.hooksPath .githooks
fi

printf '%s\n' "WaterfallHunter Council hooks enabled for $ROOT"
