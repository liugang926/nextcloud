#!/usr/bin/env python3
"""Exercise Nextcloud's durable source pairing with a synthetic target ID.

Requires the local Compose stack and the current app migration. No WeKnora
knowledge base or AD identity is created by this source-side test.
"""

import json
import secrets
import subprocess
import urllib.parse
import urllib.request
import uuid

from changes_http_smoke import file_id
from publication_http_smoke import (PROJECT, check, issue_machine_key, load_env, login,
                                    remove_binding, request, revoke_machine_key)


def decoded(body):
    return json.loads(body.decode())


def purge_retired_test_pair(binding_id):
    # Binding removal retains tombstones by design. This test has a unique
    # synthetic ID and removes only its retired/aborted pairing history.
    assert binding_id.startswith("source-pair-smoke-") and binding_id.replace("-", "").isalnum()
    statement = ("DELETE FROM oc_weknora_src_pair "
                 f"WHERE binding_id='{binding_id}' AND state IN ('retired','aborted')")
    subprocess.run(["docker", "compose", "exec", "-T", "db", "psql", "-U", "nextcloud",
                    "-d", "nextcloud", "-v", "ON_ERROR_STOP=1", "-c", statement],
                   cwd=PROJECT, check=True, stdout=subprocess.DEVNULL)


