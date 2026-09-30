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
build_dir="$(mktemp -d "$workspace_dir/.weknora-rag-build.XXXXXXXX")"
trap 'rm -rf -- "$build_dir"' EXIT
git -C "$source_dir" archive --format=tar "$base_commit" | tar -xf - -C "$build_dir"
git -C "$build_dir" apply --check "$patch_file"
git -C "$build_dir" apply "$patch_file"
patch_sha="$(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$patch_file")"
revision="${base_commit}+nextcloud.${patch_sha:0:12}"
version="$(cat "$build_dir/VERSION")"
source_label="org.opencontainers.image.revision=$base_commit"
patch_label="io.github.liugang926.weknora.nextcloud-patch-sha256=$patch_sha"

# The fast path overlays the patched binary/assets on this host's existing
# runtime images. A fresh developer host instead uses WeKnora's Dockerfiles
# from the same patched source archive, with the external docreader service.
fast_path=true
for image in weknora-go-test:1.26-sqlite \
  weknora-ldap-app:rag-pdf-builtin-59671fac weknora-ldap-ui:rag-pr; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    fast_path=false
  fi
done
runtime_arch="$(docker version --format '{{.Server.Arch}}')"
if [[ "$fast_path" == true ]]; then
  for image in weknora-go-test:1.26-sqlite \
    weknora-ldap-app:rag-pdf-builtin-59671fac weknora-ldap-ui:rag-pr; do
    if [[ "$(docker image inspect "$image" --format '{{.Architecture}}')" != "$runtime_arch" ]]; then
      fast_path=false
    fi
  done
fi
if [[ "$fast_path" != true ]]; then
  docker build --build-arg WITH_ANYDOC=0 \
    --build-arg "VERSION_ARG=$version" --build-arg "COMMIT_ID_ARG=$revision" \
    --label "$source_label" --label "$patch_label" \
    -f "$build_dir/docker/Dockerfile.app" \
    -t weknora-ldap-app:nextcloud-rag "$build_dir"
  docker build --build-arg "VITE_FRONTEND_COMMIT=$revision" \
    --label "$source_label" --label "$patch_label" \
    -f "$build_dir/frontend/Dockerfile" \
    -t weknora-ldap-ui:nextcloud-rag "$build_dir/frontend"
  echo 'Built patched WeKnora RAG app and UI from source.'
  exit 0
fi

docker run --rm --pull=never \
  -e "VERSION=$version" -e "COMMIT_ID=$revision" \
  -v "$build_dir:/src" \
  -v weknora-rag-ci-gomod:/go/pkg/mod \
  -v weknora-rag-ci-gocache:/root/.cache/go-build \
  -w /src weknora-go-test:1.26-sqlite make build-prod
cp "$build_dir/WeKnora" "$build_dir/nextcloud-integration-bin"
docker build --build-arg "WEKNORA_SOURCE_COMMIT=$base_commit" \
  --build-arg "WEKNORA_PATCH_SHA=$patch_sha" \
  --build-arg "WEKNORA_REVISION=$revision" \
  -f "$project_dir/integration/Dockerfile.weknora-rag" \
  -t weknora-ldap-app:nextcloud-rag "$build_dir"

docker run --rm \
  -e "VITE_FRONTEND_COMMIT=$revision" \
  -e NODE_OPTIONS=--max-old-space-size=4096 \
  -v "$build_dir/frontend:/src" -w /src \
  node:24-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e \
  sh -c 'npm ci --no-audit --no-fund && VITE_IS_DOCKER=true npm run build'
mkdir "$build_dir/runtime-ui"
cp -R "$build_dir/frontend/dist" "$build_dir/runtime-ui/dist"
docker build --label "$source_label" --label "$patch_label" \
  -f "$project_dir/integration/Dockerfile.weknora-rag-frontend" \
  -t weknora-ldap-ui:nextcloud-rag "$build_dir/runtime-ui"

echo 'Built weknora-ldap-app:nextcloud-rag and weknora-ldap-ui:nextcloud-rag.'
