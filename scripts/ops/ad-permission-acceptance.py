#!/usr/bin/env python3
"""Read-only two-account AD authorization probe for a pre-provisioned pilot fixture.

This tool never configures AD, creates accounts, changes memberships, or
uploads files. The fixture must contain only synthetic content. See AD-acceptance.md
for the required operator-run phases and the limits of this evidence.
"""

import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "apps/integration_weknora/tests"))
from machine_auth import signed_headers  # noqa: E402
from synthetic_ldap_topology import TopologyError, validate_topology  # noqa: E402

GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
BINDING = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
DIRECTORY = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
DENIED_HTTP = {401, 403, 404}
MAX_BODY = 2 * 1024 * 1024


class ProbeError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ProbeError("HTTP redirect refused; check the exact configured origin")


OPENER = urllib.request.build_opener(NoRedirect)


def origin(value, *, allow_remote_https, allow_loopback_http):
    parsed = urllib.parse.urlsplit(value)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "[::1]", "::1"}
    if (not parsed.hostname or parsed.username or parsed.password or parsed.path or
            parsed.query or parsed.fragment or parsed.port is None or
            value != f"{parsed.scheme}://{parsed.netloc}"):
        raise ProbeError("origins must be canonical scheme://host:port values")
    if parsed.scheme == "http" and not (loopback and allow_loopback_http):
        raise ProbeError("HTTP is permitted only for explicit loopback testing")
    if parsed.scheme == "https" and not (loopback or allow_remote_https):
        raise ProbeError("remote HTTPS requires --allow-remote-test-environment")
    if parsed.scheme not in {"http", "https"}:
        raise ProbeError("only HTTP or HTTPS origins are supported")
    return value


def fixture(path, case):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ProbeError("fixture must use schema_version 1")
    if data.get("synthetic_fixture") is not True:
        raise ProbeError("fixture must explicitly assert synthetic_fixture=true")
    for field, pattern in (("binding_id", BINDING), ("directory_id", DIRECTORY),
                           ("knowledge_base_id", SAFE_ID), ("knowledge_id", SAFE_ID)):
        if not isinstance(data.get(field), str) or not pattern.fullmatch(data[field]):
            raise ProbeError(f"invalid fixture {field}")
    if not isinstance(data.get("file_id"), int) or data["file_id"] < 1:
        raise ProbeError("fixture file_id must be a positive integer")
    query = data.get("synthetic_query")
    if not isinstance(query, str) or not 3 <= len(query) <= 160:
        raise ProbeError("fixture synthetic_query must contain 3–160 characters")
    accounts = data.get("accounts")
    if not isinstance(accounts, dict) or set(accounts) != {"a", "b"}:
        raise ProbeError("fixture must define exactly accounts a and b")
    for label, account in accounts.items():
        if not isinstance(account, dict):
            raise ProbeError(f"account {label} must be an object")
        for field in ("nextcloud_uid", "weknora_identifier", "dav_path"):
            value = account.get(field)
            if not isinstance(value, str) or not value or len(value) > 512:
                raise ProbeError(f"account {label} has invalid {field}")
        object_guid = account.get("object_guid")
        if not isinstance(object_guid, str) or not GUID.fullmatch(object_guid):
            raise ProbeError(f"account {label} needs a canonical object_guid")
        segments = account["dav_path"].split("/")
        if any(segment in {"", ".", ".."} or "\\" in segment for segment in segments):
            raise ProbeError(f"account {label} dav_path must be relative and unambiguous")
    if accounts["a"]["object_guid"].lower() == accounts["b"]["object_guid"].lower():
        raise ProbeError("the two accounts must have different objectGUID values")
    cases = data.get("cases")
    if not isinstance(cases, dict) or case not in cases:
        raise ProbeError("requested case is absent from fixture")
    expectations = cases[case]
    if not isinstance(expectations, dict) or set(expectations) != {"a", "b"}:
        raise ProbeError("each case must define accounts a and b")
    fields = {"nextcloud_login", "ldap_login", "dav", "source", "knowledge", "search"}
    for label, expected in expectations.items():
        if not isinstance(expected, dict) or set(expected) != fields or not all(
                type(value) is bool for value in expected.values()):
            raise ProbeError(f"case {case} account {label} needs six boolean expectations")
        if not expected["ldap_login"] and (expected["knowledge"] or expected["search"]):
            raise ProbeError("a failed LDAP login cannot have expected WeKnora reads")
        if not expected["nextcloud_login"] and expected["dav"]:
            raise ProbeError("a failed Nextcloud account cannot read the DAV fixture")
    return data, expectations


