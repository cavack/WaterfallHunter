#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo "ERROR: clean-install validation requires a clean worktree." >&2
  exit 2
fi

for tool in git docker tar mktemp; do
  command -v "$tool" >/dev/null 2>&1 || { echo "ERROR: missing required tool: $tool" >&2; exit 2; }
done

docker compose version >/dev/null
SHA="$(git rev-parse HEAD)"
SHORT_SHA="${SHA:0:12}"
BUILD_DATE="$(git show -s --format=%cI HEAD)"
VERSION="$(git rev-parse --abbrev-ref HEAD)"
TMP="$(mktemp -d -t wfh-clean-install-XXXXXX)"
BACKEND_IMAGE="wfh-clean-backend:${SHORT_SHA}"
FRONTEND_IMAGE="wfh-clean-frontend:${SHORT_SHA}"
WATCHDOG_IMAGE="wfh-clean-watchdog:${SHORT_SHA}"

cleanup() {
  rm -rf "$TMP"
  docker image rm -f "$BACKEND_IMAGE" "$FRONTEND_IMAGE" "$WATCHDOG_IMAGE" >/dev/null 2>&1 || true
}
trap cleanup EXIT

git archive --format=tar HEAD | tar -xf - -C "$TMP"
git ls-files > "$TMP/.wfh-source-manifest"
# mktemp creates the checkout root as 0700. The backend image runs as the
# non-root waterfall user, so grant traversal/read access to this disposable
# read-only bind mount without changing artifact runtime privileges.
chmod 0755 "$TMP"
cp "$TMP/.env.example" "$TMP/.env"
(cd "$TMP" && docker compose config --quiet)

common_args=(--build-arg "VCS_REF=$SHA" --build-arg "BUILD_DATE=$BUILD_DATE" --build-arg "VERSION=$VERSION")
docker build "${common_args[@]}" -t "$BACKEND_IMAGE" "$TMP/backend"
docker build "${common_args[@]}" -t "$FRONTEND_IMAGE" "$TMP/frontend"
docker build "${common_args[@]}" -t "$WATCHDOG_IMAGE" "$TMP/watchdog"

for image in "$BACKEND_IMAGE" "$FRONTEND_IMAGE" "$WATCHDOG_IMAGE"; do
  [[ "$(docker image inspect "$image" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')" == "$SHA" ]]
done
[[ "$(docker run --rm "$BACKEND_IMAGE" python -c 'from waterfallhunter.config import settings; print(settings.source_revision)')" == "$SHA" ]]

docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=512m \
  -e LIVE_TRADING_ENABLED=false -e REGISTRY_DB_PATH=/tmp/test-registry.db \
  -e PYTHONPATH=/app/src:/project -v "$TMP:/project:ro" -w /project \
  "$BACKEND_IMAGE" pytest -q backend/tests

docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
  -e LIVE_TRADING_ENABLED=false -e PYTHONPATH=/app/src \
  "$BACKEND_IMAGE" python -m waterfallhunter.migrate_database \
  --db-path /tmp/fresh-install.db --apply --source-revision "$SHA"

echo "CLEAN_INSTALL_VALIDATION=PASS"
echo "SOURCE_REVISION=$SHA"
