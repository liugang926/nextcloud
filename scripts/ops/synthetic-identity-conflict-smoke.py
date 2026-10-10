#!/usr/bin/env python3
"""Check GUID/UID mapping conflicts in a disposable, loopback Nextcloud stack.

Both synthetic LDAP accounts must already be mapped correctly. No account or
mapping is provisioned by this probe. Invalid requests should leave the
registry unchanged. Credentials are read only from process environment.
"""

import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
MAX_RESPONSE = 1024 * 1024


class ConflictProbeError(Exception):
    pass


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin):
        self.origin = origin

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        parts = urllib.parse.urlsplit(newurl)
        if f"{parts.scheme}://{parts.netloc}" != self.origin:
            raise ConflictProbeError("cross-origin redirect refused")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def request(opener, method, url, *, payload=None, headers=None):
    req = urllib.request.Request(url, data=payload, method=method, headers=headers or {})
    try:
        response = opener.open(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    except urllib.error.URLError as error:
        raise ConflictProbeError("network or TLS request failed") from error
    with response:
        body = response.read(MAX_RESPONSE + 1)
        if len(body) > MAX_RESPONSE:
            raise ConflictProbeError("response exceeded size limit")
        return response.status, body


def parse_fixture(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("synthetic_fixture") is not True:
        raise ConflictProbeError("fixture must explicitly be schema-version-1 synthetic data")
    directory = data.get("directory_id")
    if not isinstance(directory, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", directory):
        raise ConflictProbeError("invalid fixture directory_id")
    accounts = data.get("accounts")
    if not isinstance(accounts, dict) or set(accounts) != {"a", "b"}:
        raise ConflictProbeError("fixture needs exactly accounts a and b")
    for label in ("a", "b"):
        account = accounts[label]
        if not isinstance(account, dict) or not isinstance(account.get("nextcloud_uid"), str) or \
                not account["nextcloud_uid"] or not isinstance(account.get("object_guid"), str) or \
                not GUID.fullmatch(account["object_guid"]):
            raise ConflictProbeError(f"account {label} lacks a valid UID/objectGUID")
    if (accounts["a"]["nextcloud_uid"] == accounts["b"]["nextcloud_uid"] or
            accounts["a"]["object_guid"].lower() == accounts["b"]["object_guid"].lower()):
        raise ConflictProbeError("fixture accounts must have distinct UIDs and GUIDs")
    return directory, accounts


def login(origin, username, password):
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        SameOriginRedirect(origin))
    status, html = request(opener, "GET", origin + "/login")
    if status != 200:
        raise ConflictProbeError("Nextcloud login page unavailable")
    match = re.search(rb'data-requesttoken="([^"]+)"', html)
    if match is None:
        raise ConflictProbeError("Nextcloud login page omitted CSRF token")
    payload = urllib.parse.urlencode({"requesttoken": match.group(1).decode(),
                                      "user": username, "password": password}).encode()
    status, _ = request(opener, "POST", origin + "/login", payload=payload,
                        headers={"Origin": origin, "Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        raise ConflictProbeError("Nextcloud administrator login failed")
    status, html = request(opener, "GET", origin + "/apps/dashboard/")
    if status != 200:
        raise ConflictProbeError("Nextcloud dashboard unavailable")
    match = re.search(rb'data-requesttoken="([^"]+)"', html)
    if match is None:
        raise ConflictProbeError("Nextcloud dashboard omitted CSRF token")
    return opener, match.group(1).decode()


def json_request(opener, method, url, *, payload=None, csrf=None):
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode()
    if csrf is not None:
        headers["requesttoken"] = csrf
    status, raw = request(opener, method, url, payload=body, headers=headers)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConflictProbeError("identity endpoint returned invalid JSON") from error
    if not isinstance(data, dict):
        raise ConflictProbeError("identity endpoint returned non-object JSON")
    return status, data


def check_conflicts(origin, fixture, username, password):
    directory, accounts = parse_fixture(fixture)
    opener, csrf = login(origin, username, password)
    endpoint = origin + "/index.php/apps/integration_weknora/api/v1/admin/identities"
    status, before = json_request(opener, "GET", endpoint, csrf=csrf)
    if status != 200 or not isinstance(before.get("identities"), list):
        raise ConflictProbeError("administrator identity registry unavailable")
    identities = before["identities"]
    for label in ("a", "b"):
        account = accounts[label]
        matches = [row for row in identities if isinstance(row, dict) and
                   row.get("directory_id") == directory and
                   str(row.get("object_guid", "")).lower() == account["object_guid"].lower() and
                   row.get("nextcloud_uid") == account["nextcloud_uid"]]
        if len(matches) != 1:
            raise ConflictProbeError(f"account {label} mapping is absent or ambiguous")

    a, b = accounts["a"], accounts["b"]
    valid = {"directory_id": directory, "object_guid": a["object_guid"],
             "nextcloud_uid": a["nextcloud_uid"]}
    status, result = json_request(opener, "POST", endpoint, payload=valid, csrf=csrf)
    if status != 200 or result.get("object_guid") != a["object_guid"].lower():
        raise ConflictProbeError("exact existing identity was not idempotent")

    invalid = [
        ("A GUID to B UID", {**valid, "nextcloud_uid": b["nextcloud_uid"]}),
        ("B GUID to A UID", {**valid, "object_guid": b["object_guid"]}),
        ("unrelated GUID to A UID", {**valid, "object_guid": str(uuid.uuid4())}),
        ("wrong directory", {**valid, "directory_id": directory + "-other"}),
        ("email as identity", {**valid, "object_guid": "alice@example.test"}),
    ]
    for label, payload in invalid:
        status, result = json_request(opener, "POST", endpoint, payload=payload, csrf=csrf)
        if status != 400 or result.get("error") != "invalid_identity":
            if status == 201:
                # An incorrect implementation may have inserted this exact
                # invalid mapping. Undo only that unexpected insertion.
                json_request(opener, "POST", endpoint + "/revoke", payload=payload, csrf=csrf)
            raise ConflictProbeError(label + " was not rejected by live GUID proof")
    status, after = json_request(opener, "GET", endpoint, csrf=csrf)
    if status != 200 or after.get("identities") != identities:
        raise ConflictProbeError("identity registry changed during conflict probes")
    return {"idempotent_mapping": True, "conflicting_mappings_denied": len(invalid),
            "registry_unchanged": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--nextcloud-origin", required=True)
    args = parser.parse_args()
    parsed = urllib.parse.urlsplit(args.nextcloud_origin)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or \
            parsed.port is None or args.nextcloud_origin != f"{parsed.scheme}://{parsed.netloc}":
        raise ConflictProbeError("synthetic identity test requires canonical loopback HTTP origin")
    if os.environ.get("AD_ACCEPTANCE_TEST_ENV") != "isolated-test-accounts":
        raise ConflictProbeError("AD_ACCEPTANCE_TEST_ENV must equal isolated-test-accounts")
    username = os.environ.get("AD_TEST_NEXTCLOUD_ADMIN_USER")
    password = os.environ.get("AD_TEST_NEXTCLOUD_ADMIN_PASSWORD")
    if not username or not password:
        raise ConflictProbeError("missing protected test administrator credentials")
    print(json.dumps(check_conflicts(args.nextcloud_origin, args.fixture, username, password),
                     separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (ConflictProbeError, OSError, ValueError, json.JSONDecodeError) as error:
        print("Synthetic identity conflict probe failed: " + str(error), file=sys.stderr)
        sys.exit(1)
