#!/usr/bin/env bash
set -euo pipefail
umask 027
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

RUNNER_USER="wfh-deploy"
RUNNER_ROOT="/var/lib/wfh-github-runner"
RUNNER_HOME="${RUNNER_ROOT}/home"
WRAPPER_TARGET="/usr/local/sbin/wfh-production-deploy"
PROVISIONER_TARGET="/usr/local/sbin/wfh-provision-production-runner"
SUDOERS_TARGET="/etc/sudoers.d/wfh-production-deploy"
BOUNDARY_REVISION_TARGET="/etc/waterfallhunter/runner-boundary-revision"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
WRAPPER_SOURCE="${SCRIPT_DIR}/wfh-production-deploy"
PROVISIONER_SOURCE="${SCRIPT_DIR}/provision-ephemeral-runner.sh"

fail() {
  printf '[wfh-runner-install] ERROR: %s\n' "$*" >&2
  exit 1
}

[[ "$EUID" -eq 0 ]] || fail "host installation must run as root"
for command in getent git groupadd id install sudo useradd usermod visudo; do
  command -v "$command" >/dev/null || fail "required command missing: $command"
done
[[ -f "$WRAPPER_SOURCE" && ! -L "$WRAPPER_SOURCE" ]] || fail "wrapper source missing"
[[ -f "$PROVISIONER_SOURCE" && ! -L "$PROVISIONER_SOURCE" ]] \
  || fail "provisioner source missing"

SOURCE_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)" \
  || fail "installer source is not a Git checkout"
[[ -z "$(git -C "$SOURCE_ROOT" status --porcelain --untracked-files=all)" ]] \
  || fail "installer source checkout is not clean"
source_origin="$(git -C "$SOURCE_ROOT" remote get-url origin)"
case "$source_origin" in
  https://github.com/cavack/WFH-ORG|https://github.com/cavack/WFH-ORG.git) ;;
  *) fail "installer source origin is not cavack/WFH-ORG" ;;
esac
git -C "$SOURCE_ROOT" fetch --no-tags origin main >/dev/null
source_main="$(git -C "$SOURCE_ROOT" rev-parse origin/main)"
[[ "$(git -C "$SOURCE_ROOT" rev-parse HEAD)" == "$source_main" ]] \
  || fail "host boundary must be installed from exact current WFH-ORG main"

if ! getent group "$RUNNER_USER" >/dev/null 2>&1; then
  groupadd --system "$RUNNER_USER"
fi
if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  useradd --system --gid "$RUNNER_USER" --no-create-home \
    --home-dir "$RUNNER_HOME" --shell /bin/bash "$RUNNER_USER"
fi
[[ "$(id -u "$RUNNER_USER")" != "0" ]] || fail "runner user must not have UID 0"
usermod --gid "$RUNNER_USER" --groups "" --lock \
  --home "$RUNNER_HOME" --shell /bin/bash "$RUNNER_USER"

install -d -o root -g "$RUNNER_USER" -m 0750 "$RUNNER_ROOT"
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0750 "$RUNNER_HOME"
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0750 "$RUNNER_ROOT/runner"
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0750 "$RUNNER_ROOT/_work"
install -d -o root -g root -m 0750 /srv/waterfallhunter/runtime/state
install -d -o root -g root -m 0750 /srv/waterfallhunter/runtime/incoming
install -d -o root -g root -m 0750 /etc/waterfallhunter
install -o root -g root -m 0755 "$WRAPPER_SOURCE" "$WRAPPER_TARGET"
install -o root -g root -m 0750 "$PROVISIONER_SOURCE" "$PROVISIONER_TARGET"

revision_tmp="$(mktemp)"
trap 'rm -f -- "$revision_tmp"' EXIT
printf '%s\n' "$source_main" >"$revision_tmp"
install -o root -g root -m 0444 "$revision_tmp" "$BOUNDARY_REVISION_TARGET"

sudoers_tmp="$(mktemp)"
trap 'rm -f -- "$sudoers_tmp" "$revision_tmp"' EXIT
cat >"$sudoers_tmp" <<'EOF'
Defaults:wfh-deploy !requiretty
wfh-deploy ALL=(root) NOPASSWD: /usr/local/sbin/wfh-production-deploy
EOF
chmod 0440 "$sudoers_tmp"
visudo -cf "$sudoers_tmp" >/dev/null
install -o root -g root -m 0440 "$sudoers_tmp" "$SUDOERS_TARGET"
visudo -cf "$SUDOERS_TARGET" >/dev/null
trap - EXIT
rm -f -- "$sudoers_tmp" "$revision_tmp"

[[ "$(stat -c '%U:%G:%a' "$WRAPPER_TARGET")" == "root:root:755" ]] || fail "wrapper ownership mismatch"
[[ "$(stat -c '%U:%G:%a' "$PROVISIONER_TARGET")" == "root:root:750" ]] \
  || fail "provisioner ownership mismatch"
[[ "$(stat -c '%U:%G:%a' "$BOUNDARY_REVISION_TARGET")" == "root:root:444" ]] \
  || fail "boundary revision ownership mismatch"
[[ "$(stat -c '%U:%G:%a' "$SUDOERS_TARGET")" == "root:root:440" ]] || fail "sudoers ownership mismatch"
[[ "$(stat -c '%U:%G:%a' "$RUNNER_ROOT")" == "root:${RUNNER_USER}:750" ]] \
  || fail "runner root ownership mismatch"
[[ "$(id -gn "$RUNNER_USER")" == "$RUNNER_USER" ]] \
  || fail "runner user primary group mismatch"
if id -nG "$RUNNER_USER" | tr ' ' '\n' | grep -Fxq docker; then
  fail "runner user must not belong to the docker group"
fi
[[ "$(id -nG "$RUNNER_USER")" == "$RUNNER_USER" ]] \
  || fail "runner user has unexpected supplementary groups"
sudo_listing="$(LC_ALL=C sudo -n -l -U "$RUNNER_USER")" \
  || fail "unable to inspect effective runner sudo policy"
mapfile -t sudo_commands < <(
  printf '%s\n' "$sudo_listing" \
    | sed -n 's/^[[:space:]]\{4,\}\((.*\)$/\1/p'
)
expected_sudo="(root) NOPASSWD: ${WRAPPER_TARGET}"
[[ "${#sudo_commands[@]}" -eq 1 && "${sudo_commands[0]}" == "$expected_sudo" ]] \
  || fail "runner user has sudo privileges beyond the exact deployment wrapper"
printf '[wfh-runner-install] host boundary installed for %s\n' "$RUNNER_USER"
