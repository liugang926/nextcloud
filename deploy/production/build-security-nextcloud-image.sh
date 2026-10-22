#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:?GHCR image reference is required}"
DOCKERFILE_CONTEXT="${2:?Nextcloud Dockerfile context is required}"
EXPECTED_PHP="${3:?Expected PHP version is required}"
EXPECTED_NEXTCLOUD="${4:?Expected Nextcloud version is required}"
PUSH_IMAGE="${5:-true}"

verify_image() {
  local actual_php actual_nextcloud modules
  actual_php="$(docker run --rm --entrypoint php "$IMAGE" -r 'echo PHP_VERSION;')"
  actual_nextcloud="$(
    docker run --rm --entrypoint php "$IMAGE" -r '
      $OC_Version = array();
      $OC_VersionString = "";
      require "/usr/src/nextcloud/version.php";
      echo $OC_VersionString;
    '
  )"
  modules="$(docker run --rm --entrypoint php "$IMAGE" -m)"
  test "$actual_php" = "$EXPECTED_PHP"
  test "$actual_nextcloud" = "$EXPECTED_NEXTCLOUD"
  for module in apcu gd intl ldap memcached pdo_pgsql redis zip; do
    grep -Eiq "^${module}$" <<< "$modules"
  done
  printf 'verified_nextcloud=%s php=%s\n' "$actual_nextcloud" "$actual_php"
}

if docker pull --platform linux/amd64 "$IMAGE"; then
  verify_image
  exit 0
fi

# Build only when the immutable security image is not yet available. The
# floating PHP tag must already contain the exact patched runtime.
docker pull --platform linux/amd64 php:8.5-apache-trixie
base_php="$(
  docker run --rm --entrypoint php php:8.5-apache-trixie -r 'echo PHP_VERSION;'
)"
test "$base_php" = "$EXPECTED_PHP"

DOCKER_BUILDKIT=0 docker build \
  --platform linux/amd64 \
  --tag "$IMAGE" \
  "$DOCKERFILE_CONTEXT"

verify_image
if [[ "$PUSH_IMAGE" == true ]]; then
  docker push "$IMAGE"
  # Pull the pushed manifest so Docker records the immutable registry digest.
  docker pull --platform linux/amd64 "$IMAGE"
  verify_image
fi
