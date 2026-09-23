#!/usr/bin/env python3
"""Exercise source authorization and administrator identity mapping in Compose.

Requires a bound sample file readable by its binding owner. The script restores
the sample publication state and any pre-existing owner identity mapping.
"""

import argparse
import json
import os
import secrets
import uuid
import urllib.parse
import urllib.request

from publication_http_smoke import check, load_env, login, request, run_occ


def as_json(body):
    return json.loads(body.decode())


def post_json(opener, url, payload, headers):
    return request(opener, url, "POST", {**headers, "Content-Type": "application/json"},
                   json.dumps(payload).encode())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding", default="dev-published")
    parser.add_argument("--file-id", type=int, required=True)
    parser.add_argument("--share-path", default="Published",
                        help="binding folder path in the owner's Files view")
    args = parser.parse_args()
    values = load_env()
    base = f"http://127.0.0.1:{values.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    api = f"{base}/index.php/apps/integration_weknora/api/v1"
    authorize = f"{api}/bindings/{args.binding}/authorize"
    identities = f"{api}/admin/identities"
    publication = f"{api}/admin/bindings/{args.binding}/files/{args.file_id}"
    machine = urllib.request.build_opener()
    bearer = {"Authorization": f"Bearer {values['WEKNORA_SERVICE_TOKEN']}"}
    admin, csrf = login(base, values["NEXTCLOUD_ADMIN_USER"], values["NEXTCLOUD_ADMIN_PASSWORD"])
    admin_headers = {"requesttoken": csrf}

    status, body = request(admin, f"{api}/admin/bindings", headers=admin_headers)
    check(status, 200, "binding list")
    binding = next(b for b in as_json(body)["bindings"] if b["id"] == args.binding)
    owner = binding["owner_uid"]
    status, body = request(admin, identities, headers=admin_headers)
    check(status, 200, "identity list")
    existing_owner = next((i for i in as_json(body)["identities"] if i["nextcloud_uid"] == owner), None)
    owner_identity = existing_owner or {
        "directory_id": f"smoke-{secrets.token_hex(5)}",
        "object_guid": str(uuid.uuid4()),
        "nextcloud_uid": owner,
    }
    owner_created = False
    guest_created = False
    guest_mapped = False
    share_id = None
    guest_id = f"weknora_auth_{secrets.token_hex(5)}"
    guest_password = secrets.token_urlsafe(24)
    guest_identity = {
        "directory_id": owner_identity["directory_id"],
        "object_guid": str(uuid.uuid4()),
        "nextcloud_uid": guest_id,
    }
    original_state = None

    def decision(identity, file_id=args.file_id, extra_headers=None):
        payload = {"directory_id": identity["directory_id"],
                   "object_guid": identity["object_guid"], "file_id": file_id}
        return post_json(machine, authorize, payload,
                         bearer if extra_headers is None else extra_headers)

    try:
        status, body = decision(owner_identity, extra_headers={})
        check(status, 401, "machine credential required")
        assert as_json(body)["allow"] is False
        status, body = decision(owner_identity,
                                extra_headers={"Authorization": "Bearer wrong-token"})
        check(status, 401, "wrong machine credential")

        status, body = post_json(admin, identities, owner_identity, {})
        check(status, 412, "mapping creation requires CSRF")
        if existing_owner is None:
            status, body = post_json(admin, identities, owner_identity, admin_headers)
            check(status, 201, "create explicit owner mapping")
            owner_created = True

        status, body = decision(owner_identity)
        check(status, 200, "owner source authorization")
        allowed = as_json(body)
        assert allowed["allow"] is True and len(allowed["policy_revision"]) == 64, allowed

        unknown = {**owner_identity, "object_guid": str(uuid.uuid4())}
        status, body = decision(unknown)
        check(status, 200, "unmapped identity is denied")
        assert as_json(body)["allow"] is False
        status, body = decision(owner_identity, file_id=2147483647)
        check(status, 200, "unknown file is denied")
        assert as_json(body)["allow"] is False
        status, body = decision({**owner_identity, "object_guid": "admin@example.com"})
        check(status, 400, "email cannot stand in for objectGUID")
        assert as_json(body)["allow"] is False

        test_env = dict(os.environ, NC_PASS=guest_password)
        run_occ("user:add", "--password-from-env", "--no-interaction", guest_id, env=test_env)
        guest_created = True
        normal, normal_csrf = login(base, guest_id, guest_password)
        status, _ = request(normal, identities, headers={"requesttoken": normal_csrf})
        check(status, 403, "ordinary user cannot list mappings")
        status, _ = post_json(normal, identities, guest_identity, {"requesttoken": normal_csrf})
        check(status, 403, "ordinary user cannot create mappings")

        conflict = {**owner_identity, "object_guid": str(uuid.uuid4())}
        status, _ = post_json(admin, identities, conflict, admin_headers)
        check(status, 409, "one Nextcloud UID cannot map to two principals")
        status, body = post_json(admin, identities, guest_identity, admin_headers)
        check(status, 201, "create explicit guest mapping")
        guest_mapped = True
        status, body = decision(guest_identity)
        check(status, 200, "guest without folder access")
        assert as_json(body)["allow"] is False, body

        shares = f"{base}/ocs/v2.php/apps/files_sharing/api/v1/shares"
        share_form = urllib.parse.urlencode({"path": args.share_path, "shareType": 0,
                                              "shareWith": guest_id, "permissions": 1}).encode()
        share_headers = {**admin_headers, "OCS-APIREQUEST": "true", "Accept": "application/json",
                         "Content-Type": "application/x-www-form-urlencoded"}
        status, body = request(admin, shares, "POST", share_headers, share_form)
        check(status, 200, "read-only folder share")
        share = as_json(body)
        assert share["ocs"]["meta"]["statuscode"] == 200, share
        share_id = share["ocs"]["data"]["id"]
        status, body = decision(guest_identity)
        check(status, 200, "shared user's source authorization")
        assert as_json(body)["allow"] is True, body

        run_occ("user:disable", guest_id)
        status, body = decision(guest_identity)
        check(status, 200, "disabled account authorization")
        assert as_json(body)["allow"] is False, body
        run_occ("user:enable", guest_id)
        status, body = decision(guest_identity)
        check(status, 200, "re-enabled shared account")
        assert as_json(body)["allow"] is True, body

        status, _ = request(admin, f"{shares}/{share_id}", "DELETE", share_headers)
        check(status, 200, "share revocation")
        share_id = None
        status, body = decision(guest_identity)
        check(status, 200, "revoked folder access")
        assert as_json(body)["allow"] is False, body

        status, body = request(admin, f"{publication}/publication", headers=admin_headers)
        check(status, 200, "publication state")
        original_state = as_json(body)["state"]
        status, _ = request(admin, f"{publication}/withdraw", "POST", admin_headers, b"")
        check(status, 200, "withdraw publication")
        status, body = decision(owner_identity)
        check(status, 200, "withdrawn source authorization")
        assert as_json(body)["allow"] is False

        status, _ = post_json(admin, f"{identities}/revoke",
                              {**guest_identity, "nextcloud_uid": owner}, admin_headers)
        check(status, 409, "revoke requires exact mapping")
        status, body = post_json(admin, f"{identities}/revoke", guest_identity, admin_headers)
        check(status, 200, "revoke guest mapping")
        assert as_json(body)["revoked"] is True
        guest_mapped = False
        status, body = decision(guest_identity)
        check(status, 200, "revoked mapping is denied")
        assert as_json(body)["allow"] is False
        print("authorization HTTP smoke passed")
    finally:
        if share_id is not None:
            request(admin, f"{base}/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}",
                    "DELETE", {**admin_headers, "OCS-APIREQUEST": "true"})
        if original_state is not None:
            action = "withdraw" if original_state == "withdrawn" else "republish"
            request(admin, f"{publication}/{action}", "POST", admin_headers, b"")
        if guest_mapped:
            post_json(admin, f"{identities}/revoke", guest_identity, admin_headers)
        if guest_created:
            run_occ("user:delete", guest_id)
        if owner_created:
            post_json(admin, f"{identities}/revoke", owner_identity, admin_headers)


if __name__ == "__main__":
    main()
