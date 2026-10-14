#!/usr/bin/env sh
set -eu
: "${WEKNORA_PRD_TEST_RUNTIME:?Set WEKNORA_PRD_TEST_RUNTIME=1 for the disposable fixture}"
[ "$WEKNORA_PRD_TEST_RUNTIME" = 1 ] || exit 2
WEKNORA_PRD_AD_CONTAINER=${WEKNORA_PRD_AD_CONTAINER:-weknora-auto-space-ad-20261010}
NEXTCLOUD_PRD_TEST_CONTAINER=${NEXTCLOUD_PRD_TEST_CONTAINER:-nextcloud-auto-space-prd-test-20261010}
WEKNORA_PRD_AD_IMAGE=${WEKNORA_PRD_AD_IMAGE:-weknora-auto-space-ad-test:20261010}
WEKNORA_PRD_NETWORK=${WEKNORA_PRD_NETWORK:-nextcloud-auto-space-prd-test-20261010}
WEKNORA_PRD_FIXTURE_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if [ "$(docker inspect --format '{{index .Config.Labels "codex.task"}}' "$NEXTCLOUD_PRD_TEST_CONTAINER")" != auto-space-prd-20261010 ]; then
    echo 'Refusing an unowned Nextcloud container' >&2; exit 2
fi
if docker container inspect "$WEKNORA_PRD_AD_CONTAINER" >/dev/null 2>&1; then
    echo 'AD fixture already exists; reuse it without provisioning or rotating TLS' >&2; exit 2
fi
# This script deliberately never builds/downloads an image or publishes a host port.
docker image inspect "$WEKNORA_PRD_AD_IMAGE" >/dev/null
docker run -d --name "$WEKNORA_PRD_AD_CONTAINER" --label codex.task=auto-space-prd-20261010 \
    --hostname prd-ad --network "$WEKNORA_PRD_NETWORK" --network-alias prd-ad \
    -e WEKNORA_PRD_TEST_RUNTIME=1 -v "$WEKNORA_PRD_FIXTURE_DIR:/fixtures:ro" \
    "$WEKNORA_PRD_AD_IMAGE" bash -c 'python3 /fixtures/provision.py && exec bash /fixtures/start.sh'