def main():
    env = load_env()
    base = f"http://127.0.0.1:{env.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    api = f"{base}/index.php/apps/integration_weknora/api/v1"
    owner = env["NEXTCLOUD_ADMIN_USER"]
    admin, csrf = login(base, owner, env["NEXTCLOUD_ADMIN_PASSWORD"])
    admin_headers = {"requesttoken": csrf, "Content-Type": "application/json"}
    dav_headers = {"Authorization": "Basic " + __import__("base64").b64encode(
        f"{owner}:{env['NEXTCLOUD_ADMIN_PASSWORD']}".encode()).decode()}
    suffix = secrets.token_hex(6)
    binding_id = f"source-pair-smoke-{suffix}"
    name = binding_id
    folder_url = f"{base}/remote.php/dav/files/{urllib.parse.quote(owner)}/{name}"
    moved_url = folder_url + "-moved"
    second_id = binding_id + "-other"
    second_folder_url = folder_url + "-other"
    binding_url = f"{api}/admin/bindings/{binding_id}"
    pairing_url = binding_url + "/source-pairing"
    commit_url = f"{api}/bindings/{binding_id}/source-pairing/commit"
    machine = urllib.request.build_opener()
    created_folder = False
    moved = False
    created_binding = False
    created_second_folder = False
    created_second_binding = False

    def admin_json(method, url, payload=None, headers=admin_headers):
        return request(admin, url, method, headers,
                       None if payload is None else json.dumps(payload).encode())

    def signed_commit(pairing, token, data_source_id, overrides=None):
        payload = {
            "operation_id": pairing["operation_id"],
            "instance_id": pairing["instance_id"],
            "tenant_id": pairing["tenant_id"],
            "knowledge_base_id": pairing["knowledge_base_id"],
            "data_source_id": data_source_id,
        }
        payload.update(overrides or {})
        headers = {"Authorization": "Bearer " + token,
                   "X-WeKnora-Key-Id": pairing["key_id"],
                   "Content-Type": "application/json"}
        return request(machine, commit_url, "POST", headers, json.dumps(payload).encode())

    try:
        status, _ = request(machine, folder_url, "MKCOL", dav_headers)
        check(status, 201, "create temporary folder")
        created_folder = True
        root_id = file_id(folder_url, dav_headers)
        status, _ = admin_json("POST", f"{api}/admin/bindings", {
            "id": binding_id, "name": name, "owner_uid": owner, "root_file_id": root_id,
        })
        check(status, 201, "create temporary binding")
        created_binding = True
        status, _ = request(machine, second_folder_url, "MKCOL", dav_headers)
        check(status, 201, "create second temporary folder")
        created_second_folder = True
        second_root_id = file_id(second_folder_url, dav_headers)
        status, _ = admin_json("POST", f"{api}/admin/bindings", {
            "id": second_id, "name": second_id, "owner_uid": owner,
            "root_file_id": second_root_id,
        })
        check(status, 201, "create second temporary binding")
        created_second_binding = True

        target = {"tenant_id": "18446744073709551615",
                  "knowledge_base_id": str(uuid.uuid4())}
        op1 = str(uuid.uuid4())
        status, _ = admin_json("POST", pairing_url,
                               {"operation_id": op1, **target}, headers={"Content-Type": "application/json"})
        check(status, 412, "prepare requires administrator CSRF")
        status, body = admin_json("POST", pairing_url, {"operation_id": op1,
                             "tenant_id": 7, "knowledge_base_id": target["knowledge_base_id"]})
        check(status, 400, "tenant ID must be canonical decimal string")
        status, body = admin_json("POST", pairing_url, {"operation_id": op1, **target})
        check(status, 201, "prepare pairing")
        first = decoded(body)
        pair1 = first["pairing"]
        token1 = first["token"]
        assert pair1["state"] == "pending" and pair1["tenant_id"] == target["tenant_id"]
        assert pair1["data_source_id"] is None and pair1["key_id"] == "pair_" + op1.replace("-", "")
        status, _ = admin_json("DELETE", f"{binding_url}/keys/{pair1['key_id']}")
        check(status, 409, "pending pairing key cannot be revoked independently")
        status, body = request(admin, pairing_url, headers=admin_headers)
        check(status, 200, "read pending pairing")
        assert "token" not in decoded(body) and decoded(body)["pairing"] == pair1
        status, body = admin_json("POST", pairing_url, {"operation_id": op1, **target})
        check(status, 200, "idempotent prepare")
        assert "token" not in decoded(body)
        status, _ = admin_json("POST", pairing_url,
                               {"operation_id": str(uuid.uuid4()), **target})
        check(status, 409, "another operation cannot take the binding")
        status, _ = admin_json("POST",
                               f"{api}/admin/bindings/{second_id}/source-pairing",
                               {"operation_id": str(uuid.uuid4()), **target})
        check(status, 409, "another binding cannot take the same KB")

        data_source = str(uuid.uuid4())
        other_key, other_key_id = issue_machine_key(admin, api, binding_id, csrf)
        try:
            wrong_key_payload = {
                "operation_id": op1, "instance_id": pair1["instance_id"],
                "tenant_id": target["tenant_id"],
                "knowledge_base_id": target["knowledge_base_id"],
                "data_source_id": data_source,
            }
            status, _ = request(machine, commit_url, "POST",
                                {**other_key, "Content-Type": "application/json"},
                                json.dumps(wrong_key_payload).encode())
            check(status, 409, "another valid binding key cannot commit the intent")
        finally:
            revoke_machine_key(admin, api, binding_id, other_key_id, csrf)
        status, _ = request(machine, commit_url, "POST", {"Content-Type": "application/json"},
                            json.dumps({"operation_id": op1}).encode())
        check(status, 401, "unsigned commit denied")
        status, body = signed_commit(pair1, token1, data_source,
                                      {"tenant_id": "18446744073709551616"})
        check(status, 400, "out-of-range tenant ID rejected")
        status, _ = admin_json("POST", binding_url + "/stop", {})
        check(status, 200, "stop publication during prepare")
        status, _ = signed_commit(pair1, token1, data_source)
        check(status, 423, "stopped binding cannot commit")
        status, _ = admin_json("POST", binding_url + "/resume", {})
        check(status, 200, "resume publication")
        status, _ = signed_commit(pair1, token1, data_source)
        check(status, 409, "changed publication epoch cannot commit")
        status, body = admin_json("DELETE", pairing_url, {"operation_id": op1})
        check(status, 200, "abort stale pairing")
        assert decoded(body)["pairing"]["state"] == "aborted" and decoded(body)["revoked_key"]
        status, body = admin_json("DELETE", pairing_url, {"operation_id": op1})
        check(status, 200, "idempotent abort")
        assert decoded(body)["revoked_key"] is False
        status, _ = signed_commit(pair1, token1, data_source)
        check(status, 401, "aborted key is revoked")

        op2 = str(uuid.uuid4())
        status, body = admin_json("POST", pairing_url, {"operation_id": op2, **target})
        check(status, 201, "prepare replacement pairing")
        pair2 = decoded(body)["pairing"]
        token2 = decoded(body)["token"]
        status, _ = request(machine, folder_url, "MOVE",
                            {**dav_headers, "Destination": moved_url})
        assert status in (201, 204), (status, "move folder")
        moved = True
        status, _ = signed_commit(pair2, token2, data_source)
        check(status, 409, "moved root cannot commit")
        status, _ = request(machine, moved_url, "MOVE",
                            {**dav_headers, "Destination": folder_url})
        assert status in (201, 204), (status, "restore folder")
        moved = False
        status, body = signed_commit(pair2, token2, data_source)
        check(status, 200, "commit pairing")
        active = decoded(body)
        assert active["changed"] is True and active["pairing"]["state"] == "active"
        assert active["pairing"]["data_source_id"] == data_source
        status, _ = admin_json("DELETE", f"{binding_url}/keys/{pair2['key_id']}")
        check(status, 409, "active pairing key cannot be revoked independently")
        status, body = signed_commit(pair2, token2, data_source)
        check(status, 200, "idempotent commit")
        assert decoded(body)["changed"] is False
        status, _ = signed_commit(pair2, token2, str(uuid.uuid4()))
        check(status, 409, "committed data source identity cannot change")
        status, _ = admin_json("DELETE", pairing_url, {"operation_id": op2})
        check(status, 409, "active pairing cannot be aborted")
        status, _ = admin_json("POST", binding_url + "/stop", {})
        check(status, 200, "stop established pairing")
        status, _ = signed_commit(pair2, token2, data_source)
        check(status, 423, "stopped pairing cannot commit")
        status, _ = admin_json("POST", binding_url + "/resume", {})
        check(status, 200, "resume established pairing")
        status, body = signed_commit(pair2, token2, data_source)
        check(status, 200, "established pairing survives stop and resume")
        assert decoded(body)["changed"] is False
        status, body = admin_json("POST", pairing_url, {"operation_id": op2, **target})
        check(status, 200, "established prepare retry survives stop and resume")
        assert "token" not in decoded(body) and decoded(body)["pairing"]["state"] == "active"

        status, _ = request(machine, folder_url, "MOVE",
                            {**dav_headers, "Destination": moved_url})
        assert status in (201, 204), (status, "move paired root")
        moved = True
        source_headers = {"Authorization": "Bearer " + token2,
                          "X-WeKnora-Key-Id": pair2["key_id"]}
        status, _ = request(machine, f"{api}/bindings/{binding_id}/manifest",
                            headers=source_headers)
        check(status, 503, "moved paired root fails closed")
        status, _ = request(machine, moved_url, "MOVE",
                            {**dav_headers, "Destination": folder_url})
        assert status in (201, 204), (status, "restore paired root")
        moved = False
        print("source pairing HTTP smoke passed")
    finally:
        if created_second_binding:
            remove_binding(admin, api, second_id, csrf)
        if created_second_folder:
            request(machine, second_folder_url, "DELETE", dav_headers)
        if created_binding:
            remove_binding(admin, api, binding_id, csrf)
            purge_retired_test_pair(binding_id)
        if created_folder:
            request(machine, moved_url if moved else folder_url, "DELETE", dav_headers)


if __name__ == "__main__":
    main()
