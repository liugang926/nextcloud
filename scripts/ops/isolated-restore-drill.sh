#!/usr/bin/env bash
set -euo pipefail

# Synthetic, data-level backup and restore rehearsal. This script deliberately
# has no option for selecting an existing Compose project, volume, or database.

usage() {
  echo "Usage: $0 [new-output-directory]" >&2
  echo "Creates disposable Docker volumes and leaves synthetic backup evidence in a new directory." >&2
}

if [[ $# -gt 1 || ${1:-} == --help ]]; then
  usage
  [[ ${1:-} == --help ]] && exit 0
  exit 2
fi

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
command -v cmp >/dev/null || { echo "cmp is required" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker daemon is unavailable" >&2; exit 1; }

pg_image="${DRILL_POSTGRES_IMAGE:-postgres:16-alpine@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416}"
docker image inspect "$pg_image" >/dev/null 2>&1 || {
  echo "Image $pg_image is not local; pull it explicitly before the drill" >&2
  exit 1
}

if [[ $# -eq 1 ]]; then
  output_dir="$1"
  [[ ! -e $output_dir ]] || { echo "Output path already exists: $output_dir" >&2; exit 1; }
  mkdir -m 700 -- "$output_dir"
else
  output_dir="$(mktemp -d "${TMPDIR:-/tmp}/nextcloud-weknora-restore.XXXXXX")"
fi

run_id="$(date -u +%Y%m%d%H%M%S)-$$-${RANDOM}"
prefix="nw-restore-drill-${run_id}"
label_key="org.weknora.isolated_restore_drill"
created_volumes=()
created_containers=()
started_at="$(date +%s)"

cleanup() {
  result=$?
  trap - EXIT
  set +e
  for container in "${created_containers[@]}"; do
    if [[ $(docker inspect -f "{{ index .Config.Labels \"$label_key\" }}" "$container" 2>/dev/null) == "$run_id" ]]; then
      docker rm -f "$container" >/dev/null 2>&1
    fi
  done
  for volume in "${created_volumes[@]}"; do
    if [[ $(docker volume inspect -f "{{ index .Labels \"$label_key\" }}" "$volume" 2>/dev/null) == "$run_id" ]]; then
      docker volume rm "$volume" >/dev/null 2>&1
    fi
  done
  if [[ $result -ne 0 ]]; then
    echo "Drill failed. Synthetic evidence remains at: $output_dir" >&2
  fi
  exit "$result"
}
trap cleanup EXIT

create_volume() {
  local name="$prefix-$1"
  if docker volume inspect "$name" >/dev/null 2>&1; then
    echo "Refusing to reuse existing volume: $name" >&2
    return 1
  fi
  docker volume create --label "$label_key=$run_id" "$name" >/dev/null
  if [[ $(docker volume inspect -f "{{ index .Labels \"$label_key\" }}" "$name") != "$run_id" ]]; then
    echo "Created volume label verification failed: $name" >&2
    return 1
  fi
  created_volumes+=("$name")
}

start_database() {
  local name="$prefix-$1" volume="$2"
  if docker container inspect "$name" >/dev/null 2>&1; then
    echo "Refusing to reuse existing container: $name" >&2
    return 1
  fi
  docker run -d --pull never --network none --name "$name" \
    --label "$label_key=$run_id" \
    --mount "type=volume,source=$volume,target=/var/lib/postgresql/data,volume-nocopy" \
    -e POSTGRES_DB=postgres -e POSTGRES_USER=drill \
    -e POSTGRES_PASSWORD=synthetic-drill-only "$pg_image" >/dev/null
  created_containers+=("$name")
  local attempt
  for attempt in {1..60}; do
    if docker exec "$name" pg_isready -U drill -d postgres >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Synthetic PostgreSQL did not start: $name" >&2
  return 1
}

write_fixture() {
  local volume="$1" role="$2"
  docker run --rm --pull never --network none \
    --mount "type=volume,source=$volume,target=/payload,volume-nocopy" \
    --entrypoint sh "$pg_image" -ceu '
      case "$1" in
        nextcloud)
          mkdir -p /payload/config /payload/data/admin/files /payload/custom_apps/integration_weknora
          printf "%s\n" "synthetic-instance-uuid=isolated-drill" > /payload/config/config.php
          printf "%s\n" "synthetic published source" > /payload/data/admin/files/published.txt
          printf "%s\n" "synthetic withdrawn source" > /payload/data/admin/files/withdrawn.txt
          printf "%s\n" "integration_weknora synthetic plugin marker" > /payload/custom_apps/integration_weknora/state.txt
          ;;
        weknora-files)
          mkdir -p /payload/tenant-7/originals
          printf "%s\n" "synthetic imported original" > /payload/tenant-7/originals/imported.txt
          ;;
        weknora-index)
          mkdir -p /payload/index
          printf "%s\n" "synthetic index checkpoint; no real vector extension" > /payload/index/checkpoint.txt
          ;;
        weknora-key)
          mkdir -p /payload/keys
          printf "%s\n" "synthetic-key-not-a-real-secret" > /payload/keys/system-aes-key
          ;;
        *) exit 2 ;;
      esac
    ' sh "$role"
}

