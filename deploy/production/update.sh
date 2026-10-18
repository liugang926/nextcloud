#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="${NEXTCLOUD_PRODUCTION_ROOT:-/srv/nextcloud-production}"
mkdir -p "$ROOT/state"
exec 9>"$ROOT/state/update.lock"
if [[ "${1:-}" == --start-production ]]; then
  # Keep recovery, boot, and public ingress under the same lock as updates.
  flock -w 900 9 || { echo 'Production update still holds the boot lock' >&2; exit 1; }
  python3 "$ROOT/bin/release.py" --recover-only
  docker compose --project-name nextcloud-production --env-file "$ROOT/secrets/images.env" \
    -f "$ROOT/current/deploy/production/compose.yaml" up -d --wait --wait-timeout 600
  "$ROOT/current/deploy/production/healthcheck.sh"
  exit 0
fi
if [[ "${1:-}" == --recover-only ]]; then
  flock -w 900 9 || { echo 'Production update still holds the recovery lock' >&2; exit 1; }
else
  flock -n 9 || exit 0
fi
exec python3 "$ROOT/bin/release.py" "$@"