def required_environment():
    names = ("AD_ACCEPTANCE_TEST_ENV", "AD_TEST_A_PASSWORD", "AD_TEST_B_PASSWORD",
             "AD_TEST_BINDING_KEY_ID", "AD_TEST_BINDING_TOKEN")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise ProbeError("missing explicit test environment: " + ", ".join(missing))
    if os.environ["AD_ACCEPTANCE_TEST_ENV"] != "isolated-test-accounts":
        raise ProbeError("AD_ACCEPTANCE_TEST_ENV must equal isolated-test-accounts")
    key_id = os.environ["AD_TEST_BINDING_KEY_ID"]
    if not SAFE_ID.fullmatch(key_id):
        raise ProbeError("AD_TEST_BINDING_KEY_ID has invalid syntax")
    return {"a": os.environ["AD_TEST_A_PASSWORD"], "b": os.environ["AD_TEST_B_PASSWORD"]}, (
        key_id, os.environ["AD_TEST_BINDING_TOKEN"])


def http(method, url, *, headers=None, payload=None):
    request = urllib.request.Request(url, data=payload, method=method,
                                     headers=headers or {})
    try:
        with OPENER.open(request, timeout=25) as response:
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise ProbeError("response exceeded the read-only probe size limit")
            return response.status, body
    except urllib.error.HTTPError as error:
        body = error.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise ProbeError("error response exceeded the probe size limit")
        return error.code, body
    except urllib.error.URLError as error:
        raise ProbeError("network or TLS request failed") from error


def require_equal(actual, expected, label):
    if actual != expected:
        raise ProbeError(f"{label}: expected {expected}, observed {actual}")


def dav_probe(nc_origin, account, password, file_id):
    parts = [urllib.parse.quote(account["nextcloud_uid"], safe="")]
    parts.extend(urllib.parse.quote(part, safe="") for part in account["dav_path"].split("/"))
    root_url = nc_origin + "/remote.php/dav/files/" + parts[0] + "/"
    url = nc_origin + "/remote.php/dav/files/" + "/".join(parts)
    auth = base64.b64encode((account["nextcloud_uid"] + ":" + password).encode()).decode()
    headers = {
        "Authorization": "Basic " + auth, "Depth": "0", "Content-Type": "application/xml",
    }
    payload = (b'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
               b'<d:prop><oc:fileid/></d:prop></d:propfind>')
    root_status, _ = http("PROPFIND", root_url, headers=headers, payload=payload)
    if root_status in DENIED_HTTP:
        return False, False
    if root_status != 207:
        raise ProbeError(f"Nextcloud user DAV root returned unexpected HTTP {root_status}")
    status, body = http("PROPFIND", url, headers=headers, payload=payload)
    if status == 207:
        try:
            value = ET.fromstring(body).find(".//{http://owncloud.org/ns}fileid")
        except ET.ParseError as error:
            raise ProbeError("WebDAV returned invalid XML") from error
        if value is None or value.text != str(file_id):
            raise ProbeError("WebDAV path does not resolve to the fixture file_id")
        return True, True
    if status in DENIED_HTTP:
        return True, False
    raise ProbeError(f"WebDAV returned unexpected HTTP {status}")


