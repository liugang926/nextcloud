#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$project_dir/.." && pwd)"
source_dir="$workspace_dir/WeKnora-ldap-ad"
patch_file="$project_dir/integration/weknora-rag-b6ea8b56.patch"
base_commit="b6ea8b560b886cef77fb32fb2e092123cdce4ed3"
expected_patch_sha="8bbffc98f1fbdc03f08be0e7f6d959da61536b17b1b75b8b3a4258b914e612ae"

# Explicit candidate builds use the separately verified manifest and a distinct
# default image tag. They never replace the default source patch selection.
candidate_mode="${WEKNORA_RAG_CANDIDATE:-0}"
if [[ "$candidate_mode" != 0 && "$candidate_mode" != 1 ]]; then
  echo 'WEKNORA_RAG_CANDIDATE must be 0 or 1.' >&2
  exit 1
fi
default_tag_suffix=nextcloud-rag
if [[ "$candidate_mode" == 1 ]]; then
  candidate_fields="$(python3 - "$project_dir" "$base_commit" <<'PY'
import json
from pathlib import Path
import re
import sys

project = Path(sys.argv[1])
entry = json.loads((project / 'integration/candidates/manifest.json').read_text())['profiles']['rag']
if (entry['base'] != sys.argv[2] or not re.fullmatch(r'[a-f0-9]{64}', entry['patch_sha256'])
        or any(not re.fullmatch(r'[a-f0-9]{40}', entry[field]) for field in ('candidate_tree', 'candidate_commit'))):
    raise SystemExit('Candidate baseline or SHA is invalid')
path = project / entry['path']
if path.resolve().parent != (project / 'integration/candidates').resolve():
    raise SystemExit('Candidate patch must be inside integration/candidates')
print(path)
print(entry['patch_sha256'])
print(entry['candidate_tree'])
print(entry['candidate_commit'])
PY
)"
  patch_file="${candidate_fields%%$'\n'*}"
  candidate_remainder="${candidate_fields#*$'\n'}"
  expected_patch_sha="${candidate_remainder%%$'\n'*}"
  candidate_remainder="${candidate_remainder#*$'\n'}"
  candidate_tree="${candidate_remainder%%$'\n'*}"
  candidate_commit="${candidate_remainder#*$'\n'}"
  default_tag_suffix=nextcloud-rag-candidate
fi

# An alternate suffix lets an operator verify a candidate without replacing
# the image tags used by the shared local WeKnora stack.
tag_suffix="${WEKNORA_RAG_TAG_SUFFIX:-$default_tag_suffix}"
if [[ ! "$tag_suffix" =~ ^[a-z0-9][a-z0-9_.-]*$ || ${#tag_suffix} -gt 128 ]]; then
  echo "Invalid WEKNORA_RAG_TAG_SUFFIX: use 1-128 lowercase tag characters" >&2
  exit 1
fi
app_image="weknora-ldap-app:$tag_suffix"
ui_image="weknora-ldap-ui:$tag_suffix"

min_free_gib="${WEKNORA_RAG_MIN_FREE_GIB-16}"
if [[ ! "$min_free_gib" =~ ^[1-9][0-9]{0,3}$ ]] || (( min_free_gib > 1024 )); then
  echo "Invalid WEKNORA_RAG_MIN_FREE_GIB: use an integer from 1 to 1024" >&2
  exit 1
fi

# The source archive and Docker Desktop's sparse disk image can both grow
# substantially during a cold AnyDoc build. Check their host filesystems before
# creating the archive or starting either Docker build.
python3 - "$workspace_dir" "$min_free_gib" <<'PY'
import os
from pathlib import Path
import sys

workspace = Path(sys.argv[1])
minimum = int(sys.argv[2]) * 1024**3
locations = [workspace]
docker_raw = Path.home() / "Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw"
if docker_raw.is_file():
    locations.append(docker_raw.parent)

checked_devices = set()
for location in locations:
    device = location.stat().st_dev
    if device in checked_devices:
        continue
    checked_devices.add(device)
    filesystem = os.statvfs(location)
    available = filesystem.f_bavail * filesystem.f_frsize
    if available < minimum:
        print(
            f"WeKnora RAG build needs at least {minimum / 1024**3:g} GiB free "
            f"on the host filesystem containing {location}; "
            f"only {available / 1024**3:.1f} GiB is available. "
            "Free space or explicitly set WEKNORA_RAG_MIN_FREE_GIB after "
            "checking capacity.",
            file=sys.stderr,
        )
        sys.exit(1)
PY

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
if [[ "$candidate_mode" == 1 ]]; then
  python3 "$project_dir/scripts/ops/verify-weknora-candidate.py" \
    --profile=rag --source-repo="$source_dir"
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
source_labels=(--label "$source_label" --label "$patch_label")
app_role_labels=()
ui_role_labels=()
if [[ "$candidate_mode" == 1 ]]; then
  source_labels+=(--label "io.github.liugang926.weknora.candidate-tree=$candidate_tree"
                  --label "io.github.liugang926.weknora.candidate-commit=$candidate_commit")
  app_role_labels+=(--label "io.github.liugang926.weknora.candidate-role=app")
  ui_role_labels+=(--label "io.github.liugang926.weknora.candidate-role=ui")
fi
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
  "${source_labels[@]}" "${app_role_labels[@]}" \
  -f "$build_dir/docker/Dockerfile.app" \
  -t "$app_image" "$build_dir"
ui_build_args=(--build-arg "VITE_FRONTEND_COMMIT=$revision")
if [[ -n "${WEKNORA_RAG_NPM_REGISTRY:-}" ]]; then
  ui_build_args+=(--build-arg "NPM_REGISTRY=$WEKNORA_RAG_NPM_REGISTRY")
fi
docker build "${ui_build_args[@]}" \
  "${source_labels[@]}" "${ui_role_labels[@]}" \
  -f "$build_dir/frontend/Dockerfile" \
  -t "$ui_image" "$build_dir/frontend"

echo "Built $app_image and $ui_image from pinned source with anydoc enabled."
