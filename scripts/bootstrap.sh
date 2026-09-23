#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
if [[ ! -f .env ]]; then
  echo 'Run scripts/dev-up.sh first.' >&2
  exit 1
fi
set -a
source .env
set +a

base_url="http://127.0.0.1:${NEXTCLOUD_HTTP_PORT:-18082}"
occ=(docker compose exec -T -u www-data nextcloud php occ)

"${occ[@]}" app:enable integration_weknora

folder_url="$base_url/remote.php/dav/files/$NEXTCLOUD_ADMIN_USER/Published"
status="$(curl -sS -o /dev/null -w '%{http_code}' -u "$NEXTCLOUD_ADMIN_USER:$NEXTCLOUD_ADMIN_PASSWORD" -X MKCOL "$folder_url")"
if [[ "$status" != 201 && "$status" != 405 ]]; then
  echo "Could not create sample folder (HTTP $status)." >&2
  exit 1
fi

curl -fsS -o /dev/null -u "$NEXTCLOUD_ADMIN_USER:$NEXTCLOUD_ADMIN_PASSWORD" \
  -X PUT --data-binary @fixtures/department-notes.md \
  "$folder_url/department-notes.md"

root_id="$(curl -fsS -u "$NEXTCLOUD_ADMIN_USER:$NEXTCLOUD_ADMIN_PASSWORD" \
  -X PROPFIND -H 'Depth: 0' -H 'Content-Type: application/xml' \
  --data '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop><oc:fileid/></d:prop></d:propfind>' \
  "$folder_url" | python3 -c 'import sys, xml.etree.ElementTree as ET; root=ET.parse(sys.stdin).getroot(); value=root.find(".//{http://owncloud.org/ns}fileid"); print(value.text if value is not None else "")')"
if [[ ! "$root_id" =~ ^[0-9]+$ ]]; then
  echo 'Could not read the published folder file ID.' >&2
  exit 1
fi

token_hash="$(printf '%s' "$WEKNORA_SERVICE_TOKEN" | shasum -a 256 | awk '{print $1}')"
bindings="$(python3 - "$NEXTCLOUD_ADMIN_USER" "$root_id" <<'PY'
import json
import sys
print(json.dumps([{"id": "dev-published", "name": "Published", "owner_uid": sys.argv[1], "root_file_id": int(sys.argv[2])}], separators=(',', ':')))
PY
)"
"${occ[@]}" config:app:set integration_weknora service_token_sha256 --value="$token_hash"
"${occ[@]}" config:app:set integration_weknora bindings --value="$bindings"
"${occ[@]}" background:cron

# On a fresh installation the Apache worker may still hold the route cache
# built before app:enable. Restart it so the service API is reachable before
# the first smoke request, then wait for the Nextcloud health check.
docker compose restart nextcloud
docker compose up -d --wait --wait-timeout 180 nextcloud
route_status=''
for attempt in {1..20}; do
  route_status="$(curl -s -o /dev/null -w '%{http_code}' \
    "$base_url/index.php/apps/integration_weknora/api/v1/capabilities" || true)"
  if [[ "$route_status" == 401 ]]; then
    break
  fi
  sleep 2
done
if [[ "$route_status" != 401 ]]; then
  echo "Integration API route did not become ready (HTTP $route_status)." >&2
  exit 1
fi

echo "Nextcloud app and sample binding are ready at $base_url (binding: dev-published, folder ID: $root_id)."