def source_probe(nc_origin, data, account, key_id, token):
    url = (nc_origin + "/index.php/apps/integration_weknora/api/v1/bindings/" +
           data["binding_id"] + "/authorize")
    body = json.dumps({"directory_id": data["directory_id"],
                       "object_guid": account["object_guid"].lower(),
                       "file_id": data["file_id"]}, separators=(",", ":")).encode()
    headers = signed_headers("POST", url, {
        "Authorization": "Bearer " + token, "X-WeKnora-Key-Id": key_id,
        "Content-Type": "application/json",
    }, body)
    status, response = http("POST", url, headers=headers, payload=body)
    if status != 200:
        raise ProbeError(f"source authorization returned HTTP {status}")
    try:
        result = json.loads(response)
    except json.JSONDecodeError as error:
        raise ProbeError("source authorization returned invalid JSON") from error
    if type(result.get("allow")) is not bool:
        raise ProbeError("source authorization omitted its allow decision")
    if result["allow"] and (not result.get("source_etag") or not result.get("policy_revision")):
        raise ProbeError("allowed source response lacks current ETag or policy revision")
    return result["allow"]


def weknora_login(wk_origin, identifier, password):
    payload = json.dumps({"identifier": identifier, "password": password}).encode()
    status, body = http("POST", wk_origin + "/api/v1/auth/ldap/login", headers={
        "Content-Type": "application/json"}, payload=payload)
    if status in {401, 403}:
        return None
    if status != 200:
        raise ProbeError(f"WeKnora LDAP login returned HTTP {status}")
    try:
        result = json.loads(body)
    except json.JSONDecodeError as error:
        raise ProbeError("WeKnora LDAP login returned invalid JSON") from error
    token = result.get("token")
    if result.get("success") is not True or not isinstance(token, str) or not token:
        raise ProbeError("WeKnora LDAP login returned no user token")
    return token


def knowledge_probe(wk_origin, knowledge_id, token):
    status, body = http("GET", wk_origin + "/api/v1/knowledge/" + knowledge_id,
                        headers={"Authorization": "Bearer " + token})
    if status in {403, 404}:
        return False
    if status != 200:
        raise ProbeError(f"WeKnora knowledge read returned HTTP {status}")
    try:
        result = json.loads(body)
    except json.JSONDecodeError as error:
        raise ProbeError("WeKnora knowledge read returned invalid JSON") from error
    resource = result.get("data")
    if result.get("success") is not True or not isinstance(resource, dict) or \
            resource.get("id") != knowledge_id:
        raise ProbeError("WeKnora knowledge read returned the wrong resource")
    return True


def direct_content_probe(wk_origin, knowledge_id, token):
    """Check old-JWT chunk and preview routes for one synthetic document."""
    checks = {}
    for label, path in (("chunks", "/api/v1/chunks/" + knowledge_id),
                        ("preview", "/api/v1/knowledge/" + knowledge_id + "/preview")):
        status, body = http("GET", wk_origin + path,
                            headers={"Authorization": "Bearer " + token})
        if status in {403, 404}:
            checks[label] = False
            continue
        if status != 200:
            raise ProbeError(f"WeKnora {label} read returned HTTP {status}")
        if label == "chunks":
            try:
                result = json.loads(body)
            except json.JSONDecodeError as error:
                raise ProbeError("WeKnora chunk list returned invalid JSON") from error
            if result.get("success") is not True or not isinstance(result.get("data"), list) or \
                    not result["data"]:
                raise ProbeError("WeKnora chunk list returned invalid result")
        checks[label] = True
    if checks["chunks"] != checks["preview"]:
        raise ProbeError("WeKnora chunk and preview access disagree")
    return checks["chunks"]


