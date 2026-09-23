#!/usr/bin/env python3
"""Exercise the durable binding-wide publication gate on synthetic local data.

Run after bootstrap: python3 apps/integration_weknora/tests/binding_publication_http_smoke.py --file-id 77
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

from changes_http_smoke import file_id
from publication_http_smoke import PROJECT, check, load_env, login, request, run_occ


def as_json(body):
    return json.loads(body.decode())


def sql(statement):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "nextcloud",
         "-d", "nextcloud", "-v", "ON_ERROR_STOP=1", "-Atc", statement],
        cwd=PROJECT, check=True, text=True, stdout=subprocess.PIPE,
    )
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding", default="dev-published")
    parser.add_argument("--file-id", required=True, type=int)
    args = parser.parse_args()
    assert args.binding.replace("_", "").replace("-", "").isalnum() and args.file_id > 0
    env = load_env()
    base = f"http://127.0.0.1:{env.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    api = f"{base}/index.php/apps/integration_weknora/api/v1"
    binding_url = f"{api}/admin/bindings/{args.binding}"
    machine_url = f"{api}/bindings/{args.binding}"
    status_url = f"{api}/files/{args.file_id}/status"
    owner_uid = env["NEXTCLOUD_ADMIN_USER"]
    admin, csrf = login(base, owner_uid, env["NEXTCLOUD_ADMIN_PASSWORD"])
    admin_headers = {"requesttoken": csrf}
    machine = urllib.request.build_opener()
    machine_headers = {"Authorization": f"Bearer {env['WEKNORA_SERVICE_TOKEN']}"}
    guest_uid = f"stop_smoke_{secrets.token_hex(5)}"
    guest_password = secrets.token_urlsafe(24)
    guest_created = False
    mapping_created = False
    stopped = False
    folder_created = False
    folder_name = f"binding-stop-{secrets.token_hex(5)}"
    dav_root = (f"{base}/remote.php/dav/files/{urllib.parse.quote(owner_uid)}"
                f"/Published/{folder_name}")
    basic = base64.b64encode(f"{owner_uid}:{env['NEXTCLOUD_ADMIN_PASSWORD']}".encode()).decode()
    dav_headers = {"Authorization": f"Basic {basic}"}

    def transition(action, headers=admin_headers):
        return request(admin, f"{binding_url}/{action}", "POST", headers, b"")

    def decision(identity):
        payload = json.dumps({**identity, "file_id": args.file_id}).encode()
        return request(machine, f"{machine_url}/authorize", "POST",
                       {**machine_headers, "Content-Type": "application/json"}, payload)

    status, body = request(admin, f"{api}/admin/bindings", headers=admin_headers)
    check(status, 200, "admin binding list")
    binding = next(b for b in as_json(body)["bindings"] if b["id"] == args.binding)
    assert binding["publication_state"] == "active", "the synthetic fixture must start active"
    initial_epoch = binding["publication_epoch"]
    status, body = request(admin, f"{api}/admin/identities", headers=admin_headers)
    check(status, 200, "identity list")
    existing = next((item for item in as_json(body)["identities"]
                     if item["nextcloud_uid"] == owner_uid), None)
    identity = ({"directory_id": existing["directory_id"],
                 "object_guid": existing["object_guid"]} if existing else
                {"directory_id": f"stop-smoke-{secrets.token_hex(5)}",
                 "object_guid": str(uuid.uuid4())})
    try:
        if existing is None:
            status, body = request(admin, f"{api}/admin/identities", "POST",
                                   {**admin_headers, "Content-Type": "application/json"},
                                   json.dumps({**identity, "nextcloud_uid": owner_uid}).encode())
            check(status, 201, "create synthetic identity mapping")
            mapping_created = True

        status, body = decision(identity)
        check(status, 200, "baseline source authorization")
        assert as_json(body)["allow"] is True, body
        status, body = request(machine, f"{machine_url}/files/{args.file_id}/content",
                               headers=machine_headers)
        check(status, 200, "baseline source content")

        run_occ("user:add", "--password-from-env", "--no-interaction", guest_uid,
                env=dict(os.environ, NC_PASS=guest_password))
        guest_created = True
        guest, guest_csrf = login(base, guest_uid, guest_password)
        status, _ = request(guest, f"{binding_url}/stop", "POST",
                            {"requesttoken": guest_csrf}, b"")
        check(status, 403, "ordinary user cannot stop publication")
        status, _ = transition("stop", {})
        check(status, 412, "stop requires CSRF token")

        status, _ = request(machine, f"{machine_url}/manifest", headers=machine_headers)
        check(status, 200, "baseline manifest")
        status, _ = request(machine, f"{dav_root}", "MKCOL", dav_headers)
        check(status, 201, "create pagination fixture folder")
        folder_created = True
        for index in range(200):
            status, _ = request(machine, f"{dav_root}/case-{index:03d}.txt", "PUT",
                                dav_headers, b"synthetic\n")
            assert status in (201, 204), (index, status)
        status, body = request(machine, f"{machine_url}/manifest", headers=machine_headers)
        check(status, 200, "paginated manifest")
        cursor = as_json(body)["next_cursor"]
        assert cursor and as_json(body)["complete"] is False

        before_hint = int(sql("SELECT COALESCE(max(id),0) FROM oc_weknora_outbox "
                              f"WHERE binding_id='{args.binding}'"))
        status, body = transition("stop")
        check(status, 200, "stop publication")
        stopped = True
        stop = as_json(body)
        assert stop["publication_state"] == "stopped" and stop["changed"] is True
        assert stop["publication_epoch"] == initial_epoch + 1
        assert stop["reconcile_hint_recorded"] is True
        assert int(sql("SELECT COALESCE(max(id),0) FROM oc_weknora_outbox "
                       f"WHERE binding_id='{args.binding}'")) > before_hint

        status, body = request(machine, f"{api}/bindings", headers=machine_headers)
        check(status, 200, "stopped binding remains machine-visible")
        listed = as_json(body)["bindings"]
        assert len(listed) == 1 and listed[0]["id"] == args.binding
        assert listed[0]["publication_state"] == "stopped"
        status, body = request(machine, f"{machine_url}/manifest", headers=machine_headers)
        check(status, 423, "stopped manifest is not empty success")
        assert as_json(body)["error"] == "publication_stopped"
        status, body = request(machine, f"{machine_url}/files/{args.file_id}/content",
                               headers=machine_headers)
        check(status, 423, "stopped content")
        status, body = decision(identity)
        check(status, 200, "stopped authorization")
        assert as_json(body)["allow"] is False and as_json(body)["reason"] == "publication_stopped"
        status, body = request(admin, status_url, headers=admin_headers)
        check(status, 200, "stopped employee status")
        assert as_json(body)["source_state"] == "publication_stopped"
        assert as_json(body)["weknora_login_url"] is None
        assert sql("SELECT publication_state FROM oc_weknora_binding_id "
                   f"WHERE binding_id='{args.binding}'") == "stopped"

        status, body = transition("stop")
        check(status, 200, "idempotent stop")
        assert as_json(body)["changed"] is False
        assert as_json(body)["publication_epoch"] == stop["publication_epoch"]

        status, body = transition("resume")
        check(status, 200, "resume publication")
        stopped = False
        resumed = as_json(body)
        assert resumed["changed"] is True and resumed["publication_epoch"] == initial_epoch + 2
        assert resumed["reconcile_hint_recorded"] is True
        status, body = request(machine, f"{machine_url}/manifest?" +
                               urllib.parse.urlencode({"cursor": cursor}), headers=machine_headers)
        check(status, 409, "cursor from before stop must be invalid after resume")
        assert as_json(body)["error"] == "manifest_changed"
        status, body = request(machine, f"{machine_url}/manifest", headers=machine_headers)
        check(status, 200, "fresh manifest after resume")
        status, body = decision(identity)
        check(status, 200, "authorization after resume")
        assert as_json(body)["allow"] is True
        status, body = request(machine, f"{machine_url}/files/{args.file_id}/content",
                               headers=machine_headers)
        check(status, 200, "source content after resume")

        file_url = f"{binding_url}/files/{args.file_id}"
        status, body = request(admin, f"{file_url}/publication", headers=admin_headers)
        check(status, 200, "per-file publication before preservation check")
        original_file_state = as_json(body)["state"]
        try:
            status, _ = request(admin, f"{file_url}/withdraw", "POST", admin_headers, b"")
            check(status, 200, "withdraw file")
            status, _ = transition("stop")
            check(status, 200, "stop with file withdrawal")
            stopped = True
            status, _ = transition("resume")
            check(status, 200, "resume with file withdrawal")
            stopped = False
            status, body = request(admin, f"{file_url}/publication", headers=admin_headers)
            check(status, 200, "per-file withdrawal preserved")
            assert as_json(body)["state"] == "withdrawn"
            status, body = decision(identity)
            check(status, 200, "withdrawal still denies after resume")
            assert as_json(body)["allow"] is False
        finally:
            if original_file_state == "eligible":
                request(admin, f"{file_url}/republish", "POST", admin_headers, b"")

        # Resume must re-evaluate the root; a vanished source cannot reopen.
        temp_binding = f"resume-root-{secrets.token_hex(5)}"
        temp_root = (f"{base}/remote.php/dav/files/{urllib.parse.quote(owner_uid)}"
                     f"/{temp_binding}")
        temp_created = False
        temp_bound = False
        try:
            status, _ = request(machine, temp_root, "MKCOL", dav_headers)
            check(status, 201, "create separate temporary root")
            temp_created = True
            root_id = file_id(temp_root, dav_headers)
            status, body = request(admin, f"{api}/admin/bindings", "POST",
                                   {**admin_headers, "Content-Type": "application/json"},
                                   json.dumps({"id": temp_binding, "name": temp_binding,
                                               "owner_uid": owner_uid,
                                               "root_file_id": root_id}).encode())
            check(status, 201, "bind separate temporary root")
            temp_bound = True
            status, _ = request(admin, f"{api}/admin/bindings/{temp_binding}/stop",
                                "POST", admin_headers, b"")
            check(status, 200, "stop temporary root")
            status, _ = request(machine, temp_root, "DELETE", dav_headers)
            check(status, 204, "remove temporary root")
            temp_created = False
            status, body = request(admin, f"{api}/admin/bindings/{temp_binding}/resume",
                                   "POST", admin_headers, b"")
            check(status, 409, "resume refuses a missing root")
            status, body = request(admin, f"{api}/admin/bindings", headers=admin_headers)
            check(status, 200, "temporary root remains stopped")
            temp_state = next(b for b in as_json(body)["bindings"] if b["id"] == temp_binding)
            assert temp_state["publication_state"] == "stopped"
        finally:
            if temp_bound:
                request(admin, f"{api}/admin/bindings/{temp_binding}",
                        "DELETE", admin_headers)
            if temp_created:
                request(machine, temp_root, "DELETE", dav_headers)

        print("binding publication HTTP smoke passed")
    finally:
        if stopped:
            transition("resume")
        if folder_created:
            request(machine, dav_root, "DELETE", dav_headers)
        if guest_created:
            run_occ("user:delete", guest_uid)
        if mapping_created:
            request(admin, f"{api}/admin/identities/revoke", "POST",
                    {**admin_headers, "Content-Type": "application/json"},
                    json.dumps({**identity, "nextcloud_uid": owner_uid}).encode())


if __name__ == "__main__":
    main()
