#!/usr/bin/env python3
"""Exercise the durable changes API with real Nextcloud WebDAV operations.

Requires the local Compose stack and scripts/bootstrap.sh. The test creates
and removes uniquely named files under the development Published binding.
"""

import base64
import json
from pathlib import Path
import secrets
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from machine_auth import signed_headers


PROJECT = Path(__file__).resolve().parents[3]


def load_env():
    result = {}
    for line in (PROJECT / ".env").read_text().splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value.strip().strip('"').strip("'")
    return result


def request(url, method="GET", headers=None, body=None):
    req = urllib.request.Request(url, data=body,
                                 headers=signed_headers(method, url, headers or {}, body),
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def drain(api_url, headers, cursor=None):
    events = []
    for _ in range(100):
        suffix = "" if cursor is None else "?" + urllib.parse.urlencode({"cursor": cursor})
        status, body = request(api_url + suffix, headers=headers)
        assert status == 200, (status, body[:300])
        page = json.loads(body)
        assert page["hint_only"] is True and page["rescan_required"] is False
        assert len(page["items"]) <= 200
        events.extend(page["items"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            return events, cursor
    raise AssertionError("changes pagination did not terminate")


def file_id(url, headers):
    body = (b'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
            b'<d:prop><oc:fileid/></d:prop></d:propfind>')
    status, result = request(url, "PROPFIND", {
        **headers, "Depth": "0", "Content-Type": "application/xml",
    }, body)
    assert status == 207, (status, result[:300])
    value = ET.fromstring(result).find(".//{http://owncloud.org/ns}fileid")
    assert value is not None and value.text and value.text.isdigit()
    return int(value.text)


def main():
    env = load_env()
    base = f"http://127.0.0.1:{env.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    api_url = f"{base}/index.php/apps/integration_weknora/api/v1/bindings/dev-published/changes"
    dav_base = (f"{base}/remote.php/dav/files/"
                f"{urllib.parse.quote(env['NEXTCLOUD_ADMIN_USER'])}/Published")
    auth = base64.b64encode(
        f"{env['NEXTCLOUD_ADMIN_USER']}:{env['NEXTCLOUD_ADMIN_PASSWORD']}".encode()
    ).decode()
    dav_headers = {"Authorization": f"Basic {auth}"}
    machine_headers = {"Authorization": f"Bearer {env['WEKNORA_SERVICE_TOKEN']}"}

    status, _ = request(api_url)
    assert status == 401, f"unauthenticated changes returned {status}"
    _, cursor = drain(api_url, machine_headers)
    suffix = secrets.token_hex(8)
    file_url = f"{dav_base}/changes-{suffix}.txt"
    folder_url = f"{dav_base}/changes-folder-{suffix}"
    child_url = f"{folder_url}/child.txt"
    outside_url = (f"{base}/remote.php/dav/files/"
                   f"{urllib.parse.quote(env['NEXTCLOUD_ADMIN_USER'])}/changes-outside-{suffix}")
    moved_url = f"{outside_url}/moved.txt"
    moved_folder_url = f"{outside_url}/changes-folder-{suffix}"
    moving_url = f"{dav_base}/changes-moving-{suffix}.txt"
    try:
        status, body = request(file_url, "PUT", dav_headers, b"first version\n")
        assert status in (201, 204), (status, body[:300])
        new_id = file_id(file_url, dav_headers)
        events, cursor = drain(api_url, machine_headers, cursor)
        assert any(e["type"] == "upsert" and e["file_id"] == new_id for e in events), events

        status, body = request(file_url, "PUT", dav_headers, b"second version\n")
        assert status in (201, 204), (status, body[:300])
        events, cursor = drain(api_url, machine_headers, cursor)
        assert any(e["type"] == "upsert" and e["file_id"] == new_id for e in events), events

        status, body = request(file_url, "DELETE", dav_headers)
        assert status == 204, (status, body[:300])
        events, cursor = drain(api_url, machine_headers, cursor)
        assert any(e["type"] == "delete" and e["file_id"] == new_id for e in events), events

        status, _ = request(file_url, "DELETE", dav_headers)
        assert status == 404, f"second DELETE returned {status}"
        events, cursor = drain(api_url, machine_headers, cursor)
        assert not any(e["type"] == "delete" and e["file_id"] == new_id for e in events), events

        status, body = request(file_url, "PUT", dav_headers, b"new file, same name\n")
        assert status in (201, 204), (status, body[:300])
        replacement_id = file_id(file_url, dav_headers)
        assert replacement_id != new_id, "re-upload must have a new source identity"
        events, cursor = drain(api_url, machine_headers, cursor)
        assert any(e["type"] == "upsert" and e["file_id"] == replacement_id for e in events), events
        assert not any(e["type"] == "upsert" and e["file_id"] == new_id for e in events), events
        status, body = request(file_url, "DELETE", dav_headers)
        assert status == 204, (status, body[:300])
        _, cursor = drain(api_url, machine_headers, cursor)

        status, body = request(outside_url, "MKCOL", dav_headers)
        assert status == 201, (status, body[:300])
        status, body = request(moving_url, "PUT", dav_headers, b"move out\n")
        assert status in (201, 204), (status, body[:300])
        moving_id = file_id(moving_url, dav_headers)
        _, cursor = drain(api_url, machine_headers, cursor)
        status, body = request(moving_url, "MOVE", {
            **dav_headers, "Destination": moved_url,
        })
        assert status in (201, 204), (status, body[:300])
        events, cursor = drain(api_url, machine_headers, cursor)
        assert any(e["type"] == "delete" and e["file_id"] == moving_id and
                   e["old_path"] and e["old_path"].endswith(f"/changes-moving-{suffix}.txt")
                   for e in events), events

        status, body = request(folder_url, "MKCOL", dav_headers)
        assert status == 201, (status, body[:300])
        status, body = request(child_url, "PUT", dav_headers, b"folder member\n")
        assert status in (201, 204), (status, body[:300])
        child_id = file_id(child_url, dav_headers)
        _, cursor = drain(api_url, machine_headers, cursor)
        status, body = request(folder_url, "MOVE", {
            **dav_headers, "Destination": moved_folder_url,
        })
        assert status in (201, 204), (status, body[:300])
        events, cursor = drain(api_url, machine_headers, cursor)
        assert any(e["type"] == "subtree_deleted" and
                   e["old_path"] and e["old_path"].endswith(f"/changes-folder-{suffix}")
                   for e in events), events
        status, _ = request(
            f"{base}/index.php/apps/integration_weknora/api/v1/bindings/dev-published/files/{child_id}/content",
            headers=machine_headers)
        assert status == 404, f"moved-out descendant was readable: HTTP {status}"

        status, body = request(moved_folder_url, "MOVE", {
            **dav_headers, "Destination": folder_url,
        })
        assert status in (201, 204), (status, body[:300])
        events, cursor = drain(api_url, machine_headers, cursor)
        assert any(e["type"] == "subtree_scan" and
                   e["path"] and e["path"].endswith(f"/changes-folder-{suffix}")
                   for e in events), events
        status, body = request(
            f"{base}/index.php/apps/integration_weknora/api/v1/bindings/dev-published/files/{child_id}/content",
            headers=machine_headers)
        assert status == 200 and body == b"folder member\n", (status, body[:300])

        status, body = request(folder_url, "DELETE", dav_headers)
        assert status == 204, (status, body[:300])
        events, cursor = drain(api_url, machine_headers, cursor)
        assert any(e["type"] == "subtree_deleted" and
                   e["old_path"] and e["old_path"].endswith(f"/changes-folder-{suffix}")
                   for e in events), events

        invalid = cursor + "A"
        status, _ = request(api_url + "?" + urllib.parse.urlencode({"cursor": invalid}),
                            headers=machine_headers)
        assert status == 400, f"tampered cursor returned {status}"
        print("changes HTTP smoke passed")
    finally:
        request(file_url, "DELETE", dav_headers)
        request(moving_url, "DELETE", dav_headers)
        request(outside_url, "DELETE", dav_headers)
        request(folder_url, "DELETE", dav_headers)


if __name__ == "__main__":
    main()
