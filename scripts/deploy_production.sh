#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_REPOSITORY="cavack/WFH-ORG"

WFH_DEPLOY_ROOT="${WFH_DEPLOY_ROOT:-/srv/waterfallhunter/app}"
WFH_HOST_ROOT="${WFH_HOST_ROOT:-/srv/waterfallhunter}"
WFH_DEPLOY_SHA="${WFH_DEPLOY_SHA:-}"
ENV_FILE="${WFH_ENV_FILE:-/etc/waterfallhunter/waterfallhunter.env}"
DEPLOY_STATE_DIR="${WFH_HOST_ROOT}/runtime"
BACKUP_DIR="${WFH_HOST_ROOT}/backups"
STATE_DIR="${DEPLOY_STATE_DIR}"
LOCK_FILE="${WFH_DEPLOY_LOCK_FILE:-${STATE_DIR}/deploy.lock}"
WFH_DEPLOY_BACKUP_RETENTION_COUNT="${WFH_DEPLOY_BACKUP_RETENTION_COUNT:-2}"
WFH_TESTED_IMAGE_BUNDLE="${WFH_TESTED_IMAGE_BUNDLE:-}"
WFH_TESTED_IMAGE_BUNDLE_SHA256="${WFH_TESTED_IMAGE_BUNDLE_SHA256:-}"
WFH_TESTED_BACKEND_IMAGE_DIGEST="${WFH_TESTED_BACKEND_IMAGE_DIGEST:-}"
WFH_TESTED_FRONTEND_IMAGE_DIGEST="${WFH_TESTED_FRONTEND_IMAGE_DIGEST:-}"
WFH_TESTED_WATCHDOG_IMAGE_DIGEST="${WFH_TESTED_WATCHDOG_IMAGE_DIGEST:-}"
PRODUCTION_COMPOSE_OVERRIDE="${STATE_DIR}/production-volumes.override.yml"
PRODUCTION_IMAGE_OVERRIDE="${STATE_DIR}/production-images.override.yml"
TARGET_IMAGE_OVERRIDE="${STATE_DIR}/target-images.${WFH_DEPLOY_SHA}.override.yml"
ROLLBACK_IMAGE_OVERRIDE="${STATE_DIR}/rollback-images.${WFH_DEPLOY_SHA}.override.yml"
RELEASE_BACKEND_IMAGE="wfh-release-backend:${WFH_DEPLOY_SHA}"
RELEASE_FRONTEND_IMAGE="wfh-release-frontend:${WFH_DEPLOY_SHA}"
RELEASE_WATCHDOG_IMAGE="wfh-release-watchdog:${WFH_DEPLOY_SHA}"
ROLLBACK_BACKEND_IMAGE=""
ROLLBACK_FRONTEND_IMAGE=""
ROLLBACK_WATCHDOG_IMAGE=""
DB_PATH="/app/data/waterfall_registry.db"
DEPLOY_EPOCH="$(date -u +%s)"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PREVIOUS_SHA=""
DB_BACKUP=""
DB_BACKUP_SHA256=""
MIGRATION_MAY_HAVE_MUTATED=0
RUNTIME_REPLACED=0
ROLLBACK_ACTIVE=0
CLEANUP_ACTIVE=0
HOST_INTEGRATION_BACKUP_DIR=""
HOST_INTEGRATION_SNAPSHOTTED=0
HOST_INTEGRATION_MUTATED=0

log() {
  printf '[waterfallhunter-deploy] %s\n' "$*"
}

fail() {
  log "ERROR: $*"
  terminate_with_cleanup 1
}

require_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1 \
    || fail "required command missing: $command_name"
}

assert_signal_only_runtime_boundary() {
  grep -Eq '^LIVE_TRADING_ENABLED=(false|False|FALSE|0)$' "$ENV_FILE" \
    || fail "LIVE_TRADING_ENABLED must remain false for SIGNAL_ONLY Production"
}

assert_clean_deploy_worktree() {
  local dirty
  dirty="$(git status --porcelain=v1 --untracked-files=all -- .)"
  [[ -z "$dirty" ]]
}

assert_monitoring_bind_files_readable() {
  docker compose run --rm --no-deps --interactive=false -T \
    --entrypoint /bin/promtool prometheus \
    check config /etc/prometheus/prometheus.yml >/dev/null
}

configure_production_compose_topology() {
  if [[ -f "$PRODUCTION_COMPOSE_OVERRIDE" ]]; then
    export COMPOSE_FILE="${WFH_DEPLOY_ROOT}/docker-compose.yml:${PRODUCTION_COMPOSE_OVERRIDE}"
    log "using host-owned Production Compose topology override: ${PRODUCTION_COMPOSE_OVERRIDE}"
  else
    export COMPOSE_FILE="${WFH_DEPLOY_ROOT}/docker-compose.yml"
  fi
  if [[ -f "$PRODUCTION_IMAGE_OVERRIDE" ]]; then
    export COMPOSE_FILE="${COMPOSE_FILE}:${PRODUCTION_IMAGE_OVERRIDE}"
    log "using pinned Production image override: ${PRODUCTION_IMAGE_OVERRIDE}"
  fi
  export COMPOSE_PROJECT_NAME="waterfallhunter"
}

activate_image_override() {
  local override="$1"
  [[ -f "$override" && ! -L "$override" ]] || fail "release image override missing or symlinked: $override"
  configure_production_compose_topology
  export COMPOSE_FILE="${COMPOSE_FILE}:${override}"
}

activate_target_image_override() {
  activate_image_override "$TARGET_IMAGE_OVERRIDE"
}

activate_rollback_image_override() {
  activate_image_override "$ROLLBACK_IMAGE_OVERRIDE"
}

write_release_image_override() {
  local path="$1" backend_image="$2" frontend_image="$3" watchdog_image="$4" tmp
  tmp="${path}.tmp.$$"
  (
    umask 027
    cat > "$tmp" <<EOF
services:
  waterfall-backend:
    image: ${backend_image}
  frontend:
    image: ${frontend_image}
  watchdog:
    image: ${watchdog_image}
EOF
    chmod 0640 "$tmp"
    mv -- "$tmp" "$path"
  )
}

