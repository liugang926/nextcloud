#!/usr/bin/env python3
"""Check source authorization against a temporary Team folder and its ACL.

Requires an enabled groupfolders app in the local Compose stack. Creates a
temporary user, group, Team folder, binding, file and identity mapping, then
removes them. It does not use enterprise AD or test nested AD groups.
"""

import base64
import json
import os
import secrets
import subprocess
import urllib.parse
import urllib.request
import uuid

from changes_http_smoke import file_id, request as dav_request
from publication_http_smoke import (
    PROJECT, check, issue_machine_key, load_env, login, request,
    remove_binding, revoke_machine_key, run_occ,
)


def occ_output(*args):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "-u", "www-data", "nextcloud",
         "php", "occ", *args],
        cwd=PROJECT, check=True, text=True, stdout=subprocess.PIPE,
    )
    return result.stdout.strip()


def post_json(opener, url, payload, headers):
    return request(opener, url, "POST", {**headers, "Content-Type": "application/json"},
                   json.dumps(payload).encode())


def main():
    enabled = json.loads(occ_output("app:list", "--output=json"))["enabled"]
    if "groupfolders" not in enabled:
        raise RuntimeError("enable the local groupfolders app before this smoke test")

    values = load_env()
    base = f"http://127.0.0.1:{values.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    api = f"{base}/index.php/apps/integration_weknora/api/v1"
    admin_uid = values["NEXTCLOUD_ADMIN_USER"]
    admin, csrf = login(base, admin_uid, values["NEXTCLOUD_ADMIN_PASSWORD"])
    admin_headers = {"requesttoken": csrf}
    machine = urllib.request.build_opener()
    suffix = secrets.token_hex(6)
    guest_uid = f"weknora_team_reader_{suffix}"
    group_id = f"weknora_team_{suffix}"
    folder_name = f"WeKnoraTeam_{suffix}"
    binding_id = f"team-{suffix}"
    identity = {
        "directory_id": f"team-smoke-{suffix}",
        "object_guid": str(uuid.uuid4()),
        "nextcloud_uid": guest_uid,
    }
    dav_auth = base64.b64encode(
        f"{admin_uid}:{values['NEXTCLOUD_ADMIN_PASSWORD']}".encode()).decode()
    dav_headers = {"Authorization": f"Basic {dav_auth}"}
    dav_root = (f"{base}/remote.php/dav/files/"
                f"{urllib.parse.quote(admin_uid)}/{folder_name}")
    file_url = f"{dav_root}/team-note.txt"
    binding_url = f"{api}/admin/bindings"
    identities_url = f"{api}/admin/identities"
    machine_headers = None
    machine_key_id = None
    team_folder_id = None
    guest_created = group_created = binding_created = mapping_created = False

    try:
        password = secrets.token_urlsafe(24)
        run_occ("user:add", "--password-from-env", "--no-interaction", guest_uid,
                env=dict(os.environ, NC_PASS=password))
        guest_created = True
        guest_auth = base64.b64encode(f"{guest_uid}:{password}".encode()).decode()
        guest_file_url = (f"{base}/remote.php/dav/files/"
                          f"{urllib.parse.quote(guest_uid)}/{folder_name}/team-note.txt")
        guest_dav_headers = {"Authorization": f"Basic {guest_auth}"}
        run_occ("group:add", group_id)
        group_created = True
        run_occ("group:adduser", group_id, admin_uid, guest_uid)
        team_folder_id = int(occ_output("groupfolders:create", "--output=json", folder_name))
        run_occ("groupfolders:group", str(team_folder_id), group_id,
                "read", "write", "share", "delete")

        root_id = file_id(dav_root, dav_headers)
        status, body = dav_request(file_url, "PUT", dav_headers, b"Synthetic Team folder note\n")
        assert status in (201, 204), (status, body[:200])
        member_id = file_id(file_url, dav_headers)
        status, body = post_json(admin, binding_url, {
            "id": binding_id, "name": folder_name,
            "owner_uid": admin_uid, "root_file_id": root_id,
        }, admin_headers)
        check(status, 201, "create Team folder binding")
        binding_created = True
        machine_headers, machine_key_id = issue_machine_key(admin, api, binding_id, csrf)
        status, body = post_json(admin, identities_url, identity, admin_headers)
        check(status, 201, "map Team folder reader")
        mapping_created = True

        authorize_url = f"{api}/bindings/{binding_id}/authorize"
        payload = {"directory_id": identity["directory_id"],
                   "object_guid": identity["object_guid"], "file_id": member_id}

        def decision():
            status, body = post_json(machine, authorize_url, payload, machine_headers)
            check(status, 200, "Team folder authorization")
            return json.loads(body)

        assert decision()["allow"] is True, "group member should read Team folder file"
        status, _ = dav_request(guest_file_url, "GET", guest_dav_headers)
        check(status, 200, "group member WebDAV read")

        run_occ("groupfolders:permissions", str(team_folder_id), "--enable")
        run_occ("groupfolders:permissions", str(team_folder_id),
                f"--user={guest_uid}", "team-note.txt", "--", "-read")
        denied = decision()
        assert denied["allow"] is False and denied["reason"] == "source_not_readable", denied
        status, _ = dav_request(guest_file_url, "GET", guest_dav_headers)
        assert status in (403, 404), f"advanced ACL WebDAV read returned {status}"

        run_occ("groupfolders:permissions", str(team_folder_id),
                f"--user={guest_uid}", "team-note.txt", "clear")
        assert decision()["allow"] is True, "cleared ACL should restore read"
        status, _ = dav_request(guest_file_url, "GET", guest_dav_headers)
        check(status, 200, "cleared ACL WebDAV read")

        run_occ("group:removeuser", group_id, guest_uid)
        assert decision()["allow"] is False, "removing Team folder group membership must deny"
        status, _ = dav_request(guest_file_url, "GET", guest_dav_headers)
        assert status in (403, 404), f"removed group member WebDAV read returned {status}"
        print("Team folder ACL HTTP smoke passed")
    finally:
        if mapping_created:
            post_json(admin, f"{identities_url}/revoke", identity, admin_headers)
        if machine_key_id is not None:
            revoke_machine_key(admin, api, binding_id, machine_key_id, csrf)
        if binding_created:
            remove_binding(admin, api, binding_id, csrf)
        if team_folder_id is not None:
            run_occ("groupfolders:delete", str(team_folder_id), "--force")
        if group_created:
            run_occ("group:delete", group_id)
        if guest_created:
            run_occ("user:delete", guest_uid)


if __name__ == "__main__":
    main()
