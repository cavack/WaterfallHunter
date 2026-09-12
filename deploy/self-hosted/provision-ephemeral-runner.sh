#!/usr/bin/env bash
set -euo pipefail
umask 027
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

REPOSITORY="cavack/WFH-ORG"
RUNNER_USER="wfh-deploy"
RUNNER_ROOT="/var/lib/wfh-github-runner"
RUNNER_HOME="${RUNNER_ROOT}/home"
RUNNER_DIR="${RUNNER_ROOT}/runner"
LOCK_FILE="/run/wfh-production-runner.lock"
TARGET_SHA="${1:-}"
RUN_ID="${2:-}"
LABEL=""
RUNNER_VERSION="2.337.0"
RUNNER_ASSET="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
RUNNER_ASSET_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_ASSET}"
RUNNER_ASSET_SHA256="70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613"

fail() {
  printf '[wfh-runner-provision] ERROR: %s\n' "$*" >&2
  exit 1
}

reap_runner_registration() {
  local runner_name_to_reap="$1" unit row runner_id runner_status runner_busy
  [[ "$EUID" -eq 0 ]] || fail "runner reaping must run as root"
  [[ "$runner_name_to_reap" =~ ^wfh-production-[0-9a-f]{12}-[1-9][0-9]*-[1-9][0-9]*$ ]] \
    || fail "invalid runner name for reaping"
  unit="wfh-production-runner-${runner_name_to_reap#wfh-production-}"
  gh auth status >/dev/null 2>&1 || fail "local gh authentication unavailable"

  # At 61 minutes, retire an unused runner. If a deployment is already busy,
  # never stop it; observe until the one-job ephemeral runner exits/unregisters.
  for _ in $(seq 1 720); do
    row="$(
      gh api "repos/${REPOSITORY}/actions/runners?per_page=100" \
        --jq ".runners[] | select(.name == \"${runner_name_to_reap}\") | [.id,.status,.busy] | @tsv" \
        | head -n1 || true
    )"
    if [[ -z "$row" ]]; then
      systemctl stop "$unit" >/dev/null 2>&1 || true
      return 0
    fi
    IFS=$'\t' read -r runner_id runner_status runner_busy <<<"$row"
    [[ "$runner_id" =~ ^[1-9][0-9]*$ ]] || fail "runner ID is invalid"

    if [[ "$runner_status" == "online" && "$runner_busy" == "false" ]]; then
      systemctl stop "$unit" >/dev/null 2>&1 || true
      for _ in $(seq 1 12); do
        row="$(
          gh api "repos/${REPOSITORY}/actions/runners?per_page=100" \
            --jq ".runners[] | select(.name == \"${runner_name_to_reap}\") | [.id,.status,.busy] | @tsv" \
            | head -n1 || true
        )"
        [[ -n "$row" ]] || return 0
        IFS=$'\t' read -r runner_id runner_status runner_busy <<<"$row"
        if [[ "$runner_status" == "offline" && "$runner_busy" == "false" ]]; then
          gh api --method DELETE "repos/${REPOSITORY}/actions/runners/${runner_id}" >/dev/null
          return 0
        fi
        sleep 5
      done
      fail "idle runner did not become offline after its transient unit stopped"
    fi

    if [[ "$runner_status" == "offline" && "$runner_busy" == "false" ]]; then
      gh api --method DELETE "repos/${REPOSITORY}/actions/runners/${runner_id}" >/dev/null
      return 0
    fi
    if [[ "$runner_busy" == "true" ]]; then
      sleep 5
      continue
    fi
    sleep 5
  done
  fail "runner remained active past the bounded reap observation window"
}

[[ "$EUID" -eq 0 ]] || fail "provisioning must run as root"
for command in flock gh; do
  command -v "$command" >/dev/null || fail "required command missing: $command"
done
[[ ! -L "$LOCK_FILE" ]] || fail "runner lock must not be a symlink"
exec 9>"$LOCK_FILE"
chown root:root "$LOCK_FILE"
chmod 0600 "$LOCK_FILE"
if [[ "${1:-}" == "--reap" ]]; then
  flock -w 300 9 || fail "timed out waiting for the runner provisioning lock"
else
  flock -n 9 || fail "another runner provisioning operation is active"
fi

case "${1:-}" in
  --reap) reap_runner_registration "${2:-}"; exit 0 ;;
esac

runner_name=""
unit=""
runner_registered=0
cleanup_failed_provisioning() {
  local code=$? runner_id=""
  if (( code != 0 )); then
    if [[ -n "$unit" ]]; then
      systemctl stop "$unit" >/dev/null 2>&1 || true
    fi
    if (( runner_registered == 1 )) && [[ -n "$runner_name" ]]; then
      runner_id="$(
        gh api "repos/${REPOSITORY}/actions/runners?per_page=100" \
          --jq ".runners[] | select(.name == \"${runner_name}\") | .id" \
          | head -n1 || true
      )"
      if [[ "$runner_id" =~ ^[1-9][0-9]*$ ]]; then
        gh api --method DELETE "repos/${REPOSITORY}/actions/runners/${runner_id}" \
          >/dev/null 2>&1 || true
      fi
    fi
  fi
  exit "$code"
}
trap cleanup_failed_provisioning EXIT

