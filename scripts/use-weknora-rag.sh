#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$project_dir/.." && pwd)"
weknora_dir="$workspace_dir/weknora-ldap-local"
compose=(docker compose -f "$weknora_dir/compose.yaml" \
  -f "$project_dir/integration/weknora.override.yaml" \
  -f "$project_dir/integration/weknora-rag-local.override.yaml")
nextcloud_compose=(docker compose -f "$project_dir/compose.yaml")

abort_upgrade() {
  echo "RAG upgrade preflight failed: $1" >&2
  echo "Drain and revoke event connections, then review docs/nextcloud-event-receiver.md before retrying." >&2
  exit 1
}

require_healthy_db() {
  local label="$1" service="$2" id status
  shift 2
  id="$("$@" ps -q "$service")" || abort_upgrade "$label database container lookup failed"
  [[ "$id" =~ ^[0-9a-f]+$ ]] || abort_upgrade "$label database container is missing or ambiguous"
  status="$(docker inspect --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$id")" || \
    abort_upgrade "$label database status lookup failed"
  [[ "$status" == 'running|healthy' ]] || abort_upgrade "$label database status is $status"
}

require_quiescent_event_connections() {
  local receiver_counts receiver_active_count receiver_unexpected_count sender_count
  require_healthy_db WeKnora postgres "${compose[@]}"
  require_healthy_db Nextcloud db "${nextcloud_compose[@]}"

  receiver_counts="$("${compose[@]}" exec -T postgres psql -X -At -v ON_ERROR_STOP=1 \
    -U weknora -d weknora -c "SELECT COUNT(*) FILTER (WHERE status = 'active')::text || '|' ||
      COUNT(*) FILTER (WHERE status IS NULL OR status NOT IN ('pending', 'active', 'revoked'))::text
      FROM nextcloud_event_connections")" || abort_upgrade 'WeKnora event connection query failed'
  [[ "$receiver_counts" =~ ^(0|[1-9][0-9]*)\|(0|[1-9][0-9]*)$ ]] || \
    abort_upgrade 'WeKnora event connection count is invalid'
  receiver_active_count="${BASH_REMATCH[1]}"
  receiver_unexpected_count="${BASH_REMATCH[2]}"
  [[ "$receiver_active_count" == '0' && "$receiver_unexpected_count" == '0' ]] || \
    abort_upgrade "WeKnora connections remain (active=$receiver_active_count, unexpected_status=$receiver_unexpected_count)"

  # Count every installed sender row, including paused ones; pausing alone is
  # insufficient to protect a receiver upgrade.
  sender_count="$("${nextcloud_compose[@]}" exec -T db psql -X -At -v ON_ERROR_STOP=1 \
    -U nextcloud -d nextcloud -c 'SELECT COUNT(*) FROM oc_weknora_event_conn')" || \
    abort_upgrade 'Nextcloud installed sender query failed'
  [[ "$sender_count" =~ ^(0|[1-9][0-9]*)$ ]] || abort_upgrade 'Nextcloud installed sender count is invalid'
  [[ "$sender_count" == '0' ]] || \
    abort_upgrade "Nextcloud still has $sender_count installed event sender connection(s)"
}

for image in weknora-ldap-app:nextcloud-rag weknora-ldap-ui:nextcloud-rag; do
  docker image inspect "$image" >/dev/null
done
docker network inspect nextcloud-weknora-dev_default >/dev/null
require_quiescent_event_connections

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