verify_image_revision() {
  local image_name="$1" expected_revision="$2" actual_revision
  actual_revision="$(docker image inspect "$image_name" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' 2>/dev/null || true)"
  [[ "$actual_revision" == "$expected_revision" ]]
}

# Verify the loaded tag carries the exact release revision.
verify_loaded_image_revision() {
  verify_image_revision "$1" "$WFH_DEPLOY_SHA"
}

pin_target_release_images() {
  docker tag waterfallhunter-waterfall-backend "$RELEASE_BACKEND_IMAGE"
  docker tag waterfallhunter-frontend "$RELEASE_FRONTEND_IMAGE"
  docker tag waterfallhunter-watchdog "$RELEASE_WATCHDOG_IMAGE"
  verify_image_revision "$RELEASE_BACKEND_IMAGE" "$WFH_DEPLOY_SHA" \
    || fail "pinned backend image revision does not match target SHA"
  verify_image_revision "$RELEASE_FRONTEND_IMAGE" "$WFH_DEPLOY_SHA" \
    || fail "pinned frontend image revision does not match target SHA"
  verify_image_revision "$RELEASE_WATCHDOG_IMAGE" "$WFH_DEPLOY_SHA" \
    || fail "pinned watchdog image revision does not match target SHA"
  write_release_image_override \
    "$TARGET_IMAGE_OVERRIDE" \
    "$RELEASE_BACKEND_IMAGE" "$RELEASE_FRONTEND_IMAGE" "$RELEASE_WATCHDOG_IMAGE"
}

pin_previous_running_images() {
  local container image_id actual_revision
  [[ -n "$PREVIOUS_SHA" ]] || fail "previous release revision unavailable for rollback pinning"
  ROLLBACK_BACKEND_IMAGE="wfh-release-backend:${PREVIOUS_SHA}"
  ROLLBACK_FRONTEND_IMAGE="wfh-release-frontend:${PREVIOUS_SHA}"
  ROLLBACK_WATCHDOG_IMAGE="wfh-release-watchdog:${PREVIOUS_SHA}"
  while IFS='|' read -r container release_image; do
    actual_revision="$(docker inspect "$container" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' 2>/dev/null || true)"
    [[ "$actual_revision" == "$PREVIOUS_SHA" ]] \
      || fail "running ${container} revision does not match certified previous SHA"
    image_id="$(docker inspect "$container" --format '{{.Image}}' 2>/dev/null || true)"
    [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] \
      || fail "running ${container} immutable image identity unavailable"
    docker image inspect "$image_id" >/dev/null 2>&1 \
      || fail "running ${container} image is unavailable for rollback pinning"
    docker tag "$image_id" "$release_image"
    verify_image_revision "$release_image" "$PREVIOUS_SHA" \
      || fail "rollback image revision mismatch for ${container}"
  done <<EOF
waterfall-backend|${ROLLBACK_BACKEND_IMAGE}
waterfall-frontend|${ROLLBACK_FRONTEND_IMAGE}
waterfall-watchdog|${ROLLBACK_WATCHDOG_IMAGE}
EOF
  write_release_image_override \
    "$ROLLBACK_IMAGE_OVERRIDE" \
    "$ROLLBACK_BACKEND_IMAGE" "$ROLLBACK_FRONTEND_IMAGE" "$ROLLBACK_WATCHDOG_IMAGE"
}

promote_target_image_override() {
  local tmp="${PRODUCTION_IMAGE_OVERRIDE}.tmp.$$"
  [[ "$HOST_INTEGRATION_SNAPSHOTTED" -eq 1 ]] \
    || fail "host integration snapshot missing before image override promotion"
  [[ -f "$TARGET_IMAGE_OVERRIDE" && ! -L "$TARGET_IMAGE_OVERRIDE" ]] \
    || fail "target image override unavailable for promotion"
  cp -- "$TARGET_IMAGE_OVERRIDE" "$tmp"
  chmod 0640 "$tmp"
  HOST_INTEGRATION_MUTATED=1
  mv -- "$tmp" "$PRODUCTION_IMAGE_OVERRIDE"
  configure_production_compose_topology
}

cleanup_incoming_artifacts() {
  local incoming="${STATE_DIR}/incoming" dir base
  [[ -d "$incoming" ]] || return 0
  if [[ -n "$WFH_TESTED_IMAGE_BUNDLE" && "$WFH_TESTED_IMAGE_BUNDLE" == "${incoming}/"* ]]; then
    rm -f -- "$WFH_TESTED_IMAGE_BUNDLE"
  fi
  while IFS= read -r dir; do
    base="$(basename "$dir")"
    [[ "$base" =~ ^[0-9a-f]{40}$ ]] || continue
    rm -f -- "$dir/wfh-tested-images.tar"
    rmdir -- "$dir" 2>/dev/null || true
  done < <(find "$incoming" -mindepth 1 -maxdepth 1 -type d -print)
}