[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "target SHA required"
[[ "$RUN_ID" =~ ^[1-9][0-9]*$ ]] || fail "workflow dispatch run ID required"
id "$RUNNER_USER" >/dev/null 2>&1 || fail "run host installer first"
for command in gh curl sha256sum tar runuser systemd-run systemctl; do
  command -v "$command" >/dev/null || fail "required command missing: $command"
done

gh auth status >/dev/null 2>&1 || fail "local gh authentication unavailable"
current_main="$(gh api "repos/${REPOSITORY}/commits/main" --jq .sha)"
[[ "$current_main" == "$TARGET_SHA" ]] || fail "target SHA is not current WFH-ORG main"
run_line="$(
  gh api "repos/${REPOSITORY}/actions/runs/${RUN_ID}" \
    --jq '[.head_sha,.event,.name,.path,.status,.head_branch,.head_repository.full_name,.run_attempt] | @tsv'
)" || fail "workflow dispatch metadata unavailable"
IFS=$'\t' read -r run_head run_event run_name run_path run_status run_branch run_repository run_attempt <<<"$run_line"
[[ "$run_head" == "$TARGET_SHA" ]] || fail "workflow dispatch revision mismatch"
[[ "$run_event" == "workflow_dispatch" ]] || fail "runner may only bind to an explicit workflow dispatch"
[[ "$run_name" == "CI" && "$run_path" == ".github/workflows/ci.yml" ]] \
  || fail "runner may only bind to the canonical CI workflow"
[[ "$run_status" == "queued" || "$run_status" == "in_progress" ]] \
  || fail "workflow dispatch is not active"
[[ "$run_branch" == "main" ]] || fail "workflow dispatch is not on main"
[[ "$run_repository" == "$REPOSITORY" ]] || fail "workflow dispatch repository mismatch"
[[ "$run_attempt" =~ ^[1-9][0-9]*$ ]] || fail "workflow dispatch attempt is invalid"
LABEL="wfh-production-${RUN_ID}-${run_attempt}"

online_production_runners="$(
  gh api "repos/${REPOSITORY}/actions/runners?per_page=100" \
    --jq '[.runners[] | select(.status == "online") | select(any(.labels[]; .name | startswith("wfh-production-")))] | length'
)"
[[ "$online_production_runners" == "0" ]] || fail "a wfh-production runner is already online"

if systemctl list-units --type=service --state=running --no-legend 'wfh-production-runner-*' \
  | grep -q 'wfh-production-runner-'; then
  fail "a Production runner is already active"
fi

mapfile -t assets < <(
  gh api "repos/actions/runner/releases/tags/v${RUNNER_VERSION}" \
    --jq '.assets[] | select(.name | test("^actions-runner-linux-x64-[0-9.]+\\.tar\\.gz$")) | [.browser_download_url,.digest,.name] | @tsv'
)
[[ "${#assets[@]}" -eq 1 ]] || fail "expected exactly one official Linux x64 runner asset"
IFS=$'\t' read -r asset_url asset_digest asset_name <<<"${assets[0]}"
[[ "$asset_name" == "$RUNNER_ASSET" ]] || fail "runner release asset name mismatch"
[[ "$asset_url" == "$RUNNER_ASSET_URL" ]] || fail "runner release asset URL mismatch"
[[ "$asset_digest" == "sha256:${RUNNER_ASSET_SHA256}" ]] \
  || fail "runner release asset digest mismatch"

install -d -o root -g "$RUNNER_USER" -m 0750 "$RUNNER_ROOT"
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0750 "$RUNNER_HOME"
rm -rf -- "$RUNNER_DIR"
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0750 "$RUNNER_DIR"
tarball="${RUNNER_ROOT}/${RUNNER_ASSET}"
rm -f -- "$tarball"
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location "$RUNNER_ASSET_URL" --output "$tarball"
printf '%s  %s\n' "$RUNNER_ASSET_SHA256" "$tarball" | sha256sum --check --status \
  || fail "GitHub Actions runner asset SHA-256 mismatch"
tar -xzf "$tarball" -C "$RUNNER_DIR"
rm -f -- "$tarball"
chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_DIR"

registration_token="$(gh api --method POST "repos/${REPOSITORY}/actions/runners/registration-token" --jq .token)"
[[ -n "$registration_token" ]] || fail "runner registration token unavailable"
runner_name="wfh-production-${TARGET_SHA:0:12}-${RUN_ID}-${run_attempt}"
runner_registered=1

runuser -u "$RUNNER_USER" -- "$RUNNER_DIR/config.sh" \
  --url "https://github.com/${REPOSITORY}" \
  --token "$registration_token" \
  --name "$runner_name" \
  --labels "$LABEL" \
  --work _work \
  --ephemeral \
  --disableupdate \
  --unattended \
  --replace >/dev/null
unset registration_token

unit="wfh-production-runner-${runner_name#wfh-production-}"
systemd-run \
  --unit "$unit" \
  --uid "$RUNNER_USER" \
  --gid "$RUNNER_USER" \
  --working-directory "$RUNNER_DIR" \
  --setenv="HOME=${RUNNER_HOME}" \
  --collect \
  --property=Restart=no \
  --property=TimeoutStopSec=30s \
  "$RUNNER_DIR/run.sh" >/dev/null

online=0
for _ in $(seq 1 30); do
  state="$(gh api "repos/${REPOSITORY}/actions/runners?per_page=100" \
    --jq ".runners[] | select(.name == \"${runner_name}\") | [.status,.busy] | @tsv" | head -n1 || true)"
  if [[ "$state" == $'online\tfalse' ]]; then
    online=1
    break
  fi
  sleep 2
done
[[ "$online" -eq 1 ]] || {
  fail "ephemeral runner did not become online"
}

systemd-run \
  --unit "${unit}-reaper" \
  --on-active=61m \
  --collect \
  /usr/local/sbin/wfh-provision-production-runner --reap "$runner_name" >/dev/null

trap - EXIT
printf '[wfh-runner-provision] runner=%s unit=%s target_sha=%s label=%s\n' \
  "$runner_name" "$unit" "$TARGET_SHA" "$LABEL"
