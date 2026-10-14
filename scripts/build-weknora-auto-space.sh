#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --verify-only && $# == 1 ]]; then
  source_dir="${WEKNORA_RAG_SOURCE_DIR:-$(dirname "$project_dir")/WeKnora-ldap-ad}"
  exec python3 "$project_dir/scripts/ops/verify-weknora-candidate.py" \
    --profile auto_space --source-repo "$source_dir"
fi
if (( $# != 0 )); then
  echo 'Usage: build-weknora-auto-space.sh [--verify-only]' >&2
  exit 2
fi
export WEKNORA_RAG_CANDIDATE=1
export WEKNORA_RAG_CANDIDATE_PROFILE=auto_space
exec bash "$project_dir/scripts/build-weknora-rag.sh" "$@"