# Verify the staged CI bundle before loading any release image.
load_tested_release_artifacts() {
  local expected_bundle actual_bundle_sha
  expected_bundle="${STATE_DIR}/incoming/${WFH_DEPLOY_SHA}/wfh-tested-images.tar"
  [[ "$WFH_TESTED_IMAGE_BUNDLE" == "$expected_bundle" ]] || fail "tested image bundle path is not canonical"
  [[ -f "$WFH_TESTED_IMAGE_BUNDLE" && ! -L "$WFH_TESTED_IMAGE_BUNDLE" ]] || fail "tested image bundle missing or symlinked"
  [[ "$WFH_TESTED_IMAGE_BUNDLE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "tested image bundle SHA256 invalid"
  for digest in "$WFH_TESTED_BACKEND_IMAGE_DIGEST" "$WFH_TESTED_FRONTEND_IMAGE_DIGEST" "$WFH_TESTED_WATCHDOG_IMAGE_DIGEST"; do
    [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "tested image digest invalid"
  done
  actual_bundle_sha="$(sha256sum "$WFH_TESTED_IMAGE_BUNDLE" | awk '{print $1}')"
  [[ "$actual_bundle_sha" == "$WFH_TESTED_IMAGE_BUNDLE_SHA256" ]] || fail "tested image bundle checksum mismatch"
  python3 "${WFH_DEPLOY_ROOT}/scripts/verify_ci_image_bundle.py" \
    --allowed-root "${STATE_DIR}/incoming/${WFH_DEPLOY_SHA}" \
    --bundle "$WFH_TESTED_IMAGE_BUNDLE" \
    --revision "$WFH_DEPLOY_SHA" \
    --image "waterfallhunter-waterfall-backend=$WFH_TESTED_BACKEND_IMAGE_DIGEST" \
    --image "waterfallhunter-frontend=$WFH_TESTED_FRONTEND_IMAGE_DIGEST" \
    --image "waterfallhunter-watchdog=$WFH_TESTED_WATCHDOG_IMAGE_DIGEST" \
    || fail "CI-tested image bundle portable digest/revision verification failed"
  docker load -i "$WFH_TESTED_IMAGE_BUNDLE" >/dev/null || fail "unable to load CI-tested image bundle"
  verify_loaded_image_revision waterfallhunter-waterfall-backend \
    || fail "loaded backend image revision does not match target SHA"
  verify_loaded_image_revision waterfallhunter-frontend \
    || fail "loaded frontend image revision does not match target SHA"
  verify_loaded_image_revision waterfallhunter-watchdog \
    || fail "loaded watchdog image revision does not match target SHA"
  log "loaded exact CI-tested release images for ${WFH_DEPLOY_SHA}"
}

snapshot_host_integration_state() {
  local unit target state manifest
  [[ "$HOST_INTEGRATION_SNAPSHOTTED" -eq 0 ]] || return 0
  HOST_INTEGRATION_BACKUP_DIR="${STATE_DIR}/host-integration.${PREVIOUS_SHA:-unknown}.${DEPLOY_EPOCH}"
  install -d -m 0700 \
    "$HOST_INTEGRATION_BACKUP_DIR/systemd" \
    "$HOST_INTEGRATION_BACKUP_DIR/nginx" \
    "$HOST_INTEGRATION_BACKUP_DIR/runtime"
  manifest="$HOST_INTEGRATION_BACKUP_DIR/manifest"
  : > "$manifest"
  : > "$HOST_INTEGRATION_BACKUP_DIR/systemd-enabled"
  : > "$HOST_INTEGRATION_BACKUP_DIR/systemd-active"
  if [[ -e "$PRODUCTION_IMAGE_OVERRIDE" || -L "$PRODUCTION_IMAGE_OVERRIDE" ]]; then
    cp -a -- "$PRODUCTION_IMAGE_OVERRIDE" "$HOST_INTEGRATION_BACKUP_DIR/runtime/production-images.override.yml"
    printf 'runtime:%s=present\n' "$PRODUCTION_IMAGE_OVERRIDE" >> "$manifest"
  else
    printf 'runtime:%s=absent\n' "$PRODUCTION_IMAGE_OVERRIDE" >> "$manifest"
  fi

  for unit in \
    waterfallhunter.service \
    waterfallhunter-healthcheck.service \
    waterfallhunter-healthcheck.timer; do
    target="/etc/systemd/system/${unit}"
    if [[ -e "$target" || -L "$target" ]]; then
      cp -a -- "$target" "$HOST_INTEGRATION_BACKUP_DIR/systemd/${unit}"
      printf 'systemd:%s=present\n' "$unit" >> "$manifest"
    else
      printf 'systemd:%s=absent\n' "$unit" >> "$manifest"
    fi
    state="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
    printf '%s=%s\n' "$unit" "${state:-unknown}" \
      >> "$HOST_INTEGRATION_BACKUP_DIR/systemd-enabled"
    state="$(systemctl is-active "$unit" 2>/dev/null || true)"
    printf '%s=%s\n' "$unit" "${state:-unknown}" \
      >> "$HOST_INTEGRATION_BACKUP_DIR/systemd-active"
  done

  for target in \
    /etc/nginx/sites-available/waterfallhunter.conf \
    /etc/nginx/sites-enabled/waterfallhunter.conf; do
    if [[ -e "$target" || -L "$target" ]]; then
      cp -a -- "$target" "$HOST_INTEGRATION_BACKUP_DIR/nginx/$(basename "$(dirname "$target")")"
      printf 'nginx:%s=present\n' "$target" >> "$manifest"
    else
      printf 'nginx:%s=absent\n' "$target" >> "$manifest"
    fi
  done
  HOST_INTEGRATION_SNAPSHOTTED=1
}

restore_host_integration_state() {
  local unit target presence state status=0 manifest
  [[ "$HOST_INTEGRATION_SNAPSHOTTED" -eq 1 ]] || return 0
  manifest="$HOST_INTEGRATION_BACKUP_DIR/manifest"
  systemctl stop waterfallhunter-healthcheck.timer waterfallhunter.service >/dev/null 2>&1 || true

  for unit in \
    waterfallhunter.service \
    waterfallhunter-healthcheck.service \
    waterfallhunter-healthcheck.timer; do
    target="/etc/systemd/system/${unit}"
    presence="$(awk -F= -v key="systemd:${unit}" '$1 == key { print $2 }' "$manifest")"
    rm -f -- "$target" || status=1
    if [[ "$presence" == "present" ]]; then
      cp -a -- "$HOST_INTEGRATION_BACKUP_DIR/systemd/${unit}" "$target" || status=1
    fi
  done
  systemctl daemon-reload || status=1
  presence="$(awk -F= -v key="runtime:${PRODUCTION_IMAGE_OVERRIDE}" '$1 == key { print $2 }' "$manifest")"
  rm -f -- "$PRODUCTION_IMAGE_OVERRIDE" || status=1
  if [[ "$presence" == "present" ]]; then
    cp -a -- "$HOST_INTEGRATION_BACKUP_DIR/runtime/production-images.override.yml" "$PRODUCTION_IMAGE_OVERRIDE" || status=1
  fi
  for unit in waterfallhunter.service waterfallhunter-healthcheck.timer; do
    state="$(awk -F= -v key="$unit" '$1 == key { print $2 }' "$HOST_INTEGRATION_BACKUP_DIR/systemd-enabled")"
    case "$state" in
      enabled|enabled-runtime)
        systemctl enable "$unit" >/dev/null 2>&1 || status=1
        ;;
      *)
        systemctl disable "$unit" >/dev/null 2>&1 || true
        ;;
    esac
  done

  for unit in waterfallhunter.service waterfallhunter-healthcheck.timer; do
    state="$(awk -F= -v key="$unit" '$1 == key { print $2 }' "$HOST_INTEGRATION_BACKUP_DIR/systemd-active")"
    case "$state" in
      active|activating)
        systemctl start "$unit" >/dev/null 2>&1 || status=1
        ;;
    esac
  done

  for target in \
    /etc/nginx/sites-available/waterfallhunter.conf \
    /etc/nginx/sites-enabled/waterfallhunter.conf; do
    presence="$(awk -F= -v key="nginx:${target}" '$1 == key { print $2 }' "$manifest")"
    rm -f -- "$target" || status=1
    if [[ "$presence" == "present" ]]; then
      cp -a -- "$HOST_INTEGRATION_BACKUP_DIR/nginx/$(basename "$(dirname "$target")")" "$target" || status=1
    fi
  done
  if command -v nginx >/dev/null 2>&1; then
    nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || status=1
  fi
  HOST_INTEGRATION_MUTATED=0
  return "$status"
}

