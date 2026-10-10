#!/usr/bin/env python3
"""Basic signed Nextcloud source API check against the local Compose stack."""

import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "apps/integration_weknora/tests"))
from machine_auth import signed_headers


def env_values():
    result = {}
    for line in (PROJECT / ".env").read_text().splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value.strip().strip('"').strip("'")
    return result


def request(url, token=None, extra=None):
    headers = {} if token is None else {"Authorization": "Bearer " + token}
    headers.update(extra or {})
    req = urllib.request.Request(url, headers=signed_headers("GET", url, headers), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def main():
    env = env_values()
    api = (f"http://127.0.0.1:{env.get('NEXTCLOUD_HTTP_PORT', '18082')}"
           "/index.php/apps/integration_weknora/api/v1")
    token = env["WEKNORA_SERVICE_TOKEN"]
    assert request(f"{api}/capabilities")[0] == 401
    status, body = request(f"{api}/capabilities", token)
    assert status == 200 and json.loads(body)["protocol_version"] == "1"
    status, body = request(f"{api}/bindings/dev-published/manifest", token)
    assert status == 200, (status, body[:300])
    manifest = json.loads(body)
    files = [item for item in manifest["items"] if item["name"] == "department-notes.md"]
    assert manifest["complete"] and len(files) == 1
    file_id = files[0]["file_id"]
    content_url = f"{api}/bindings/dev-published/files/{file_id}/content"
    status, body = request(content_url, token)
    assert status == 200 and body == (PROJECT / "fixtures/department-notes.md").read_bytes()
    status, _ = request(content_url, token, {"If-Match": '"wrong-version"'})
    assert status == 412
    print(f"Nextcloud API smoke passed (binding dev-published, file ID {file_id}).")


if __name__ == "__main__":
    main()
