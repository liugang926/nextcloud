#!/usr/bin/env python3
"""Verify the connection-only admin DOM and permission-filtered activity APIs."""

from __future__ import annotations

import argparse
import json
import urllib.request

from publication_http_smoke import check, load_env, login, request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder-id", type=int)
    parser.add_argument("--file-id", type=int)
    args = parser.parse_args()
    values = load_env()
    base = f"http://127.0.0.1:{values.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    admin, csrf = login(base, values["NEXTCLOUD_ADMIN_USER"],
                        values["NEXTCLOUD_ADMIN_PASSWORD"])
    api = f"{base}/index.php/apps/integration_weknora/api/v1"

    status, body = request(admin, f"{base}/settings/admin/integration_weknora")
    check(status, 200, "administrator settings")
    page = body.decode("utf-8", "replace")
    for forbidden in (
        "weknora-directory-rule-form", "weknora-binding-form",
        "weknora-pairing-binding", "weknora-connection-form",
        "weknora-publication-form", "Source diagnostics",
        "Folder bindings", "Source pairing", "File publication decision",
    ):
        assert forbidden not in page, forbidden
    for required in (
        "WeKnora 连接", "weknora-setup-form",
        "weknora-setup-space-list", "weknora-setup-disconnect",
    ):
        assert required in page, required

    headers = {"requesttoken": csrf}
    folder_id = args.folder_id
    if folder_id is None:
        status, body = request(admin, f"{api}/admin/bindings", headers=headers)
        check(status, 200, "administrator binding inventory")
        bindings = json.loads(body).get("bindings", [])
        assert bindings and isinstance(bindings[0].get("root_file_id"), int), bindings
        folder_id = bindings[0]["root_file_id"]
    folder_url = f"{api}/folders/{folder_id}/activity?limit=5"
    status, body = request(admin, folder_url, headers=headers)
    check(status, 200, "folder activity")
    folder = json.loads(body)
    assert isinstance(folder.get("items"), list)
    assert folder.get("next_cursor") is None or isinstance(folder["next_cursor"], str)

    if args.file_id is not None:
        status, body = request(
            admin, f"{api}/files/{args.file_id}/activity?limit=5", headers=headers)
        check(status, 200, "file activity")
        file_page = json.loads(body)
        assert isinstance(file_page.get("items"), list)

    anonymous = urllib.request.build_opener()
    status, _ = request(anonymous, folder_url)
    check(status, 401, "anonymous activity")
    print("connection-only admin DOM and activity HTTP smoke passed")


if __name__ == "__main__":
    main()
