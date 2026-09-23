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

# Docker reports a healthy HTTP endpoint as soon as Apache serves status.php,
# while the first-install entrypoint can still be creating the database. Wait
# for occ to report an installed instance before enabling the app.
installed=false
for attempt in {1..150}; do
  if "${occ[@]}" status --output=json 2>/dev/null | python3 -c \
    'import json, sys; sys.exit(not json.load(sys.stdin).get("installed", False))' 2>/dev/null; then
    installed=true
    break
  fi
  sleep 2
done
if [[ "$installed" != true ]]; then
  echo 'Nextcloud did not finish first-time installation within five minutes.' >&2
  exit 1
fi

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
source_hash="$(python3 - "$NEXTCLOUD_ADMIN_USER" "$root_id" <<'PY'
import hashlib
import sys
value = b'dev-published\0' + sys.argv[1].encode() + b'\0' + sys.argv[2].encode()
print(hashlib.sha256(value).hexdigest())
PY
)"
bindings="$(python3 - "$NEXTCLOUD_ADMIN_USER" "$root_id" <<'PY'
import json
import sys
print(json.dumps([{"id": "dev-published", "name": "Published", "owner_uid": sys.argv[1], "root_file_id": int(sys.argv[2])}], separators=(',', ':')))
PY
)"
registered="$(docker compose exec -T db psql -U nextcloud -d nextcloud \
  -v ON_ERROR_STOP=1 -qAtc "INSERT INTO oc_weknora_binding_id (binding_id, source_hash, retired_at) VALUES ('dev-published', '$source_hash', 0) ON CONFLICT (binding_id) DO NOTHING; SELECT CASE WHEN source_hash = '$source_hash' AND retired_at = 0 THEN 1 ELSE 0 END FROM oc_weknora_binding_id WHERE binding_id = 'dev-published'")"
if [[ "$registered" != 1 ]]; then
  echo 'Sample binding ID is retired or belongs to a different source.' >&2
  exit 1
fi
"${occ[@]}" config:app:set integration_weknora bindings --value="$bindings"
# The runtime has no global-token fallback. On a fresh installation the
# migration ran before the sample binding existed, so provision its exact
# binding key after the folder and binding have been created.
key_ready="$(docker compose exec -T db psql -U nextcloud -d nextcloud -v ON_ERROR_STOP=1 -qAtc \
  "INSERT INTO oc_weknora_machine_key (key_id, binding_id, token_sha256, source_hash, created_at, created_by_uid) VALUES ('default', 'dev-published', '$token_hash', '$source_hash', EXTRACT(EPOCH FROM NOW())::BIGINT, 'bootstrap') ON CONFLICT (key_id) DO UPDATE SET token_sha256 = EXCLUDED.token_sha256 WHERE oc_weknora_machine_key.binding_id = EXCLUDED.binding_id AND oc_weknora_machine_key.source_hash = EXCLUDED.source_hash; SELECT CASE WHEN binding_id = 'dev-published' AND source_hash = '$source_hash' AND token_sha256 = '$token_hash' THEN 1 ELSE 0 END FROM oc_weknora_machine_key WHERE key_id = 'default'")"
if [[ "$key_ready" != 1 ]]; then
  echo 'Sample key ID is already assigned to a different binding.' >&2
  exit 1
fi
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
