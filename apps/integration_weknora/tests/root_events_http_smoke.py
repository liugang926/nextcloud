#!/usr/bin/env python3
"""Verify bound-root rename and deletion hints in the local Compose stack."""

import base64
import json
import subprocess
import secrets
import urllib.parse

from changes_http_smoke import drain, file_id, load_env, request, PROJECT
from publication_http_smoke import (
    issue_machine_key, login, remove_binding, request as session_request,
    revoke_machine_key,
)


def sql(statement):
    subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "nextcloud",
         "-d", "nextcloud", "-v", "ON_ERROR_STOP=1", "-c", statement],
        cwd=PROJECT, check=True, stdout=subprocess.DEVNULL,
    )


def main():
    env = load_env()
    base = f"http://127.0.0.1:{env.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    owner = urllib.parse.quote(env["NEXTCLOUD_ADMIN_USER"])
    dav_base = f"{base}/remote.php/dav/files/{owner}"
    auth = base64.b64encode(
        f"{env['NEXTCLOUD_ADMIN_USER']}:{env['NEXTCLOUD_ADMIN_PASSWORD']}".encode()
    ).decode()
    dav_headers = {"Authorization": f"Basic {auth}"}
    api = f"{base}/index.php/apps/integration_weknora/api/v1"
    machine_headers = None
    key_id = None
    admin = csrf = None
    suffix = secrets.token_hex(8)
    binding_id = f"root-events-{suffix}"
    original_url = f"{dav_base}/root-events-{suffix}"
    moved_url = f"{dav_base}/root-events-moved-{suffix}"
    configured = False
    floor_set = False
    try:
        status, body = request(original_url, "MKCOL", dav_headers)
        assert status == 201, (status, body[:300])
        root_id = file_id(original_url, dav_headers)
        admin, csrf = login(base, env["NEXTCLOUD_ADMIN_USER"],
                            env["NEXTCLOUD_ADMIN_PASSWORD"])
        payload = json.dumps({
            "id": binding_id, "name": "Root event test",
            "owner_uid": env["NEXTCLOUD_ADMIN_USER"], "root_file_id": root_id,
        }).encode()
        status, body = session_request(
            admin, f"{base}/index.php/apps/integration_weknora/api/v1/admin/bindings",
            "POST", {"requesttoken": csrf, "Content-Type": "application/json"}, payload,
        )
        assert status == 201, (status, body[:300])
        configured = True
        machine_headers, key_id = issue_machine_key(admin, api, binding_id, csrf)
        changes_url = f"{api}/bindings/{binding_id}/changes"
        _, cursor = drain(changes_url, machine_headers)
        initial_cursor = cursor

        status, body = request(original_url, "MOVE", {
            **dav_headers, "Destination": moved_url,
        })
        assert status in (201, 204), (status, body[:300])
        events, cursor = drain(changes_url, machine_headers, cursor)
        assert any(e["type"] == "subtree_moved" and e["file_id"] == root_id
                   for e in events), events

        status, body = request(moved_url, "DELETE", dav_headers)
        assert status == 204, (status, body[:300])
        events, cursor = drain(changes_url, machine_headers, cursor)
        assert any(e["type"] == "subtree_deleted" and e["file_id"] == root_id
                   for e in events), events

        # Simulate a retention floor; an old cursor must request a full rescan.
        last_event_id = max(int(e["event_id"]) for e in events)
        sql("INSERT INTO oc_weknora_change_floor (binding_id, floor_id) "
            f"VALUES ('{binding_id}', {last_event_id})")
        floor_set = True
        status, body = request(
            changes_url + "?" + urllib.parse.urlencode({"cursor": initial_cursor}),
            headers=machine_headers,
        )
        expired = json.loads(body)
        assert (status == 409 and expired["rescan_required"] is True and
                isinstance(expired.get("next_cursor"), str)), (status, body[:300])
        # In production the consumer adopts this checkpoint only after a
        # successful complete manifest reconciliation.
        status, body = request(
            changes_url + "?" + urllib.parse.urlencode({"cursor": expired["next_cursor"]}),
            headers=machine_headers,
        )
        assert status == 200 and json.loads(body)["hint_only"] is True, (status, body[:300])
        print("bound-root event HTTP smoke passed")
    finally:
        try:
            if floor_set:
                sql(f"DELETE FROM oc_weknora_change_floor WHERE binding_id = '{binding_id}'")
        finally:
            try:
                if key_id is not None:
                    revoke_machine_key(admin, api, binding_id, key_id, csrf)
            finally:
                try:
                    if configured:
                        remove_binding(admin, api, binding_id, csrf)
                finally:
                    request(original_url, "DELETE", dav_headers)
                    request(moved_url, "DELETE", dav_headers)


if __name__ == "__main__":
    main()