install_systemd_units() {
  local source_dir="${WFH_DEPLOY_ROOT}/deploy/systemd"
  [[ "$HOST_INTEGRATION_SNAPSHOTTED" -eq 1 ]] || fail "host integration snapshot missing before systemd install"
  [[ -f "$source_dir/waterfallhunter.service" ]] || fail "canonical systemd service missing"
  [[ -f "$source_dir/waterfallhunter-healthcheck.service" ]] || fail "canonical healthcheck service missing"
  [[ -f "$source_dir/waterfallhunter-healthcheck.timer" ]] || fail "canonical healthcheck timer missing"
  HOST_INTEGRATION_MUTATED=1
  install -m 0644 "$source_dir/waterfallhunter.service" /etc/systemd/system/waterfallhunter.service
  install -m 0644 "$source_dir/waterfallhunter-healthcheck.service" /etc/systemd/system/waterfallhunter-healthcheck.service
  install -m 0644 "$source_dir/waterfallhunter-healthcheck.timer" /etc/systemd/system/waterfallhunter-healthcheck.timer
  systemctl daemon-reload
  systemctl enable waterfallhunter.service waterfallhunter-healthcheck.timer >/dev/null
}

activate_systemd_units() {
  systemctl start waterfallhunter.service
  systemctl start waterfallhunter-healthcheck.timer
  systemctl is-active --quiet waterfallhunter.service
  systemctl is-active --quiet waterfallhunter-healthcheck.timer
}

install_nginx_site() {
  local source="${WFH_DEPLOY_ROOT}/deploy/nginx/waterfallhunter.conf"
  local available="/etc/nginx/sites-available/waterfallhunter.conf"
  local enabled="/etc/nginx/sites-enabled/waterfallhunter.conf"
  [[ "$HOST_INTEGRATION_SNAPSHOTTED" -eq 1 ]] || fail "host integration snapshot missing before Nginx install"
  [[ -f "$source" ]] || fail "canonical Nginx site missing: deploy/nginx/waterfallhunter.conf"
  [[ -d /etc/nginx/sites-available && -d /etc/nginx/sites-enabled ]] \
    || fail "canonical Nginx site directories are unavailable"
  HOST_INTEGRATION_MUTATED=1
  install -m 0644 "$source" "$available"
  ln -sfn "$available" "$enabled"
  nginx -t >/dev/null
  systemctl reload nginx
}

verify_public_edge() {
  local public_url="${WFH_PUBLIC_EDGE_URL:-http://waterfall.booksreadlive.online/dashboard/}"
  curl --fail --silent --show-error --location --max-time 15 \
    --output /dev/null "$public_url"
}

remove_release_containers_before_compose_handoff() {
  local alertmanager_volume container service
  for container in \
    waterfall-backend \
    waterfall-frontend \
    waterfall-watchdog \
    waterfall-prometheus \
    waterfall-grafana; do
    if docker inspect "$container" >/dev/null 2>&1; then
      log "removing fixed-name container before Compose handoff: ${container}"
      docker rm -f "$container" >/dev/null || return 1
    fi
  done

  # Alertmanager historically had a project-scoped generated name rather than
  # a fixed container_name. Resolve its actual volume from the effective
  # Compose topology, including a host-owned external-volume override.
  alertmanager_volume="$(docker compose config --format json | python3 -c '
import json, re, sys
config = json.load(sys.stdin)
name = config.get("volumes", {}).get("alertmanager_data", {}).get("name", "")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
    raise SystemExit("resolved Alertmanager volume name is missing or unsafe")
print(name)
')" || return 1
  while IFS= read -r container; do
    [[ "$container" =~ ^[0-9a-f]{12,64}$ ]] || return 1
    service="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.service" }}' "$container" 2>/dev/null || true)"
    [[ "$service" == "alertmanager" ]] || continue
    log "removing volume-bound Alertmanager container before Compose handoff: ${container}"
    docker rm -f "$container" >/dev/null || return 1
  done < <(docker ps -aq --filter "volume=${alertmanager_volume}")
}

