#!/usr/bin/env sh
# One task-owned or operator-provided Nextcloud installation. No implicit host selection.
set -eu
: "${NEXTCLOUD_OCC_PATH:?Set NEXTCLOUD_OCC_PATH to the intended installation occ file}"
WEKNORA_DISCOVERY_INTERVAL_SECONDS=${WEKNORA_DISCOVERY_INTERVAL_SECONDS:-5}
case "$WEKNORA_DISCOVERY_INTERVAL_SECONDS" in
    ''|*[!0-9]*) echo 'Discovery interval must be a positive integer' >&2; exit 2 ;;
esac
if [ "$WEKNORA_DISCOVERY_INTERVAL_SECONDS" -lt 1 ]; then
    echo 'Discovery interval must be at least one second' >&2
    exit 2
fi
if [ ! -f "$NEXTCLOUD_OCC_PATH" ]; then
    echo 'Configured occ path does not exist' >&2
    exit 2
fi
trap 'exit 0' INT TERM
while :; do
    # Errors keep the durable queue and retry history; no target is silently replaced.
    php "$NEXTCLOUD_OCC_PATH" integration_weknora:discover-directories || true
    sleep "$WEKNORA_DISCOVERY_INTERVAL_SECONDS"
done
