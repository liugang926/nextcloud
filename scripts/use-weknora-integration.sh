#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$project_dir/.." && pwd)"
compose=(docker compose -f "$workspace_dir/weknora-ldap-local/compose.yaml" -f "$project_dir/integration/weknora.override.yaml" -f "$project_dir/integration/weknora.dev-http.yaml")

"${compose[@]}" up -d --no-deps --wait app
"${compose[@]}" ps app
