#!/usr/bin/env python3
"""Exercise manifest pagination and mutation detection against local Docker."""

import base64
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent /
                       "apps/integration_weknora/tests"))
from machine_auth import signed_headers
from publication_http_smoke import login as admin_login, request as web_request


def env_values():
    result = {}
    for line in (Path(__file__).resolve().parent.parent / ".env").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            result[key] = value
    return result


env = env_values()
base = f"http://127.0.0.1:{env.get('NEXTCLOUD_HTTP_PORT', '18082')}"
api = f"{base}/index.php/apps/integration_weknora/api/v1"
dav = f"{base}/remote.php/dav/files/{quote(env['NEXTCLOUD_ADMIN_USER'])}/Published/manifest-pagination"
basic = base64.b64encode(f"{env['NEXTCLOUD_ADMIN_USER']}:{env['NEXTCLOUD_ADMIN_PASSWORD']}".encode()).decode()


def request(method, url, auth, data=None, headers=None):
    all_headers = signed_headers(method, url, {"Authorization": auth, **(headers or {})}, data)
    req = Request(url, data=data, method=method, headers=all_headers)
    try:
        with urlopen(req, timeout=20) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def manifest(cursor=None):
    query = "" if cursor is None else "?" + urlencode({"cursor": cursor})
    status, body = request("GET", f"{api}/bindings/dev-published/manifest{query}",
                           f"Bearer {env['WEKNORA_SERVICE_TOKEN']}")
    return status, json.loads(body)


auth = f"Basic {basic}"
status, _ = request("MKCOL", dav, auth)
assert status in (201, 405), f"MKCOL returned {status}"
withdrawn_file = None
admin = None
csrf = None
try:
    for index in range(205):
        status, _ = request("PUT", f"{dav}/case-{index:03d}.txt", auth, b"test\n")
        assert status in (201, 204), f"PUT {index} returned {status}"

    status, first = manifest()
    assert status == 200 and not first["complete"] and first["next_cursor"]
    assert len(first["items"]) == 200

    status, _ = request("PUT", f"{dav}/case-999.txt", auth, b"changed\n")
    assert status in (201, 204)
    status, changed = manifest(first["next_cursor"])
    assert status == 409 and changed["error"] == "manifest_changed"

    # Publication changes do not change the folder ETag. A saved cursor must
    # still be rejected when an administrator withdraws one of its files.
    status, first = manifest()
    assert status == 200 and first["next_cursor"]
    withdrawn_file = next(item["file_id"] for item in first["items"]
                          if item["path"].endswith("manifest-pagination/case-000.txt"))
    admin, csrf = admin_login(base, env["NEXTCLOUD_ADMIN_USER"],
                              env["NEXTCLOUD_ADMIN_PASSWORD"])
    publication_url = (f"{api}/admin/bindings/dev-published/files/"
                       f"{withdrawn_file}/withdraw")
    status, _ = web_request(admin, publication_url, "POST",
                            {"requesttoken": csrf}, b"")
    assert status == 200, f"withdrawal returned {status}"
    status, changed = manifest(first["next_cursor"])
    assert status == 409 and changed["error"] == "manifest_changed"
    status, _ = web_request(admin, publication_url.removesuffix("/withdraw") + "/republish",
                            "POST", {"requesttoken": csrf}, b"")
    assert status == 200, f"republish returned {status}"
    withdrawn_file = None

    ids = set()
    cursor = None
    while True:
        status, page = manifest(cursor)
        assert status == 200
        for item in page["items"]:
            assert item["file_id"] not in ids
            ids.add(item["file_id"])
        if page["complete"]:
            break
        cursor = page["next_cursor"]
    assert len(ids) >= 207, f"expected sample plus 206 test files, got {len(ids)}"

    sample = next(item for item in page["items"] if item["file_id"] in ids)  # valid bound ID
    status, _ = request("GET", f"{api}/bindings/dev-published/files/{sample['file_id']}/content",
                        f"Bearer {env['WEKNORA_SERVICE_TOKEN']}", headers={"If-Match": '"deliberately-wrong"'})
    assert status == 412, f"If-Match returned {status}"
    print(f"Manifest smoke passed: {len(ids)} unique files, content and publication mutations rejected, conditional read checked.")
finally:
    if withdrawn_file is not None and admin is not None:
        web_request(admin,
                    f"{api}/admin/bindings/dev-published/files/{withdrawn_file}/republish",
                    "POST", {"requesttoken": csrf}, b"")
    status, _ = request("DELETE", dav, auth)
    assert status in (204, 404), f"cleanup returned {status}"
