#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="${NEXTCLOUD_PRODUCTION_ROOT:-/srv/nextcloud-production}"
DATA="${NEXTCLOUD_DATA_ROOT:-/data/nextcloud-production}"
ENV_FILE="${NEXTCLOUD_IMAGES_ENV:-$ROOT/secrets/images.env}"
DEST="${1:?usage: backup.sh BACKUP_DIRECTORY [--quiesced]}"
QUIESCED="${2:-}"
dc() { docker compose --project-name nextcloud-production --env-file "$ENV_FILE" -f "$ROOT/current/deploy/production/compose.yaml" "$@"; }
workers=(cron event-worker event-status-worker directory-discovery-worker stage-status-worker)
resume() {
  local status=$?
  trap - EXIT
  if [[ "$QUIESCED" != --quiesced ]]; then
    dc up -d db redis nextcloud office || status=1
    local ready=false
    for ((attempt=0; attempt<60; attempt++)); do
      if dc exec -T --user www-data nextcloud php occ maintenance:mode --off; then ready=true; break; fi
      sleep 2
    done
    if [[ "$ready" == true ]]; then
      if NEXTCLOUD_SKIP_PROXY=1 NEXTCLOUD_SKIP_WORKERS=1 "$ROOT/current/deploy/production/healthcheck.sh"; then
        dc up -d --wait "${workers[@]}" proxy || status=1
        "$ROOT/current/deploy/production/healthcheck.sh" || status=1
      else
        status=1
        echo 'Internal checkpoint health check failed; public proxy remains stopped' >&2
      fi
    else
      status=1
      echo 'Nextcloud did not recover after checkpoint; public proxy remains stopped' >&2
    fi
  fi
  exit "$status"
}
[[ ! -e "$DEST" ]] || { echo 'Backup target already exists; refusing overwrite' >&2; exit 1; }
mkdir -p -m 0700 "$DEST"
if [[ "$QUIESCED" != --quiesced ]]; then
  exec 9>"$ROOT/state/update.lock"
  flock -n 9 || { echo 'Production update/backup already running' >&2; exit 1; }
  dc exec -T --user www-data nextcloud php occ maintenance:mode --on
  trap resume EXIT
  dc stop proxy
  dc stop --timeout 60 office nextcloud "${workers[@]}"
fi
test -d "$DATA/html/config"
test -d "$DATA/html/custom_apps"
cp "$ENV_FILE" "$DEST/images.env"
readlink -f "$ROOT/current" > "$DEST/previous-release.txt"
python3 "$ROOT/bin/release.py" --record-db-role "$DEST"
dc exec -T db sh -eu -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' > "$DEST/postgres.dump.tmp"
test -s "$DEST/postgres.dump.tmp"
mv "$DEST/postgres.dump.tmp" "$DEST/postgres.dump"
tar --numeric-owner -cpf "$DEST/config-customapps.tar" -C "$DATA/html" config custom_apps
dc exec -T db sh -eu -c 'exec pg_restore --list' < "$DEST/postgres.dump" > "$DEST/postgres.dump.list"
(
  cd "$DEST"
  sha256sum postgres.dump config-customapps.tar images.env database-role.json > SHA256SUMS
)
date -u +%Y-%m-%dT%H:%M:%SZ > "$DEST/COMPLETE"
sync -f "$DEST" 2>/dev/null || sync
echo 'Production checkpoint complete'
