#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$project_dir/.." && pwd)"
weknora_dir="$workspace_dir/weknora-ldap-local"
compose=(docker compose -f "$weknora_dir/compose.yaml" \
  -f "$project_dir/integration/weknora.override.yaml" \
  -f "$project_dir/integration/weknora-rag-local.override.yaml")

for image in weknora-ldap-app:nextcloud-rag weknora-ldap-ui:nextcloud-rag; do
  docker image inspect "$image" >/dev/null
done
docker network inspect nextcloud-weknora-dev_default >/dev/null

backup_dir="$project_dir/dist/backups"
umask 077
mkdir -p "$backup_dir"
backup_file="$backup_dir/weknora-before-rag-$(date -u +%Y%m%dT%H%M%SZ).dump"
"${compose[@]}" exec -T postgres pg_dump -U weknora -d weknora -Fc >"$backup_file"
"${compose[@]}" exec -T postgres pg_restore -l <"$backup_file" >/dev/null

"${compose[@]}" up -d --no-deps --wait --wait-timeout 180 app frontend
# The local nginx gateway resolves the frontend container when it starts. Its
# upstream IP changes when Compose recreates the frontend.
"${compose[@]}" restart lan-gateway >/dev/null
curl --noproxy '*' -fsS --max-time 15 http://127.0.0.1:18081/health >/dev/null
public_url="$("${compose[@]}" config --format json | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["services"]["app"]["environment"]["FRONTEND_BASE_URL"])')"
curl --noproxy '*' -kfsS --max-time 15 "$public_url/platform/nextcloud-ask" >/dev/null
echo "WeKnora RAG app and frontend are ready; database backup: $backup_file"
