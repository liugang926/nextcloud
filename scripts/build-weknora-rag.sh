#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$project_dir/.." && pwd)"
source_dir="$workspace_dir/WeKnora-ldap-ad"
patch_file="$project_dir/integration/weknora-rag-06792ba5.patch"
base_commit="06792ba5d88ed8fd0ddb9cf77f927c6cb9ae29fc"

if ! git -C "$source_dir" cat-file -e "$base_commit^{commit}"; then
  echo "WeKnora RAG baseline commit $base_commit is unavailable" >&2
  exit 1
fi
if [[ ! -s "$patch_file" ]]; then
  echo "WeKnora RAG patch is missing" >&2
  exit 1
fi
for image in weknora-go-test:1.26-sqlite \
  weknora-ldap-app:rag-pdf-builtin-59671fac weknora-ldap-ui:rag-pr; do
  docker image inspect "$image" >/dev/null
done

build_dir="$(mktemp -d "$workspace_dir/.weknora-rag-build.XXXXXXXX")"
trap 'rm -rf -- "$build_dir"' EXIT
git -C "$source_dir" archive --format=tar "$base_commit" | tar -xf - -C "$build_dir"
git -C "$build_dir" apply --check "$patch_file"
git -C "$build_dir" apply "$patch_file"

docker run --rm --pull=never \
  -v "$build_dir:/src" \
  -v weknora-rag-ci-gomod:/go/pkg/mod \
  -v weknora-rag-ci-gocache:/root/.cache/go-build \
  -w /src weknora-go-test:1.26-sqlite make build-prod
cp "$build_dir/WeKnora" "$build_dir/nextcloud-integration-bin"
docker build -f "$project_dir/integration/Dockerfile.weknora-rag" \
  -t weknora-ldap-app:nextcloud-rag "$build_dir"

(
  cd "$build_dir/frontend"
  npm ci --no-audit --no-fund
  VITE_IS_DOCKER=true npm run build
)
mkdir "$build_dir/runtime-ui"
cp -R "$build_dir/frontend/dist" "$build_dir/runtime-ui/dist"
docker build -f "$project_dir/integration/Dockerfile.weknora-rag-frontend" \
  -t weknora-ldap-ui:nextcloud-rag "$build_dir/runtime-ui"

echo 'Built weknora-ldap-app:nextcloud-rag and weknora-ldap-ui:nextcloud-rag.'
