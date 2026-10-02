#!/usr/bin/env python3
"""Verify administrator-issued scoped keys, overlap and committed revocation."""

import json
import secrets
import urllib.error
import urllib.request

from machine_auth import signed_headers
from publication_http_smoke import load_env, login, request


def machine_request(url, token, key_id):
    headers = signed_headers("GET", url, {"Authorization": "Bearer " + token},
                             key_id=key_id)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def main():
    env = load_env()
    base = f"http://127.0.0.1:{env.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    api = f"{base}/index.php/apps/integration_weknora/api/v1"
    binding_id = "dev-published"
    capabilities = f"{api}/capabilities"
    bindings = f"{api}/bindings"
    keys_url = f"{api}/admin/bindings/{binding_id}/keys"
    admin, csrf = login(base, env["NEXTCLOUD_ADMIN_USER"], env["NEXTCLOUD_ADMIN_PASSWORD"])
    admin_headers = {"requesttoken": csrf}
    issued = []

    def issue(key_id):
        status, body = request(admin, keys_url, "POST",
                               {**admin_headers, "Content-Type": "application/json"},
                               json.dumps({"key_id": key_id}).encode())
        assert status == 201, f"key issue returned HTTP {status}"
        value = json.loads(body)
        assert value["binding_id"] == binding_id and value["key_id"] == key_id
        assert isinstance(value["token"], str) and len(value["token"]) >= 48
        issued.append(key_id)
        return value["token"]

    try:
        first_id = "rotation-smoke-" + secrets.token_hex(5)
        second_id = "rotation-smoke-" + secrets.token_hex(5)
        first_token = issue(first_id)
        second_token = issue(second_id)

        assert machine_request(capabilities, env["WEKNORA_SERVICE_TOKEN"], "default")[0] == 200
        assert machine_request(capabilities, first_token, first_id)[0] == 200
        assert machine_request(capabilities, second_token, second_id)[0] == 200
        assert machine_request(capabilities, first_token, second_id)[0] == 401
        assert machine_request(capabilities, second_token, first_id)[0] == 401

        status, body = machine_request(bindings, first_token, first_id)
        assert status == 200 and [item["id"] for item in json.loads(body)["bindings"]] == [binding_id]

        status, body = request(admin, keys_url, headers=admin_headers)
        assert status == 200
        metadata = json.loads(body)["keys"]
        assert {first_id, second_id, "default"}.issubset({item["key_id"] for item in metadata})
        assert first_token.encode() not in body and second_token.encode() not in body
        assert all("token" not in item and "token_sha256" not in item for item in metadata)

        status, _ = request(admin, f"{keys_url}/{first_id}", "DELETE", admin_headers)
        assert status == 200
        issued.remove(first_id)
        assert machine_request(capabilities, first_token, first_id)[0] == 401, "revoked key still works"
        assert machine_request(capabilities, second_token, second_id)[0] == 200
        assert machine_request(capabilities, env["WEKNORA_SERVICE_TOKEN"], "default")[0] == 200
        print("machine key rotation HTTP smoke passed")
    finally:
        for key_id in issued:
            request(admin, f"{keys_url}/{key_id}", "DELETE", admin_headers)


if __name__ == "__main__":
    main()
