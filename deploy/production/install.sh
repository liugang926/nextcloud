#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "$(id -u)" == 0 ]] || { echo 'Run install.sh as root' >&2; exit 1; }
ROOT="${NEXTCLOUD_PRODUCTION_ROOT:-/srv/nextcloud-production}"
DATA="${NEXTCLOUD_DATA_ROOT:-/data/nextcloud-production}"
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "$ROOT" == /srv/nextcloud-production && "$DATA" == /data/nextcloud-production ]] || {
  echo 'systemd units require the documented production paths' >&2; exit 1;
}
for executable in docker python3 flock curl tar sha256sum systemctl; do
  command -v "$executable" >/dev/null || { echo "Missing prerequisite: $executable" >&2; exit 1; }
done
docker compose version >/dev/null
mkdir -p -m 0755 "$ROOT" "$ROOT/releases" "$ROOT/bin"
mkdir -p -m 0700 "$ROOT/secrets" "$ROOT/state" "$DATA/backups"
mkdir -p "$DATA/html" "$DATA/data" "$DATA/postgres" "$DATA/redis" "$DATA/office"
python3 - "$ROOT/secrets" <<'PY'
from pathlib import Path
import os,secrets,sys
directory=Path(sys.argv[1])
for name in ('nextcloud_db_password','nextcloud_admin_password'):
    path=directory/name
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        if not path.is_file() or path.stat().st_size==0:
            raise SystemExit('Existing secret file is empty or invalid: '+name)
    else:
        with os.fdopen(fd,'w') as stream: stream.write(secrets.token_urlsafe(36)+'\n')
env=directory/'images.env'
if not env.exists():
    fd=os.open(env,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write('NC_ADMIN_USER=admin\nNEXTCLOUD_DOMAIN=cloud.tianjizn.com\nWEKNORA_HTTPS_ORIGINS=https://ai-doc.tianjizn.com\n')
PY
for file in release.py update.sh backup.sh; do
  install -m 0750 "$HERE/$file" "$ROOT/bin/$file"
done
for unit in nextcloud-production.slice nextcloud-production.service nextcloud-production-update.service nextcloud-production-update.timer; do
  install -m 0644 "$HERE/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now docker.service
systemctl start nextcloud-production.slice
test "$(systemctl show nextcloud-production.slice --property=MemoryMax --value)" != infinity
systemctl enable nextcloud-production.service
systemctl enable --now nextcloud-production-update.timer
if [[ -f "$ROOT/current/deploy/production/compose.yaml" && -s "$ROOT/secrets/images.env" ]]; then
  if docker compose --env-file "$ROOT/secrets/images.env" -f "$ROOT/current/deploy/production/compose.yaml" config --quiet; then
    systemctl start nextcloud-production.service
  fi
fi
echo 'Production boot service and 2-minute public-release updater installed'