wait_for_backend_endpoint() {
  local endpoint="$1"
  local attempts="${2:-30}"
  local sleep_seconds="${3:-4}"
  local i
  for ((i = 1; i <= attempts; i++)); do
    if docker compose exec -T waterfall-backend /opt/venv/bin/python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000${endpoint}', timeout=3).read()" \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep "$sleep_seconds"
  done
  return 1
}

wait_for_container_healthy() {
  local container="$1"
  local attempts="${2:-30}"
  local sleep_seconds="${3:-3}"
  local state i
  for ((i = 1; i <= attempts; i++)); do
    state="$(docker inspect -f '{{if .State.Running}}{{if .State.Health}}{{.State.Health.Status}}{{else}}missing-healthcheck{{end}}{{else}}stopped{{end}}' "$container" 2>/dev/null || true)"
    if [[ "$state" == "healthy" ]]; then
      return 0
    fi
    sleep "$sleep_seconds"
  done
  return 1
}

wait_for_monitoring_containers_healthy() {
  local alertmanager_container
  wait_for_container_healthy waterfall-prometheus 30 3 || return 1
  wait_for_container_healthy waterfall-grafana 30 3 || return 1
  alertmanager_container="$(docker compose ps -q alertmanager)"
  [[ "$alertmanager_container" =~ ^[0-9a-f]{12,64}$ ]] || return 1
  wait_for_container_healthy "$alertmanager_container" 30 3
}

verify_running_revision() {
  local container expected actual
  expected="$1"
  for container in waterfall-backend waterfall-frontend waterfall-watchdog; do
    actual="$(docker inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$container" 2>/dev/null || true)"
    [[ "$actual" == "$expected" ]] || return 1
  done
}

verify_running_signal_only() {
  docker compose exec -T waterfall-backend /opt/venv/bin/python -c \
    'from waterfallhunter.config import settings; assert settings.live_trading_enabled is False' \
    >/dev/null 2>&1
}

build_revision() {
  local revision="$1"
  local build_date
  build_date="$(git show -s --format=%cI "$revision" 2>/dev/null || printf '%s' "$BUILD_DATE")"
  VCS_REF="$revision" BUILD_DATE="$build_date" VERSION="$revision" \
    docker compose build \
      --build-arg VCS_REF="$revision" \
      --build-arg BUILD_DATE="$build_date" \
      --build-arg VERSION="$revision" \
      waterfall-backend frontend watchdog
}

resolve_previous_revision() {
  local candidate certificate
  candidate="$(docker inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' waterfall-backend 2>/dev/null || true)"
  if [[ "$candidate" =~ ^[0-9a-f]{40}$ ]] && git cat-file -e "${candidate}^{commit}" 2>/dev/null; then
    printf '%s\n' "$candidate"
    return 0
  fi

  certificate="${STATE_DIR}/last-successful-deploy.txt"
  if [[ -f "$certificate" ]]; then
    candidate="$(awk -F= '$1 == "revision" {print $2; exit}' "$certificate")"
    if [[ "$candidate" =~ ^[0-9a-f]{40}$ ]] && git cat-file -e "${candidate}^{commit}" 2>/dev/null; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  candidate="$(git rev-parse HEAD 2>/dev/null || true)"
  [[ "$candidate" =~ ^[0-9a-f]{40}$ ]] || return 1
  git cat-file -e "${candidate}^{commit}" 2>/dev/null || return 1
  printf '%s\n' "$candidate"
}

backup_database() {
  # backup: this marker is intentionally kept stable for deployment-contract tests.
  local actual_staged_sha backend_gid backend_identity backend_uid backup_name backup_output staging_created=0 staging_dir
  backup_name="waterfall_registry.${WFH_DEPLOY_SHA}.${DEPLOY_EPOCH}.db"
  DB_BACKUP="${BACKUP_DIR}/${backup_name}"
  staging_dir="${BACKUP_DIR}/.staging-${WFH_DEPLOY_SHA}-${DEPLOY_EPOCH}"
  install -d -m 0750 "$BACKUP_DIR"
  [[ ! -e "$staging_dir" ]] || fail "database backup staging path already exists"

  backend_identity="$(docker compose run --rm --no-deps --interactive=false -T \
    waterfall-backend \
    /opt/venv/bin/python -c 'import os; print(os.getuid(), os.getgid())')" \
    || fail "unable to resolve backend service identity for database backup"
  read -r backend_uid backend_gid <<< "$backend_identity"
  [[ "$backend_uid" =~ ^[0-9]+$ && "$backend_gid" =~ ^[0-9]+$ ]] \
    || fail "backend service identity is invalid"
  install -d -m 0700 -o "$backend_uid" -g "$backend_gid" "$staging_dir"
  staging_created=1

  if ! backup_output="$(docker compose run --rm --no-deps --interactive=false -T \
    -v "${staging_dir}:/backup" \
    waterfall-backend \
    /opt/venv/bin/python -c \
    'import hashlib, pathlib, sqlite3, sys
src=pathlib.Path("/app/data/waterfall_registry.db")
dst=pathlib.Path("/backup") / sys.argv[1]
if not src.is_file(): raise SystemExit("source database missing")
with sqlite3.connect(src) as source, sqlite3.connect(dst) as target:
    source.backup(target)
with sqlite3.connect(dst) as check:
    row=check.execute("PRAGMA integrity_check").fetchone()
    if not row or str(row[0]).lower() != "ok": raise SystemExit("backup integrity check failed")
h=hashlib.sha256()
with dst.open("rb") as fh:
    for chunk in iter(lambda: fh.read(1024*1024), b""): h.update(chunk)
print(h.hexdigest())' \
    "$backup_name")"; then
    rm -f -- "${staging_dir}/${backup_name}" "$DB_BACKUP" "${DB_BACKUP}.sha256"
    rmdir -- "$staging_dir" 2>/dev/null || true
    fail "database backup snapshot failed"
  fi

  DB_BACKUP_SHA256="$(printf '%s\n' "$backup_output" | tail -n 1 | tr -d '[:space:]')"
  actual_staged_sha="$(sha256sum "${staging_dir}/${backup_name}" 2>/dev/null | awk '{print $1}')" || true
  if [[ "$staging_created" -ne 1 \
    || ! -f "${staging_dir}/${backup_name}" \
    || -L "${staging_dir}/${backup_name}" \
    || ! "$DB_BACKUP_SHA256" =~ ^[0-9a-f]{64}$ \
    || "$actual_staged_sha" != "$DB_BACKUP_SHA256" ]]; then
    rm -f -- "${staging_dir}/${backup_name}" "$DB_BACKUP" "${DB_BACKUP}.sha256"
    rmdir -- "$staging_dir" 2>/dev/null || true
    fail "database backup staging certification failed"
  fi

  if ! mv -- "${staging_dir}/${backup_name}" "$DB_BACKUP" \
    || ! chown 0:0 "$DB_BACKUP" \
    || ! chmod 0640 "$DB_BACKUP" \
    || ! rmdir -- "$staging_dir" \
    || ! printf '%s  %s\n' "$DB_BACKUP_SHA256" "$(basename "$DB_BACKUP")" > "${DB_BACKUP}.sha256"; then
    rm -f -- "${staging_dir}/${backup_name}" "$DB_BACKUP" "${DB_BACKUP}.sha256"
    rmdir -- "$staging_dir" 2>/dev/null || true
    fail "database backup promotion failed"
  fi

  [[ -s "$DB_BACKUP" ]] || fail "database backup missing after backup step"
  log "database backup certified: ${DB_BACKUP} sha256=${DB_BACKUP_SHA256}"
}

