#!/usr/bin/env python3
"""Exercise publication controls against the local Nextcloud Compose stack.

Run after scripts/bootstrap.sh, for example:
  python3 apps/integration_weknora/tests/publication_http_smoke.py --file-id 77
"""

import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from machine_auth import signed_headers


PROJECT = Path(__file__).resolve().parents[3]


def load_env():
    values = {}
    for line in (PROJECT / ".env").read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


def request(opener, url, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, data=data,
                                 headers=signed_headers(method, url, headers or {}, data),
                                 method=method)
    try:
        with opener.open(req, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def login(base, user, password):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    status, body = request(opener, f"{base}/login")
    assert status == 200, (status, body[:200])
    token = re.search(rb'data-requesttoken="([^"]+)"', body).group(1).decode()
    payload = urllib.parse.urlencode({"requesttoken": token, "user": user, "password": password}).encode()
    status, _ = request(opener, f"{base}/login", "POST", {"Origin": base}, payload)
    assert status == 200, f"login failed for {user}: {status}"
    status, body = request(opener, f"{base}/apps/dashboard/")
    assert status == 200, (status, body[:200])
    token = re.search(rb'data-requesttoken="([^"]+)"', body).group(1).decode()
    return opener, token


def check(status, expected, message):
    assert status == expected, f"{message}: expected {expected}, got {status}"


def issue_machine_key(admin, api, binding_id, csrf):
    """Issue a temporary binding-scoped credential without printing its secret."""
    key_id = "smoke-" + secrets.token_hex(12)
    url = f"{api}/admin/bindings/{binding_id}/keys"
    status, body = request(
        admin, url, "POST",
        {"requesttoken": csrf, "Content-Type": "application/json"},
        json.dumps({"key_id": key_id}).encode(),
    )
    check(status, 201, "issue binding machine key")
    issued = json.loads(body)
    assert issued["binding_id"] == binding_id and issued["key_id"] == key_id
    assert isinstance(issued.get("token"), str) and issued["token"]
    return {"Authorization": "Bearer " + issued["token"],
            "X-WeKnora-Key-Id": key_id}, key_id


def revoke_machine_key(admin, api, binding_id, key_id, csrf):
    status, _ = request(
        admin, f"{api}/admin/bindings/{binding_id}/keys/{key_id}",
        "DELETE", {"requesttoken": csrf},
    )
    check(status, 200, "revoke binding machine key")


def remove_binding(admin, api, binding_id, csrf):
    status, body = request(
        admin, f"{api}/admin/bindings/{binding_id}",
        "DELETE", {"requesttoken": csrf},
    )
    check(status, 200, "remove synthetic binding")
    assert json.loads(body)["removed"] is True


def run_occ(*args, env=None):
    command = ["docker", "compose", "exec", "-T", "-u", "www-data"]
    if env is not None and "NC_PASS" in env:
        command += ["-e", "NC_PASS"]
    command += ["nextcloud", "php", "occ", *args]
    subprocess.run(command,
                   cwd=PROJECT, env=env, check=True, stdout=subprocess.DEVNULL)


def audit_actions(binding, file_id):
    query = ("SELECT action FROM oc_weknora_pub_audit "
             f"WHERE binding_id = '{binding}' AND file_id = {file_id} ORDER BY id DESC LIMIT 2")
    result = subprocess.run(["docker", "compose", "exec", "-T", "db", "psql", "-U", "nextcloud",
                             "-d", "nextcloud", "-Atc", query], cwd=PROJECT, check=True,
                            text=True, stdout=subprocess.PIPE)
    return result.stdout.splitlines()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding", default="dev-published")
    parser.add_argument("--file-id", type=int, required=True)
    args = parser.parse_args()
    assert re.fullmatch(r"[A-Za-z0-9_-]+", args.binding) and args.file_id > 0
    values = load_env()
    base = f"http://127.0.0.1:{values.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    api = f"{base}/index.php/apps/integration_weknora/api/v1"
    file_api = f"{api}/admin/bindings/{args.binding}/files/{args.file_id}"
    reader = urllib.request.build_opener()
    bearer = {"Authorization": f"Bearer {values['WEKNORA_SERVICE_TOKEN']}"}
    admin, token = login(base, values["NEXTCLOUD_ADMIN_USER"], values["NEXTCLOUD_ADMIN_PASSWORD"])
    admin_headers = {"requesttoken": token}

    status, body = request(admin, f"{file_api}/publication", headers=admin_headers)
    check(status, 200, "admin state")
    original = json.loads(body)["state"]
    user_id = f"weknora_permission_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(24)
    test_env = dict(os.environ, NC_PASS=password)
    created = False
    try:
        run_occ("user:add", "--password-from-env", "--no-interaction", user_id, env=test_env)
        created = True
        normal, normal_token = login(base, user_id, password)
        status, _ = request(normal, f"{file_api}/publication", headers={"requesttoken": normal_token})
        check(status, 403, "non-admin state")
        status, _ = request(normal, f"{file_api}/republish", "POST", {"requesttoken": normal_token}, b"")
        check(status, 403, "non-admin republish")
        status, _ = request(normal, f"{api}/admin/bindings", headers={"requesttoken": normal_token})
        check(status, 403, "non-admin binding list")

        status, _ = request(admin, f"{file_api}/republish", "POST", data=b"")
        check(status, 412, "missing CSRF token")
        status, body = request(admin, f"{file_api}/withdraw", "POST", admin_headers, b"")
        check(status, 200, "withdraw")
        assert json.loads(body)["excluded"] is True
        status, body = request(reader, f"{api}/bindings/{args.binding}/manifest", headers=bearer)
        check(status, 200, "manifest after withdrawal")
        assert args.file_id not in [item["file_id"] for item in json.loads(body)["items"]]
        status, _ = request(reader, f"{api}/bindings/{args.binding}/files/{args.file_id}/content", headers=bearer)
        check(status, 404, "content after withdrawal")
        status, body = request(admin, f"{file_api}/publication", headers=admin_headers)
        check(status, 200, "persisted withdrawal")
        assert json.loads(body)["state"] == "withdrawn"

        status, body = request(admin, f"{file_api}/republish", "POST", admin_headers, b"")
        check(status, 200, "republish")
        assert json.loads(body)["excluded"] is False
        assert audit_actions(args.binding, args.file_id) == ["eligible", "withdrawn"]
        status, body = request(reader, f"{api}/bindings/{args.binding}/manifest", headers=bearer)
        check(status, 200, "manifest after republish")
        assert args.file_id in [item["file_id"] for item in json.loads(body)["items"]]

        status, body = request(admin, f"{api}/admin/bindings", headers=admin_headers)
        check(status, 200, "admin binding list")
        current = next(b for b in json.loads(body)["bindings"] if b["id"] == args.binding)
        save_headers = {**admin_headers, "Content-Type": "application/json"}
        payload = json.dumps(current).encode()
        status, _ = request(admin, f"{api}/admin/bindings", "POST", save_headers, payload)
        check(status, 200, "unchanged binding update")
        status, _ = request(admin, f"{api}/admin/bindings", "POST", save_headers,
                            json.dumps({**current, "id": "overlap-smoke"}).encode())
        check(status, 409, "overlapping binding")
        print("publication HTTP smoke passed")
    finally:
        if original == "withdrawn":
            request(admin, f"{file_api}/withdraw", "POST", admin_headers, b"")
        else:
            request(admin, f"{file_api}/republish", "POST", admin_headers, b"")
        if created:
            run_occ("user:delete", user_id)


if __name__ == "__main__":
    main()
