#!/usr/bin/env bash
set -euo pipefail
# Run as the same OS user as Nextcloud. Claims are durable and expire in 60s.
: "${NEXTCLOUD_OCC_PATH:?Set the absolute path to the intended Nextcloud occ}"
[[ "$NEXTCLOUD_OCC_PATH" = /* && -f "$NEXTCLOUD_OCC_PATH" ]] || { printf 'Invalid Nextcloud occ path\n' >&2; exit 2; }
task_workers="${WEKNORA_STATUS_WORKERS:-4}"
[[ "$task_workers" =~ ^[1-8]$ ]] || { printf 'Worker count must be between 1 and 8\n' >&2; exit 2; }
task_pids=()
trap 'kill "${task_pids[@]}" 2>/dev/null || true; wait || true' EXIT INT TERM
for ((task_index=0; task_index<task_workers; task_index++)); do
    (
        task_child=''
        trap 'if [[ -n "$task_child" ]]; then kill "$task_child" 2>/dev/null || true; wait "$task_child" 2>/dev/null || true; fi; exit' INT TERM
        while true; do
            php "$NEXTCLOUD_OCC_PATH" integration_weknora:poll-stage-status &
            task_child=$!
            wait "$task_child" || true
            task_child=''
            sleep 5 &
            task_child=$!
            wait "$task_child" || true
            task_child=''
        done
    ) &
    task_pids+=("$!")
done
wait
