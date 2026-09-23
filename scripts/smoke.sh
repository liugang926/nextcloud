#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
set -a
source .env
set +a

base_url="http://127.0.0.1:${NEXTCLOUD_HTTP_PORT:-18082}/index.php/apps/integration_weknora/api/v1"
unauthorized="$(curl -sS -o /dev/null -w '%{http_code}' "$base_url/capabilities")"
if [[ "$unauthorized" != 401 ]]; then
  echo "Expected unauthorized API request to return 401; got $unauthorized." >&2
  exit 1
fi

curl -fsS -H "Authorization: Bearer $WEKNORA_SERVICE_TOKEN" "$base_url/capabilities" \
  | python3 -c 'import json,sys; value=json.load(sys.stdin); assert value["protocol_version"] == "1"; assert value["instance_id"]'
manifest="$(curl -fsS -H "Authorization: Bearer $WEKNORA_SERVICE_TOKEN" "$base_url/bindings/dev-published/manifest")"
file_id="$(printf '%s' "$manifest" | python3 -c 'import json,sys; value=json.load(sys.stdin); assert value["complete"] is True; items=[item for item in value["items"] if item["name"] == "department-notes.md"]; assert len(items) == 1; print(items[0]["file_id"])')"
curl -fsS -H "Authorization: Bearer $WEKNORA_SERVICE_TOKEN" \
  "$base_url/bindings/dev-published/files/$file_id/content" \
  | cmp - fixtures/department-notes.md

wrong_version="$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $WEKNORA_SERVICE_TOKEN" -H 'If-Match: "wrong-version"' \
  "$base_url/bindings/dev-published/files/$file_id/content")"
if [[ "$wrong_version" != 412 ]]; then
  echo "Expected conditional content request to return 412; got $wrong_version." >&2
  exit 1
fi

echo "Nextcloud API smoke passed (binding dev-published, file ID $file_id)."