def search_probe(wk_origin, data, token):
    def run(scope):
        payload = json.dumps({"query": data["synthetic_query"], **scope}).encode()
        status, body = http("POST", wk_origin + "/api/v1/knowledge-search", headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json",
        }, payload=payload)
        if status in {403, 404}:
            return False
        if status != 200:
            raise ProbeError(f"WeKnora retrieval returned HTTP {status}")
        try:
            result = json.loads(body)
        except json.JSONDecodeError as error:
            raise ProbeError("WeKnora retrieval returned invalid JSON") from error
        rows = result.get("data")
        if result.get("success") is not True or not isinstance(rows, list):
            raise ProbeError("WeKnora retrieval returned an invalid result")
        if any(not isinstance(row, dict) or row.get("knowledge_id") != data["knowledge_id"]
               for row in rows):
            raise ProbeError("WeKnora retrieval returned content outside the fixture knowledge")
        return bool(rows)

    # A full-KB selection may discard an explicit document target inside it.
    # Exercise both policy paths independently.
    kb_found = run({"knowledge_base_ids": [data["knowledge_base_id"]]})
    document_found = run({"knowledge_ids": [data["knowledge_id"]]})
    if kb_found != document_found:
        raise ProbeError("KB and document retrieval decisions disagree")
    return kb_found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--case", required=True)
    parser.add_argument("--nextcloud-origin", required=True)
    parser.add_argument("--weknora-origin", required=True)
    parser.add_argument("--allow-loopback-http", action="store_true")
    parser.add_argument("--allow-remote-test-environment", action="store_true")
    parser.add_argument("--topology-ldif", type=Path,
                        help="fresh, attribute-limited LDAP export for primary/nested cases")
    parser.add_argument("--grant-group-guid", help="objectGUID of the group granting folder and KB access")
    parser.add_argument("--child-group-guid", help="direct child group objectGUID for nested_group")
    args = parser.parse_args()
    data, expectations = fixture(args.fixture, args.case)
    topology_result = None
    if args.case in {"primary_group", "nested_group"}:
        if args.topology_ldif is None or args.grant_group_guid is None:
            raise ProbeError("primary/nested phases require --topology-ldif and --grant-group-guid")
        if args.case == "nested_group" and args.child_group_guid is None:
            raise ProbeError("nested_group also requires --child-group-guid")
        try:
            age_seconds = dt.datetime.now(dt.timezone.utc).timestamp() - args.topology_ldif.stat().st_mtime
            if age_seconds < -5 or age_seconds > 120:
                raise ProbeError("LDAP topology export must be captured within 120 seconds of this probe")
            topology_result = validate_topology(
                data, args.case, args.topology_ldif, args.grant_group_guid,
                args.child_group_guid)
        except TopologyError as error:
            raise ProbeError("LDAP topology preflight failed: " + str(error)) from error
    nc_origin = origin(args.nextcloud_origin,
                       allow_remote_https=args.allow_remote_test_environment,
                       allow_loopback_http=args.allow_loopback_http)
    wk_origin = origin(args.weknora_origin,
                       allow_remote_https=args.allow_remote_test_environment,
                       allow_loopback_http=args.allow_loopback_http)
    passwords, (key_id, token) = required_environment()

    report = {"case": args.case, "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "checks": {}}
    if topology_result is not None:
        report["ldap_topology"] = topology_result
    for label in ("a", "b"):
        account = data["accounts"][label]
        expected = expectations[label]
        observed = {}
        observed["nextcloud_login"], observed["dav"] = dav_probe(
            nc_origin, account, passwords[label], data["file_id"])
        require_equal(observed["nextcloud_login"], expected["nextcloud_login"],
                      f"account {label} Nextcloud DAV account")
        require_equal(observed["dav"], expected["dav"], f"account {label} WebDAV")
        observed["source"] = source_probe(nc_origin, data, account, key_id, token)
        require_equal(observed["source"], expected["source"], f"account {label} source")
        user_token = weknora_login(wk_origin, account["weknora_identifier"], passwords[label])
        observed["ldap_login"] = user_token is not None
        require_equal(observed["ldap_login"], expected["ldap_login"],
                      f"account {label} WeKnora LDAP login")
        if user_token is None:
            observed["knowledge"] = observed["search"] = False
        else:
            observed["knowledge"] = knowledge_probe(wk_origin, data["knowledge_id"], user_token)
            observed["search"] = search_probe(wk_origin, data, user_token)
        require_equal(observed["knowledge"], expected["knowledge"],
                      f"account {label} knowledge read")
        require_equal(observed["search"], expected["search"],
                      f"account {label} retrieval")
        report["checks"][label] = observed
    print(json.dumps(report, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (ProbeError, OSError, ValueError, json.JSONDecodeError) as error:
        # Do not dump remote response bodies, request headers, passwords or JWTs.
        print("AD permission probe failed: " + str(error), file=sys.stderr)
        sys.exit(1)
