#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${WFH_PROJECT_DIR:-/srv/waterfallhunter/app}"
ENV_FILE="${WFH_ENV_FILE:-/etc/waterfallhunter/waterfallhunter.env}"
OVERRIDE="${WFH_PRODUCTION_COMPOSE_OVERRIDE:-/srv/waterfallhunter/runtime/production-volumes.override.yml}"
IMAGE_OVERRIDE="${WFH_PRODUCTION_IMAGE_OVERRIDE:-/srv/waterfallhunter/runtime/production-images.override.yml}"

export WFH_ENV_FILE="$ENV_FILE"

[[ -f "$PROJECT_DIR/docker-compose.yml" ]] || {
  echo "ERROR: production compose file missing: $PROJECT_DIR/docker-compose.yml" >&2
  exit 2
}
[[ -f "$ENV_FILE" ]] || {
  echo "ERROR: production environment missing: $ENV_FILE" >&2
  exit 2
}

compose_args=(
  --project-name waterfallhunter
  --env-file "$ENV_FILE"
  -f "$PROJECT_DIR/docker-compose.yml"
)
if [[ -f "$OVERRIDE" ]]; then
  compose_args+=(-f "$OVERRIDE")
fi
if [[ -f "$IMAGE_OVERRIDE" ]]; then
  compose_args+=(-f "$IMAGE_OVERRIDE")
fi

cd "$PROJECT_DIR"
exec /usr/bin/docker compose "${compose_args[@]}" "$@"