restore_database_backup() {
  local actual_sha backend_gid backend_identity backend_uid backup_gid backup_mode backup_name
  [[ -n "$DB_BACKUP" && -f "$DB_BACKUP" ]] || return 1
  [[ "$DB_BACKUP" == "${BACKUP_DIR}/"* ]] || return 1
  [[ "$DB_BACKUP_SHA256" =~ ^[0-9a-f]{64}$ ]] || return 1
  actual_sha="$(sha256sum "$DB_BACKUP" | awk '{print $1}')" || return 1
  [[ "$actual_sha" == "$DB_BACKUP_SHA256" ]] || return 1

  backend_identity="$(docker compose run --rm --no-deps --interactive=false -T \
    waterfall-backend \
    /opt/venv/bin/python -c 'import os; print(os.getuid(), os.getgid())')" \
    || return 1
  read -r backend_uid backend_gid <<< "$backend_identity"
  [[ "$backend_uid" =~ ^[0-9]+$ && "$backend_gid" =~ ^[0-9]+$ ]] || return 1

  backup_gid="$(stat -c '%g' "$DB_BACKUP")" || return 1
  backup_mode="$(stat -c '%a' "$DB_BACKUP")" || return 1
  [[ "$backup_gid" =~ ^[0-9]+$ && "$backup_mode" =~ ^[0-7]{3,4}$ ]] || return 1
  backup_name="$(basename "$DB_BACKUP")"

  (
    metadata_changed=0
    restore_backup_metadata() {
      if [[ "$metadata_changed" -eq 1 ]]; then
        chgrp "$backup_gid" "$DB_BACKUP" >/dev/null 2>&1 || true
        chmod "$backup_mode" "$DB_BACKUP" >/dev/null 2>&1 || true
      fi
    }
    trap restore_backup_metadata EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    chgrp "$backend_gid" "$DB_BACKUP"
    metadata_changed=1
    chmod 0640 "$DB_BACKUP"

    docker compose stop waterfall-backend frontend watchdog >/dev/null 2>&1 || exit 1
    docker compose run --rm --no-deps --interactive=false -T \
      --user "$backend_uid:$backend_gid" \
      -v "${DB_BACKUP}:/backup/${backup_name}:ro" \
      waterfall-backend \
      /opt/venv/bin/python -c \
      'import hashlib, pathlib, sqlite3, sys
src = pathlib.Path("/backup") / sys.argv[1]
expected = sys.argv[2]
dst = pathlib.Path("/app/data/waterfall_registry.db")
if not src.is_file(): raise SystemExit("rollback backup missing")
h = hashlib.sha256()
with src.open("rb") as fh:
    for chunk in iter(lambda: fh.read(1024 * 1024), b""): h.update(chunk)
if h.hexdigest() != expected: raise SystemExit("rollback backup checksum mismatch")
with sqlite3.connect(f"file:{src}?mode=ro&immutable=1", uri=True) as source:
    row = source.execute("PRAGMA integrity_check").fetchone()
    if not row or str(row[0]).lower() != "ok": raise SystemExit("rollback backup integrity failure")
    pathlib.Path(str(dst) + "-wal").unlink(missing_ok=True)
    pathlib.Path(str(dst) + "-shm").unlink(missing_ok=True)
    with sqlite3.connect(dst) as target:
        source.backup(target)
with sqlite3.connect(dst) as check:
    row = check.execute("PRAGMA integrity_check").fetchone()
    if not row or str(row[0]).lower() != "ok": raise SystemExit("restored database integrity failure")' \
      "$backup_name" "$DB_BACKUP_SHA256"
  ) || return 1

  [[ "$(stat -c '%g' "$DB_BACKUP")" == "$backup_gid" ]] || return 1
  [[ "$(stat -c '%a' "$DB_BACKUP")" == "$backup_mode" ]] || return 1
  log "database restored from certified pre-migration backup: ${DB_BACKUP}"
}

