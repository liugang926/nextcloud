#!/usr/bin/env python3
"""Verify overlapping current/previous machine keys in the local Compose DB."""

import hashlib
import secrets
import subprocess
import urllib.error
import urllib.request

from machine_auth import signed_headers
from publication_http_smoke import PROJECT, load_env


def occ(*args):
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "-u", "www-data", "nextcloud",
         "php", "occ", *args], cwd=PROJECT, text=True, capture_output=True,
    )


def get_config(key):
    result = occ("config:app:get", "integration_weknora", key)
    if result.returncode:
        return None
    return result.stdout.strip()


def set_config(key, value):
    if value is None:
        result = occ("config:app:delete", "integration_weknora", key)
    else:
        result = occ("config:app:set", "integration_weknora", key, "--value=" + value)
    assert result.returncode == 0, result.stderr


def request(url, token, key_id):
    headers = signed_headers("GET", url, {"Authorization": "Bearer " + token}, key_id=key_id)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def main():
    env = load_env()
    url = (f"http://127.0.0.1:{env.get('NEXTCLOUD_HTTP_PORT', '18082')}"
           "/index.php/apps/integration_weknora/api/v1/capabilities")
    active_id = get_config("service_key_id") or "default"
    old_id = "rotation-smoke-" + secrets.token_hex(4)
    old_token = secrets.token_urlsafe(32)
    active_hash = get_config("service_token_sha256")
    assert active_hash and len(active_hash) == 64
    previous = {key: get_config(key) for key in (
        "service_previous_key_id", "service_previous_token_sha256")}
    try:
        set_config("service_previous_token_sha256", hashlib.sha256(old_token.encode()).hexdigest())
        set_config("service_previous_key_id", old_id)
        assert request(url, env["WEKNORA_SERVICE_TOKEN"], active_id) == 200
        assert request(url, old_token, old_id) == 200
        assert request(url, old_token, active_id) == 401
        assert request(url, env["WEKNORA_SERVICE_TOKEN"], old_id) == 401
        set_config("service_previous_key_id", None)
        assert request(url, old_token, old_id) == 401, "revoked previous key remained usable"
        assert request(url, env["WEKNORA_SERVICE_TOKEN"], active_id) == 200
        new_current_token = secrets.token_urlsafe(32)
        set_config("service_token_sha256", hashlib.sha256(new_current_token.encode()).hexdigest())
        assert request(url, env["WEKNORA_SERVICE_TOKEN"], active_id) == 401, "revoked active key remained usable"
        assert request(url, new_current_token, active_id) == 200
        set_config("service_token_sha256", active_hash)
        assert request(url, env["WEKNORA_SERVICE_TOKEN"], active_id) == 200
        print("machine key rotation HTTP smoke passed")
    finally:
        set_config("service_token_sha256", active_hash)
        set_config("service_previous_token_sha256", previous["service_previous_token_sha256"])
        set_config("service_previous_key_id", previous["service_previous_key_id"])


if __name__ == "__main__":
    main()
