#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
if [[ ! -f .env ]]; then
  echo 'Run scripts/dev-up.sh and scripts/bootstrap.sh first.' >&2
  exit 1
fi
# Use this project's saved Compose settings, even when the invoking shell has
# exported settings for another Compose project.
unset COMPOSE_FILE COMPOSE_PROJECT_NAME NEXTCLOUD_LAN_HOST NEXTCLOUD_HTTP_BIND_IP NEXTCLOUD_HTTP_PORT

lan_host="${1:-}"
if [[ -z "$lan_host" ]] && command -v ipconfig >/dev/null 2>&1; then
  for iface in en0 en1; do
    lan_host="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
    [[ -n "$lan_host" ]] && break
  done
fi
if [[ -z "$lan_host" ]] && command -v ip >/dev/null 2>&1; then
  lan_host="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)"
fi
if [[ -z "$lan_host" ]]; then
  echo 'Provide the LAN IPv4 address: scripts/allow-lan-access.sh 192.168.1.20' >&2
  exit 1
fi
python3 - "$lan_host" <<'PY'
import ipaddress
import sys

try:
    address = ipaddress.ip_address(sys.argv[1])
except ValueError as error:
    raise SystemExit(f'Invalid LAN IP address: {error}')
if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local:
    raise SystemExit('Use a non-loopback private IPv4 address for LAN access')
PY

# Preserve generated credentials and the current .env permissions. The two
# non-secret fields are the only existing values this script changes.
python3 - "$lan_host" <<'PY'
from pathlib import Path
import os
import sys
import tempfile

path = Path('.env')
lines = path.read_text().splitlines()
values = {
    'NEXTCLOUD_HTTP_BIND_IP': '127.0.0.1',
    'NEXTCLOUD_LAN_HOST': sys.argv[1],
    'COMPOSE_FILE': 'compose.yaml:integration/nextcloud.lan.yaml',
}
seen = set()
updated = []
for line in lines:
    key = line.split('=', 1)[0]
    if key in values:
        if key in seen:
            raise SystemExit(f'Duplicate {key} in .env')
        if key == 'COMPOSE_FILE' and line.split('=', 1)[1] != values[key]:
            raise SystemExit('Existing COMPOSE_FILE needs manual review before LAN setup')
        updated.append(f'{key}={values[key]}')
        seen.add(key)
    else:
        updated.append(line)
for key, value in values.items():
    if key not in seen:
        updated.append(f'{key}={value}')
with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.env-lan-',
                                 delete=False) as stream:
    stream.write('\n'.join(updated) + '\n')
    temporary = Path(stream.name)
try:
    os.chmod(temporary, path.stat().st_mode & 0o777)
    os.replace(temporary, path)
finally:
    temporary.unlink(missing_ok=True)
PY

docker compose --env-file .env config --format json | python3 -c '
import json, sys
expected = {"127.0.0.1", sys.argv[1]}
ports = json.load(sys.stdin)["services"]["nextcloud"]["ports"]
hosts = {item.get("host_ip") for item in ports if item.get("target") == 80}
if hosts != expected:
    raise SystemExit("refusing unexpected Nextcloud listener interfaces")
' "$lan_host"
docker compose --env-file .env up -d --wait --wait-timeout 180 nextcloud
trusted="$(docker compose --env-file .env exec -T -u www-data nextcloud \
  php occ config:system:get trusted_domains --output=json)"
slot="$(python3 -c 'import json,sys; domains=json.load(sys.stdin); host=sys.argv[1]; print("exists" if host in domains else len(domains))' \
  "$lan_host" <<<"$trusted")"
if [[ "$slot" != exists ]]; then
  docker compose --env-file .env exec -T -u www-data nextcloud \
    php occ config:system:set trusted_domains "$slot" --value="$lan_host"
fi

port="$(python3 - <<'PY'
from pathlib import Path
for line in Path('.env').read_text().splitlines():
    if line.startswith('NEXTCLOUD_HTTP_PORT='):
        print(line.split('=', 1)[1].strip())
        break
else:
    print('18082')
PY
)"
url="http://$lan_host:$port"
status="$(curl --noproxy '*' -sS --max-time 15 -o /dev/null -w '%{http_code}' "$url/login")"
if [[ "$status" != 200 ]]; then
  echo "LAN endpoint returned HTTP $status: $url/login" >&2
  exit 1
fi
echo "Nextcloud LAN access ready: $url"