file_manifest() {
  local volume="$1" destination="$2"
  docker run --rm --pull never --network none \
    --mount "type=volume,source=$volume,target=/payload,readonly" \
    --entrypoint sh "$pg_image" -ceu '
      cd /payload
      find . -type f -print | sort | while IFS= read -r file; do sha256sum "$file"; done
    ' > "$destination"
}

archive_volume() {
  local volume="$1" archive="$2"
  docker run --rm --pull never --network none \
    --mount "type=volume,source=$volume,target=/payload,readonly" \
    --entrypoint tar "$pg_image" -C /payload -cf - . > "$archive"
}

restore_volume() {
  local volume="$1" archive="$2"
  docker run --rm -i --pull never --network none \
    --mount "type=volume,source=$volume,target=/payload,volume-nocopy" \
    --entrypoint tar "$pg_image" -C /payload -xf - < "$archive"
}

db_source_volume="$prefix-db-source"
db_restored_volume="$prefix-db-restored"
create_volume db-source
create_volume db-restored
for role in nextcloud weknora-files weknora-index weknora-key; do
  create_volume "$role-source"
  create_volume "$role-restored"
done

db_source="$prefix-postgres-source"
db_restored="$prefix-postgres-restored"
start_database postgres-source "$db_source_volume"
docker exec "$db_source" createdb -U drill nextcloud
docker exec "$db_source" createdb -U drill weknora
docker exec "$db_source" psql -U drill -d nextcloud -v ON_ERROR_STOP=1 -c \
  "CREATE TABLE drill_state (name text PRIMARY KEY, value text NOT NULL); INSERT INTO drill_state VALUES ('instance_uuid','synthetic-instance-uuid'),('published_file','42'),('withdrawn_file','43'),('plugin_binding','department-a');" >/dev/null
docker exec "$db_source" psql -U drill -d weknora -v ON_ERROR_STOP=1 -c \
  "CREATE TABLE drill_state (name text PRIMARY KEY, value text NOT NULL); INSERT INTO drill_state VALUES ('data_source_id','synthetic-nextcloud'),('publication_state','closed-for-restore'),('withdrawn_tombstone','file-43'),('index_checkpoint','synthetic-1');" >/dev/null

for db in nextcloud weknora; do
  docker exec "$db_source" pg_dump -U drill -Fc -d "$db" > "$output_dir/$db.dump"
  docker exec "$db_source" psql -U drill -d "$db" -Atqc \
    "SELECT name || '=' || value FROM drill_state ORDER BY name" > "$output_dir/$db.expected.txt"
done

for role in nextcloud weknora-files weknora-index weknora-key; do
  source_volume="$prefix-$role-source"
  restored_volume="$prefix-$role-restored"
  write_fixture "$source_volume" "$role"
  file_manifest "$source_volume" "$output_dir/$role.expected.sha256"
  archive_volume "$source_volume" "$output_dir/$role.tar"
  restore_volume "$restored_volume" "$output_dir/$role.tar"
  file_manifest "$restored_volume" "$output_dir/$role.restored.sha256"
  cmp "$output_dir/$role.expected.sha256" "$output_dir/$role.restored.sha256"
done

start_database postgres-restored "$db_restored_volume"
for db in nextcloud weknora; do
  docker exec "$db_restored" createdb -U drill "$db"
  docker exec -i "$db_restored" pg_restore -U drill --no-owner --no-privileges \
    -d "$db" < "$output_dir/$db.dump"
  docker exec "$db_restored" psql -U drill -d "$db" -Atqc \
    "SELECT name || '=' || value FROM drill_state ORDER BY name" > "$output_dir/$db.restored.txt"
  cmp "$output_dir/$db.expected.txt" "$output_dir/$db.restored.txt"
done

if command -v shasum >/dev/null 2>&1; then
  (cd "$output_dir" && shasum -a 256 -- *.dump *.tar) > "$output_dir/archive.sha256"
else
  (cd "$output_dir" && sha256sum -- *.dump *.tar) > "$output_dir/archive.sha256"
fi

elapsed=$(( $(date +%s) - started_at ))
cat > "$output_dir/result.txt" <<EOF
status=PASS
scope=synthetic-data-only
nextcloud_database=verified
weknora_database=verified
nextcloud_files_and_config=verified
weknora_originals_index_and_key_fixture=verified
source_or_running_compose_volumes_used=no
application_boot_or_authorization_replay=not-tested
elapsed_seconds=$elapsed
EOF
echo "Synthetic isolated data restore PASS ($elapsed seconds). Evidence: $output_dir"
