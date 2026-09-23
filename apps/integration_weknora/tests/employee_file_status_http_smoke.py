#!/usr/bin/env python3
"""Verify that the Files sidebar API reports only session-visible source state.

Run against the local Compose fixture after bootstrap:
  python3 apps/integration_weknora/tests/employee_file_status_http_smoke.py --file-id 77
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import time
import urllib.parse
import urllib.request

from publication_http_smoke import PROJECT, check, load_env, login, request, run_occ


def read_config():
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "-u", "www-data", "nextcloud", "php", "occ",
         "config:app:get", "integration_weknora", "weknora_web_url"],
        cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def wait_login_url(opener, url, headers, expected):
    # `occ` and Apache have separate app-config caches in the local stack.
    for attempt in range(20):
        status, body = request(opener, url, headers=headers)
        check(status, 200, "employee status after URL configuration")
        if json.loads(body)["weknora_login_url"] == expected:
            return
        if attempt < 19:
            time.sleep(0.2)
    raise AssertionError(f"expected login URL {expected!r} was not observed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding", default="dev-published")
    parser.add_argument("--file-id", type=int, required=True)
    parser.add_argument("--share-path", default="Published")
    args = parser.parse_args()
    assert re.fullmatch(r"[A-Za-z0-9_-]+", args.binding) and args.file_id > 0
    values = load_env()
    base = f"http://127.0.0.1:{values.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    api = f"{base}/index.php/apps/integration_weknora/api/v1"
    status_url = f"{api}/files/{args.file_id}/status"
    state_url = f"{api}/admin/bindings/{args.binding}/files/{args.file_id}/publication"
    admin, csrf = login(base, values["NEXTCLOUD_ADMIN_USER"], values["NEXTCLOUD_ADMIN_PASSWORD"])
    admin_headers = {"requesttoken": csrf}
    anonymous = urllib.request.build_opener()

    status, _ = request(anonymous, status_url)
    check(status, 401, "anonymous status")
    status, _ = request(anonymous, status_url,
                        headers={"Authorization": f"Bearer {values['WEKNORA_SERVICE_TOKEN']}"})
    check(status, 401, "service token cannot read employee status")
    status, body = request(admin, state_url, headers=admin_headers)
    check(status, 200, "original publication state")
    original_state = json.loads(body)["state"]
    original_url = read_config()
    guest_uid = f"weknora_status_{secrets.token_hex(5)}"
    guest_password = secrets.token_urlsafe(24)
    guest_created = False
    share_id = None
    shares = f"{base}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    share_headers = {**admin_headers, "OCS-APIREQUEST": "true", "Accept": "application/json",
                     "Content-Type": "application/x-www-form-urlencoded"}

    try:
        if original_state == "withdrawn":
            status, _ = request(admin, f"{api}/admin/bindings/{args.binding}/files/{args.file_id}/republish",
                                "POST", admin_headers, b"")
            check(status, 200, "prepare eligible source")

        status, body = request(admin, status_url, headers=admin_headers)
        check(status, 200, "owner status")
        source = json.loads(body)
        assert source["source_state"] == "in_scope" and source["binding_name"]
        assert source["knowledge_state"] == "unverified" and source["qa_available"] is False
        assert source["knowledge_ready_at"] is None and source["published_source_etag"] is None
        assert isinstance(source["source_modified_at"], int)

        status, body = request(admin, f"{api}/files/2147483647/status", headers=admin_headers)
        check(status, 404, "unreadable file status")
        assert "binding_name" not in json.loads(body), body

        run_occ("config:app:set", "integration_weknora", "weknora_web_url",
                "--value=http://127.0.0.1:18081")
        wait_login_url(admin, status_url, admin_headers, "http://127.0.0.1:18081")
        run_occ("config:app:set", "integration_weknora", "weknora_web_url",
                "--value=javascript:alert(1)")
        wait_login_url(admin, status_url, admin_headers, None)
        run_occ("config:app:set", "integration_weknora", "weknora_web_url",
                "--value=https://weknora.example/login")
        wait_login_url(admin, status_url, admin_headers, "https://weknora.example/login")

        run_occ("user:add", "--password-from-env", "--no-interaction", guest_uid,
                env=dict(os.environ, NC_PASS=guest_password))
        guest_created = True
        guest, guest_csrf = login(base, guest_uid, guest_password)
        status, body = request(guest, status_url, headers={"requesttoken": guest_csrf})
        assert status == 404, ("unshared file denied", status, body[:300])

        share_form = urllib.parse.urlencode({"path": args.share_path, "shareType": 0,
                                              "shareWith": guest_uid, "permissions": 1}).encode()
        status, body = request(admin, shares, "POST", share_headers, share_form)
        check(status, 200, "read-only folder share")
        share_id = json.loads(body)["ocs"]["data"]["id"]
        status, body = request(guest, status_url, headers={"requesttoken": guest_csrf})
        check(status, 200, "shared file status")
        shared = json.loads(body)
        assert shared["source_state"] == "in_scope" and shared["qa_available"] is False
        assert shared["weknora_login_url"] == "https://weknora.example/login"

        status, _ = request(admin, f"{api}/admin/bindings/{args.binding}/files/{args.file_id}/withdraw",
                            "POST", admin_headers, b"")
        check(status, 200, "withdraw source")
        status, body = request(guest, status_url, headers={"requesttoken": guest_csrf})
        check(status, 200, "withdrawn file status")
        withdrawn = json.loads(body)
        assert withdrawn["source_state"] == "withdrawn"
        assert withdrawn["weknora_login_url"] is None and withdrawn["qa_available"] is False

        status, _ = request(admin, f"{shares}/{share_id}", "DELETE", share_headers)
        check(status, 200, "revoke file share")
        share_id = None
        status, _ = request(guest, status_url, headers={"requesttoken": guest_csrf})
        check(status, 404, "revoked file status")
        print("employee file status HTTP smoke passed")
    finally:
        if share_id is not None:
            request(admin, f"{shares}/{share_id}", "DELETE", share_headers)
        if guest_created:
            run_occ("user:delete", guest_uid)
        action = "withdraw" if original_state == "withdrawn" else "republish"
        request(admin, f"{api}/admin/bindings/{args.binding}/files/{args.file_id}/{action}",
                "POST", admin_headers, b"")
        if original_url is None:
            run_occ("config:app:delete", "integration_weknora", "weknora_web_url")
        else:
            run_occ("config:app:set", "integration_weknora", "weknora_web_url",
                    f"--value={original_url}")


if __name__ == "__main__":
    main()