prune_database_backups() {
  local keep="$WFH_DEPLOY_BACKUP_RETENTION_COUNT"
  local certificate="${STATE_DIR}/last-successful-deploy.txt"
  local backup_root certified_backup="" canonical_certified_backup="" canonical_path
  local line path index=0
  [[ "$keep" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ -d "$BACKUP_DIR" ]] || return 0
  backup_root="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$BACKUP_DIR")" || return 1
  [[ -n "$backup_root" && "$backup_root" == /* ]] || return 1
  if [[ -f "$certificate" ]]; then
    certified_backup="$(awk -F= '$1 == "backup" {print substr($0, index($0, "=") + 1); exit}' "$certificate")" || return 1
    if [[ -n "$certified_backup" ]]; then
      canonical_certified_backup="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$certified_backup")" || return 1
      [[ "$canonical_certified_backup" == "${backup_root}/"* ]] || return 1
    fi
  fi
  while IFS= read -r line; do
    index=$((index + 1))
    (( index > keep )) || continue
    path="${line#* }"
    canonical_path="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$path")" || return 1
    [[ "$canonical_path" == "${backup_root}/"* ]] || return 1
    if [[ -n "$canonical_certified_backup" && "$canonical_path" == "$canonical_certified_backup" ]]; then
      continue
    fi
    rm -f -- "$canonical_path" "${canonical_path}.sha256"
  done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'waterfall_registry.*.db' -printf '%T@ %p\n' | sort -nr)
}

assert_telegram_delivery_disabled() {
  docker compose run --rm --no-deps --interactive=false -T waterfall-backend \
    /opt/venv/bin/python -c \
    'from waterfallhunter.config import settings; assert settings.telegram_signal_delivery_enabled is False' \
    >/dev/null 2>&1 \
    || fail "effective Telegram signal delivery setting must remain disabled without separate operator approval"
}

restore_previous_workspace() {
  [[ -n "$PREVIOUS_SHA" ]] || return 0
  git checkout --detach "$PREVIOUS_SHA" >/dev/null 2>&1 || return 1
  activate_rollback_image_override || return 1
}

previous_revision_accepts_current_schema() {
  [[ -n "$PREVIOUS_SHA" ]] || return 1
  git checkout --detach "$PREVIOUS_SHA" >/dev/null 2>&1 || return 1
  activate_rollback_image_override || return 1
  docker compose run --rm --no-deps --interactive=false -T waterfall-backend \
    /opt/venv/bin/python -m waterfallhunter.migrate_database \
    --db-path "$DB_PATH" --preflight >/dev/null 2>&1
}

rollback_previous_revision() {
  [[ "$ROLLBACK_ACTIVE" -eq 0 ]] || return 1
  ROLLBACK_ACTIVE=1
  [[ -n "$PREVIOUS_SHA" ]] || return 1
  activate_rollback_image_override || return 1

  if [[ "$MIGRATION_MAY_HAVE_MUTATED" -eq 1 ]]; then
    if ! restore_database_backup; then
      log "rollback stopped: certified pre-migration database restore failed"
      docker compose stop waterfall-backend frontend watchdog >/dev/null 2>&1 || true
      git checkout --detach "$WFH_DEPLOY_SHA" >/dev/null 2>&1 || true
      return 1
    fi
    if ! previous_revision_accepts_current_schema; then
      log "rollback stopped: previous revision is not certified against the restored schema"
      docker compose stop waterfall-backend frontend watchdog >/dev/null 2>&1 || true
      git checkout --detach "$WFH_DEPLOY_SHA" >/dev/null 2>&1 || true
      return 1
    fi
  else
    restore_previous_workspace || return 1
  fi

  remove_release_containers_before_compose_handoff || return 1
  docker compose up -d --remove-orphans </dev/null || return 1
  wait_for_backend_endpoint /livez 20 3 || return 1
  wait_for_backend_endpoint /readyz 30 4 || return 1
  wait_for_container_healthy waterfall-backend 30 3 || return 1
  wait_for_container_healthy waterfall-frontend 30 3 || return 1
  wait_for_container_healthy waterfall-watchdog 30 3 || return 1
  wait_for_monitoring_containers_healthy || return 1
  verify_running_revision "$PREVIOUS_SHA" || return 1
  verify_running_signal_only || return 1
  log "rollback certified at ${PREVIOUS_SHA}"
  return 0
}

terminate_with_cleanup() {
  local status="$1"
  [[ "$CLEANUP_ACTIVE" -eq 0 ]] || exit "$status"
  CLEANUP_ACTIVE=1
  trap - ERR TERM HUP INT
  set +e
  if [[ "$RUNTIME_REPLACED" -eq 1 || "$MIGRATION_MAY_HAVE_MUTATED" -eq 1 ]]; then
    log "deployment failed after mutable Production step; attempting bounded rollback"
    rollback_previous_revision \
      || log "automatic rollback could not be certified; backup retained at ${DB_BACKUP:-unavailable}"
  else
    restore_previous_workspace \
      || log "pre-mutation workspace restoration could not be certified"
  fi
  if [[ "$HOST_INTEGRATION_MUTATED" -eq 1 ]]; then
    restore_host_integration_state \
      || log "host integration restoration could not be certified"
  fi
  cleanup_incoming_artifacts \
    || log "incoming artifact cleanup failed during failure handling"
  prune_database_backups \
    || log "database backup retention cleanup failed during failure handling"
  exit "$status"
}

on_error() {
  local status=$?
  terminate_with_cleanup "$status"
}

on_signal() {
  local signal="$1"
  local status=1
  case "$signal" in
    HUP) status=129 ;;
    INT) status=130 ;;
    TERM) status=143 ;;
    *) status=1 ;;
  esac
  log "received ${signal}; initiating bounded cleanup"
  terminate_with_cleanup "$status"
}

trap on_error ERR
trap 'on_signal TERM' TERM
trap 'on_signal HUP' HUP
trap 'on_signal INT' INT

require_command git
require_command docker
require_command flock
require_command awk
require_command find
require_command sort
require_command sha256sum
require_command install
require_command python3
require_command systemctl
require_command nginx
require_command curl

[[ "$WFH_DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "WFH_DEPLOY_SHA must be an exact 40-character Git SHA"
[[ "$WFH_TESTED_IMAGE_BUNDLE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "WFH_TESTED_IMAGE_BUNDLE_SHA256 must be a SHA256"
[[ "$WFH_TESTED_BACKEND_IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "WFH_TESTED_BACKEND_IMAGE_DIGEST invalid"
[[ "$WFH_TESTED_FRONTEND_IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "WFH_TESTED_FRONTEND_IMAGE_DIGEST invalid"
[[ "$WFH_TESTED_WATCHDOG_IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "WFH_TESTED_WATCHDOG_IMAGE_DIGEST invalid"
[[ -d "$WFH_DEPLOY_ROOT/.git" ]] || fail "deployment root is not a Git checkout: $WFH_DEPLOY_ROOT"
[[ -f "$ENV_FILE" ]] || fail "Production .env is missing"
[[ "$WFH_DEPLOY_BACKUP_RETENTION_COUNT" =~ ^[1-9][0-9]*$ ]] || fail "WFH_DEPLOY_BACKUP_RETENTION_COUNT must be a positive integer"

cd "$WFH_DEPLOY_ROOT"
# Reject repository mixing before creating state or fetching/checkout.
git rev-parse --show-toplevel
git remote get-url origin
git branch --show-current
git rev-parse HEAD
git status --porcelain=v2
[[ "$(git remote get-url origin)" == "https://github.com/${EXPECTED_REPOSITORY}.git" ]] || {
  log "ERROR: deployment repository identity mismatch"
  exit 1
}
export WFH_ENV_FILE="$ENV_FILE"
export COMPOSE_PROJECT_NAME="waterfallhunter"
install -d -m 0750 "$STATE_DIR" "$BACKUP_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || fail "another WaterfallHunter deployment is already running"
configure_production_compose_topology

git fetch --prune origin main
git cat-file -e "${WFH_DEPLOY_SHA}^{commit}" || fail "target revision is not available after fetch"
[[ "$(git rev-parse origin/main)" == "$WFH_DEPLOY_SHA" ]] \
  || fail "target revision is stale; only the current origin/main tip may deploy"

if ! assert_clean_deploy_worktree; then
  log "ERROR: deployment worktree contains tracked or untracked source changes"
  exit 1
fi

PREVIOUS_SHA="$(resolve_previous_revision)" || fail "unable to resolve the certified previous Production revision"
pin_previous_running_images
assert_signal_only_runtime_boundary

git checkout --detach "$WFH_DEPLOY_SHA"
docker compose config --quiet
assert_signal_only_runtime_boundary
assert_monitoring_bind_files_readable \
  || fail "Prometheus bind-mounted configuration is not readable by the non-root service"

load_tested_release_artifacts
pin_target_release_images
activate_target_image_override
assert_telegram_delivery_disabled

backup_database

docker compose run --rm --no-deps --interactive=false -T waterfall-backend \
  /opt/venv/bin/python -m waterfallhunter.migrate_database \
  --db-path "$DB_PATH" --preflight

# From this point onward the migration command may have changed the database
# even when a later verification inside that command exits non-zero.
MIGRATION_MAY_HAVE_MUTATED=1
docker compose run --rm --no-deps --interactive=false -T waterfall-backend \
  /opt/venv/bin/python -m waterfallhunter.migrate_database \
  --db-path "$DB_PATH" --apply --source-revision "$WFH_DEPLOY_SHA"

RUNTIME_REPLACED=1
remove_release_containers_before_compose_handoff
docker compose up -d --remove-orphans --no-build </dev/null

wait_for_backend_endpoint /livez 20 3 || fail "backend /livez did not become healthy"
wait_for_backend_endpoint /readyz 30 4 || fail "backend /readyz did not become ready"
wait_for_container_healthy waterfall-backend 30 3 || fail "backend container did not become healthy"
wait_for_container_healthy waterfall-frontend 30 3 || fail "frontend container did not become healthy"
wait_for_container_healthy waterfall-watchdog 30 3 || fail "watchdog container did not become healthy"
wait_for_monitoring_containers_healthy || fail "monitoring containers did not become healthy"
verify_running_revision "$WFH_DEPLOY_SHA" || fail "running OCI org.opencontainers.image.revision does not match target SHA"
verify_running_signal_only || fail "running backend violated the SIGNAL_ONLY live-trading boundary"

snapshot_host_integration_state
promote_target_image_override
install_systemd_units
install_nginx_site
verify_public_edge || fail "public WaterfallHunter edge did not become reachable"
activate_systemd_units || fail "canonical systemd service/timer did not become active"
cleanup_incoming_artifacts || fail "incoming tested-image bundle cleanup failed"

prune_database_backups || fail "database backup retention cleanup failed"

install -d -m 0750 "$STATE_DIR"
cat > "${STATE_DIR}/last-successful-deploy.txt" <<EOF
revision=${WFH_DEPLOY_SHA}
previous_revision=${PREVIOUS_SHA}
deployed_at=${DEPLOY_EPOCH}
backup=${DB_BACKUP}
backup_sha256=${DB_BACKUP_SHA256}
tested_backend_image_digest=${WFH_TESTED_BACKEND_IMAGE_DIGEST}
tested_frontend_image_digest=${WFH_TESTED_FRONTEND_IMAGE_DIGEST}
tested_watchdog_image_digest=${WFH_TESTED_WATCHDOG_IMAGE_DIGEST}
tested_image_bundle_sha256=${WFH_TESTED_IMAGE_BUNDLE_SHA256}
telegram_signal_delivery_enabled=false
live_trading_enabled=false
product_mode=SIGNAL_ONLY
EOF

if [[ -n "$HOST_INTEGRATION_BACKUP_DIR" && -d "$HOST_INTEGRATION_BACKUP_DIR" ]]; then
  rm -rf -- "$HOST_INTEGRATION_BACKUP_DIR"
  HOST_INTEGRATION_BACKUP_DIR=""
fi

trap - ERR TERM HUP INT
log "deployment certified: revision=${WFH_DEPLOY_SHA} previous=${PREVIOUS_SHA} SIGNAL_ONLY=true"
