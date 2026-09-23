#!/usr/bin/env python3
"""Exercise runtime binding isolation and a lost publication owner in Compose.

The test uses real WebDAV folder moves and the app's HTTP endpoints. A temporary
app-config edit simulates a bound owner account disappearing after setup.
"""

import argparse
import base64
import json
import os
import secrets
import subprocess
import urllib.parse
import urllib.request
import uuid

from changes_http_smoke import file_id, request as dav_request
from publication_http_smoke import PROJECT, check, load_env, login, request, run_occ


def occ(*args):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "-u", "www-data", "nextcloud",
         "php", "occ", *args], cwd=PROJECT, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def bindings():
    return json.loads(occ("config:app:get", "integration_weknora", "bindings"))


def set_bindings(value):
    occ("config:app:set", "integration_weknora", "bindings",
        "--value=" + json.dumps(value, separators=(",", ":")))


def sql(statement):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "nextcloud",
         "-d", "nextcloud", "-v", "ON_ERROR_STOP=1", "-Atc", statement],
        cwd=PROJECT, check=True, text=True, stdout=subprocess.PIPE,
    )
    return result.stdout.strip()


def post_json(opener, url, payload, headers):
    return request(opener, url, "POST", {**headers, "Content-Type": "application/json"},
                   json.dumps(payload).encode())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate-missing-bind-lock", action="store_true",
                        help="remove the migration's lock seed to exercise first-write recovery")
    args = parser.parse_args()
    env = load_env()
    base = f"http://127.0.0.1:{env.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    api = f"{base}/index.php/apps/integration_weknora/api/v1"
    admin_uid = env["NEXTCLOUD_ADMIN_USER"]
    admin, csrf = login(base, admin_uid, env["NEXTCLOUD_ADMIN_PASSWORD"])
    admin_headers = {"requesttoken": csrf}
    machine = urllib.request.build_opener()
    bearer = {"Authorization": f"Bearer {env['WEKNORA_SERVICE_TOKEN']}"}
    dav_auth = base64.b64encode(
        f"{admin_uid}:{env['NEXTCLOUD_ADMIN_PASSWORD']}".encode()).decode()
    dav_headers = {"Authorization": f"Basic {dav_auth}"}
    dav_base = f"{base}/remote.php/dav/files/{urllib.parse.quote(admin_uid)}"
    suffix = secrets.token_hex(6)
    a_id, b_id = f"scope-a-{suffix}", f"scope-b-{suffix}"
    a_url = f"{dav_base}/{a_id}"
    b_url = f"{dav_base}/{b_id}"
    moved_b_url = f"{a_url}/{b_id}"
    file_url = f"{b_url}/member.txt"
    moved_file_url = f"{moved_b_url}/member.txt"
    root_a = root_b = member = None
    moved = False
    admin_disabled = False
    guest_created = False
    mapping_created = False
    share_id = None
    identity = None
    guest_uid = f"scope_reader_{suffix}"

    try:
        if args.simulate_missing_bind_lock:
            sql("DELETE FROM oc_weknora_bind_lock WHERE id = 1")
            assert sql("SELECT count(*) FROM oc_weknora_bind_lock WHERE id = 1") == "0"
        for url in (a_url, b_url):
            status, body = dav_request(url, "MKCOL", dav_headers)
            check(status, 201, "create test root")
        root_a = file_id(a_url, dav_headers)
        root_b = file_id(b_url, dav_headers)
        status, body = dav_request(file_url, "PUT", dav_headers, b"bound file\n")
        assert status in (201, 204), (status, body[:300])
        member = file_id(file_url, dav_headers)

        for binding_id, root_id in ((a_id, root_a), (b_id, root_b)):
            status, body = post_json(admin, f"{api}/admin/bindings", {
                "id": binding_id, "name": binding_id,
                "owner_uid": admin_uid, "root_file_id": root_id,
            }, admin_headers)
            check(status, 201, "create sibling binding")
        if args.simulate_missing_bind_lock:
            assert sql("SELECT count(*) FROM oc_weknora_bind_lock WHERE id = 1") == "1"

        status, body = request(machine, f"{api}/bindings/{a_id}/manifest", headers=bearer)
        check(status, 200, "sibling A baseline manifest")
        assert member not in [item["file_id"] for item in json.loads(body)["items"]]
        status, body = request(machine, f"{api}/bindings/{b_id}/manifest", headers=bearer)
        check(status, 200, "sibling B baseline manifest")
        assert member in [item["file_id"] for item in json.loads(body)["items"]]

        password = secrets.token_urlsafe(24)
        run_occ("user:add", "--password-from-env", "--no-interaction", guest_uid,
                env=dict(os.environ, NC_PASS=password))
        guest_created = True
        login(base, guest_uid, password)
        shares_url = f"{base}/ocs/v2.php/apps/files_sharing/api/v1/shares"
        share_payload = urllib.parse.urlencode({
            "path": b_id, "shareType": 0, "shareWith": guest_uid,
            "permissions": 1,
        }).encode()
        share_headers = {**admin_headers, "OCS-APIREQUEST": "true",
                         "Accept": "application/json",
                         "Content-Type": "application/x-www-form-urlencoded"}
        status, body = request(admin, shares_url, "POST", share_headers, share_payload)
        check(status, 200, "share source root with mapped reader")
        share = json.loads(body)
        assert share["ocs"]["meta"]["statuscode"] == 200, share
        share_id = share["ocs"]["data"]["id"]

        identities_url = f"{api}/admin/identities"
        identity = {
            "directory_id": f"scope-{suffix}",
            "object_guid": str(uuid.uuid4()),
            "nextcloud_uid": guest_uid,
        }
        status, body = post_json(admin, identities_url, identity, admin_headers)
        check(status, 201, "create test identity")
        mapping_created = True
        authorize_url = f"{api}/bindings/{b_id}/authorize"
        payload = {"directory_id": identity["directory_id"],
                   "object_guid": identity["object_guid"], "file_id": member}

        def decision():
            return post_json(machine, authorize_url, payload, bearer)

        status, body = decision()
        check(status, 200, "baseline authorization")
        assert json.loads(body)["allow"] is True, body

        status, body = dav_request(b_url, "MOVE", {
            **dav_headers, "Destination": moved_b_url,
        })
        assert status in (201, 204), (status, body[:300])
        moved = True
        for url in (f"{api}/bindings/{a_id}/manifest",
                    f"{api}/bindings/{b_id}/files/{member}/content"):
            status, body = request(machine, url, headers=bearer)
            check(status, 503, "overlapping roots must block source reads")
        status, body = decision()
        check(status, 503, "overlapping roots must block authorization")
        assert json.loads(body)["allow"] is False

        status, body = dav_request(moved_b_url, "MOVE", {
            **dav_headers, "Destination": b_url,
        })
        assert status in (201, 204), (status, body[:300])
        moved = False
        status, body = decision()
        check(status, 200, "restored binding authorization")
        assert json.loads(body)["allow"] is True, body

        # The reader keeps its share and identity while the binding owner is
        # temporarily disabled. A stale indexed copy must not be authorized.
        run_occ("user:disable", admin_uid)
        admin_disabled = True
        status, body = decision()
        check(status, 503, "disabled binding owner must deny authorization")
        assert json.loads(body)["allow"] is False
        status, body = request(machine, f"{api}/bindings/{b_id}/manifest", headers=bearer)
        check(status, 503, "disabled binding owner must block manifest")

        run_occ("user:enable", admin_uid)
        admin_disabled = False
        status, body = decision()
        check(status, 200, "re-enabled binding owner restores authorization")
        assert json.loads(body)["allow"] is True, body

        print("runtime binding scope HTTP smoke passed")
    finally:
        if args.simulate_missing_bind_lock:
            sql("INSERT INTO oc_weknora_bind_lock (id) VALUES (1) ON CONFLICT DO NOTHING")
        if admin_disabled:
            run_occ("user:enable", admin_uid)
        if moved:
            dav_request(moved_b_url, "MOVE", {**dav_headers, "Destination": b_url})
        if mapping_created and identity is not None:
            post_json(admin, f"{api}/admin/identities/revoke", identity, admin_headers)
        if share_id is not None:
            request(admin, f"{shares_url}/{share_id}", "DELETE", share_headers)
        if guest_created:
            run_occ("user:delete", guest_uid)
        if root_a is not None or root_b is not None:
            set_bindings([item for item in bindings() if item["id"] not in (a_id, b_id)])
        dav_request(moved_file_url, "DELETE", dav_headers)
        dav_request(file_url, "DELETE", dav_headers)
        dav_request(moved_b_url, "DELETE", dav_headers)
        dav_request(b_url, "DELETE", dav_headers)
        dav_request(a_url, "DELETE", dav_headers)


if __name__ == "__main__":
    main()
