#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if [[ ! -f .env ]]; then
  python3 - <<'PY'
from pathlib import Path
from secrets import token_urlsafe

content = "\n".join([
    "NEXTCLOUD_ADMIN_USER=devadmin",
    f"NEXTCLOUD_ADMIN_PASSWORD=x{token_urlsafe(24)}",
    f"NEXTCLOUD_DB_PASSWORD=x{token_urlsafe(24)}",
    f"WEKNORA_SERVICE_TOKEN={token_urlsafe(32)}",
    "NEXTCLOUD_HTTP_BIND_IP=127.0.0.1",
    "NEXTCLOUD_HTTP_PORT=18082",
    "",
])
path = Path('.env')
path.write_text(content)
path.chmod(0o600)
PY
  echo 'Created local .env with generated credentials.'
fi

docker compose up -d --wait --wait-timeout 600
echo 'Nextcloud is ready. Run scripts/bootstrap.sh to install the integration app and sample binding.'
