#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$project_dir/.." && pwd)"
source_dir="$workspace_dir/WeKnora-ldap-ad"
patch_file="$project_dir/integration/weknora.patch"
dockerfile="$project_dir/integration/Dockerfile.weknora"
# Fixed submission/feat/ldap-ad-group-permissions baseline for this patch.
base_commit="c6c4bd445a8ee49e742da9d804957a3fe4bf52d4"

if [[ ! -d "$source_dir/.git" && ! -f "$source_dir/.git" ]]; then
  echo "WeKnora source was not found at $source_dir" >&2
  exit 1
fi
if ! git -C "$source_dir" cat-file -e "$base_commit^{commit}"; then
  echo "WeKnora baseline commit $base_commit is unavailable in $source_dir" >&2
  exit 1
fi
if [[ ! -s "$patch_file" || ! -f "$dockerfile" ]]; then
  echo 'WeKnora patch or local runtime Dockerfile is missing.' >&2
  exit 1
fi

# Archive committed files into a disposable build context. This works even if
# the adjacent WeKnora checkout has uncommitted integration changes and never
# alters or stages that checkout.
build_dir="$(mktemp -d "$workspace_dir/.weknora-build.XXXXXXXX")"
trap 'rm -rf -- "$build_dir"' EXIT
git -C "$source_dir" archive --format=tar "$base_commit" | tar -xf - -C "$build_dir"
git -C "$build_dir" apply --check "$patch_file"
git -C "$build_dir" apply "$patch_file"

docker run --rm \
  -v "$build_dir:/src" \
  -v weknora-go-mod:/go/pkg/mod \
  -v weknora-go-build:/root/.cache/go-build \
  -w /src \
  weknora-go-test:1.26-sqlite \
  make build-prod

# Upstream's .dockerignore excludes its ordinary WeKnora build output. Stage
# the binary under a distinct name so Docker copies the artifact we just built.
cp "$build_dir/WeKnora" "$build_dir/nextcloud-integration-bin"

docker build \
  -f "$dockerfile" \
  -t weknora-ldap-app:nextcloud-integration \
  "$build_dir"

echo 'Built weknora-ldap-app:nextcloud-integration.'
echo 'To use it with the existing local WeKnora stack, run scripts/use-weknora-integration.sh.'
