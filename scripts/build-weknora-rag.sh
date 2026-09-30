#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$project_dir/.." && pwd)"
source_dir="$workspace_dir/WeKnora-ldap-ad"
patch_file="$project_dir/integration/weknora-rag-b8a34e0b.patch"
base_commit="b8a34e0bae8fcf0d3c8273bba2e56414abed41e2"
expected_patch_sha="1bbeb4989ac21afb8c70754064f64aed5eb1d1d918ef9601b5afbe0728c6a1c8"

# An alternate suffix lets an operator verify a candidate without replacing
# the image tags used by the shared local WeKnora stack.
tag_suffix="${WEKNORA_RAG_TAG_SUFFIX:-nextcloud-rag}"
if [[ ! "$tag_suffix" =~ ^[a-z0-9][a-z0-9_.-]*$ || ${#tag_suffix} -gt 128 ]]; then
  echo "Invalid WEKNORA_RAG_TAG_SUFFIX: use 1-128 lowercase tag characters" >&2
  exit 1
fi
app_image="weknora-ldap-app:$tag_suffix"
ui_image="weknora-ldap-ui:$tag_suffix"

if ! git -C "$source_dir" cat-file -e "$base_commit^{commit}"; then
  echo "WeKnora RAG baseline commit $base_commit is unavailable" >&2
  exit 1
fi
if [[ ! -s "$patch_file" ]]; then
  echo "WeKnora RAG patch is missing" >&2
  exit 1
fi
patch_sha="$(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$patch_file")"
if [[ "$patch_sha" != "$expected_patch_sha" ]]; then
  echo "WeKnora RAG patch SHA-256 mismatch: expected $expected_patch_sha, got $patch_sha" >&2
  exit 1
fi

build_dir="$(mktemp -d "$workspace_dir/.weknora-rag-build.XXXXXXXX")"
trap 'rm -rf -- "$build_dir"' EXIT
git -C "$source_dir" archive --format=tar "$base_commit" | tar -xf - -C "$build_dir"
git -C "$build_dir" apply --check "$patch_file"
git -C "$build_dir" apply "$patch_file"

revision="${base_commit}+nextcloud.${patch_sha:0:12}"
version="$(cat "$build_dir/VERSION")"
source_label="org.opencontainers.image.revision=$base_commit"
patch_label="io.github.liugang926.weknora.nextcloud-patch-sha256=$patch_sha"

# The pinned source Dockerfile builds the Rust anydoc library and links the
# Go backend with GO_BUILD_TAGS=anydoc when WITH_ANYDOC=1.
build_args=(--build-arg WITH_ANYDOC=1)
if [[ -n "${WEKNORA_RAG_GOPROXY:-}" ]]; then
  build_args+=(--build-arg "GOPROXY_ARG=$WEKNORA_RAG_GOPROXY")
fi
if [[ -n "${WEKNORA_RAG_APT_MIRROR:-}" ]]; then
  build_args+=(--build-arg "APK_MIRROR_ARG=$WEKNORA_RAG_APT_MIRROR")
fi
if [[ -n "${WEKNORA_RAG_CARGO_REGISTRY_MIRROR:-}" ]]; then
  build_args+=(--build-arg "CARGO_REGISTRY_MIRROR_ARG=$WEKNORA_RAG_CARGO_REGISTRY_MIRROR")
fi
if [[ -n "${WEKNORA_RAG_NPM_REGISTRY:-}" ]]; then
  build_args+=(--build-arg "NPM_REGISTRY_ARG=$WEKNORA_RAG_NPM_REGISTRY")
fi
if [[ -n "${WEKNORA_RAG_RUSTUP_DIST_SERVER:-}" ]]; then
  build_args+=(--build-arg "RUSTUP_DIST_SERVER_ARG=$WEKNORA_RAG_RUSTUP_DIST_SERVER")
fi
if [[ -n "${WEKNORA_RAG_RUSTUP_UPDATE_ROOT:-}" ]]; then
  build_args+=(--build-arg "RUSTUP_UPDATE_ROOT_ARG=$WEKNORA_RAG_RUSTUP_UPDATE_ROOT")
fi
docker build "${build_args[@]}" \
  --build-arg "VERSION_ARG=$version" --build-arg "COMMIT_ID_ARG=$revision" \
  --label "$source_label" --label "$patch_label" \
  -f "$build_dir/docker/Dockerfile.app" \
  -t "$app_image" "$build_dir"
ui_build_args=(--build-arg "VITE_FRONTEND_COMMIT=$revision")
if [[ -n "${WEKNORA_RAG_NPM_REGISTRY:-}" ]]; then
  ui_build_args+=(--build-arg "NPM_REGISTRY=$WEKNORA_RAG_NPM_REGISTRY")
fi
docker build "${ui_build_args[@]}" \
  --label "$source_label" --label "$patch_label" \
  -f "$build_dir/frontend/Dockerfile" \
  -t "$ui_image" "$build_dir/frontend"

echo "Built $app_image and $ui_image from pinned source with anydoc enabled."
