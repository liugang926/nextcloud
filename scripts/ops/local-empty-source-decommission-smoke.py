#!/usr/bin/env python3
"""Exercise virgin empty-source retirement on two disposable Compose stacks.

This creates its own empty WebDAV folder, binding and dedicated WeKnora KB.
The successful protocol intentionally retains the paused WeKnora source and
pair tombstone in that disposable stack. It never targets an existing source.
It prints operation IDs for recovery, never the one-time machine credential.

Requires WEKNORA_TEST_ADMIN_EMAIL and WEKNORA_TEST_ADMIN_PASSWORD in the
process environment and an isolated Nextcloud .env with administrator login.
Both stacks must already run the decommission-capable app and migrations.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import sys
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "apps/integration_weknora/tests"))
from changes_http_smoke import file_id  # noqa: E402
from publication_http_smoke import login, request  # noqa: E402

_pairing = runpy.run_path(str(Path(__file__).with_name("local-source-pairing.py")))
nc_request = _pairing["nc_request"]
wk_login = _pairing["wk_login"]
wk_request = _pairing["wk_request"]
_abort = runpy.run_path(str(Path(__file__).with_name("local-source-pairing-abort-smoke.py")))
origin = _abort["origin"]
env_file_values = _abort["env_file_values"]
compose_container = _abort["compose_container"]
inspect = _abort["inspect"]
SAFE_PROJECT = _abort["SAFE_PROJECT"]
SHARED_PROJECTS = _abort["SHARED_PROJECTS"]
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def expect(actual, expected, stage):
    if actual != expected:
        raise RuntimeError(f"{stage}: HTTP {actual}, expected {expected}")


def object_row(status, body, key, stage):
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get(key), dict):
        raise RuntimeError(f"{stage}: HTTP {status} or missing {key}")
    return body[key]


def same_fields(row, expected, names, stage):
    if any(row.get(name) != expected.get(name) for name in names):
        raise RuntimeError(f"{stage}: operation identity changed")


def require_isolated_stacks(nc_info, wk_info, nc_port, wk_port):
    nc_ports = nc_info["NetworkSettings"]["Ports"].get("80/tcp") or []
    if not any(int(item["HostPort"]) == nc_port for item in nc_ports):
        raise RuntimeError("Nextcloud origin is not this isolated container")
    if not any(int(item["HostPort"]) == wk_port
               for mappings in wk_info["NetworkSettings"]["Ports"].values()
               for item in (mappings or [])):
        raise RuntimeError("WeKnora origin is not this isolated container")
    nc_networks = nc_info["NetworkSettings"]["Networks"]
    wk_networks = wk_info["NetworkSettings"]["Networks"]
    shared = {name for name in set(nc_networks) & set(wk_networks)
              if nc_networks[name].get("NetworkID") and
              nc_networks[name]["NetworkID"] == wk_networks[name].get("NetworkID")}
    hostname = nc_info.get("Name", "").lstrip("/")
    if (not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", hostname) or
            not any(hostname in (nc_networks[name].get("Aliases") or []) for name in shared)):
        raise RuntimeError("WeKnora cannot resolve this unique isolated Nextcloud container")
    machine_origin = "http://" + hostname
    env = dict(item.split("=", 1) for item in wk_info["Config"]["Env"] if "=" in item)
    allowed = {item.strip() for item in env.get("WEKNORA_NEXTCLOUD_ALLOWED_ORIGINS", "").split(",")}
    if env.get("WEKNORA_NEXTCLOUD_DEV_HTTP") != "1" or machine_origin not in allowed:
        raise RuntimeError("isolated WeKnora has not approved the exact machine origin")
    return machine_origin


def empty_dav_folder(opener, url, headers):
    status, body = request(opener, url, "PROPFIND", {**headers, "Depth": "1"})
    if status != 207:
        return False
    try:
        root = ET.fromstring(body)
        hrefs = [node.text or "" for node in root.findall("{DAV:}response/{DAV:}href")]
        folder_path = urllib.parse.urlsplit(url).path.rstrip("/")
        return len(hrefs) == 1 and urllib.parse.urlsplit(hrefs[0]).path.rstrip("/") == folder_path
    except ET.ParseError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nextcloud-origin", required=True,
                        help="loopback origin of a disposable Nextcloud Compose stack")
    parser.add_argument("--weknora-origin", required=True,
                        help="loopback origin of a disposable WeKnora Compose stack")
    parser.add_argument("--nextcloud-env-file", required=True,
                        help="isolated Nextcloud .env; never printed")
    parser.add_argument("--nextcloud-compose-project", required=True)
    parser.add_argument("--weknora-compose-project", required=True)
    parser.add_argument("--weknora-app-service", default="app")
    args = parser.parse_args()
    try:
        nc_origin = origin(args.nextcloud_origin)
        wk_origin = origin(args.weknora_origin)
    except ValueError as error:
        parser.error(str(error))
    for project in (args.nextcloud_compose_project, args.weknora_compose_project):
        if not SAFE_PROJECT.fullmatch(project) or project in SHARED_PROJECTS:
            parser.error("use explicit disposable Compose projects, not shared projects")
    if args.nextcloud_compose_project == args.weknora_compose_project:
        parser.error("provide distinct Compose projects")
    if not SAFE_PROJECT.fullmatch(args.weknora_app_service):
        parser.error("invalid WeKnora app service name")
    env_path = Path(args.nextcloud_env_file)
    if not env_path.is_file():
        parser.error("isolated Nextcloud env file does not exist")
    values = env_file_values(env_path)
    owner = values.get("NEXTCLOUD_ADMIN_USER")
    password = values.get("NEXTCLOUD_ADMIN_PASSWORD")
    if not owner or not password:
        parser.error("isolated Nextcloud env file lacks administrator login")
    nc_info = inspect(compose_container(args.nextcloud_compose_project, "nextcloud"))
    wk_info = inspect(compose_container(args.weknora_compose_project, args.weknora_app_service))
    machine_origin = require_isolated_stacks(nc_info, wk_info, nc_origin.port, wk_origin.port)

    nc_base = args.nextcloud_origin
    wk_base = args.weknora_origin
    api = nc_base + "/index.php/apps/integration_weknora/api/v1"
    admin, csrf = login(nc_base, owner, password)
    wk_token, tenant_id = wk_login(wk_base)
    binding = "decom-empty-" + secrets.token_hex(8)
    folder_url = (nc_base + "/remote.php/dav/files/" +
                  urllib.parse.quote(owner, safe="") + "/" + binding)
    dav_headers = {"Authorization": "Basic " + base64.b64encode(
        f"{owner}:{password}".encode()).decode()}
    binding_url = f"{api}/admin/bindings/{binding}"
    pair_url = binding_url + "/source-pairing"
    decom_url = binding_url + "/decommission"
    pair_path = "/api/v1/datasource/nextcloud-source-pairings"
    kb_path = "/api/v1/knowledge-bases"
    machine = urllib.request.build_opener()
    pair_op = str(uuid.uuid4())
    decom_op = str(uuid.uuid4())
    wrong_op = str(uuid.uuid4())
    owned_folder = owned_binding = owned_kb = finalized = False
    cleanup_failed = passed = False
    kb_id = source_id = machine_token = key_id = None
    stage = "create empty fixtures"
    try:
        status, _ = request(machine, folder_url, "MKCOL", dav_headers)
        expect(status, 201, "create owned empty folder")
        owned_folder = True
        root_id = file_id(folder_url, dav_headers)
        status, body = nc_request(admin, csrf, f"{api}/admin/bindings", "POST", {
            "id": binding, "name": binding, "owner_uid": owner, "root_file_id": root_id})
        expect(status, 201, "create owned binding")
        if body.get("binding", {}).get("id") != binding:
            raise RuntimeError("binding create returned a different ID")
        owned_binding = True
        status, body = wk_request(wk_base, wk_token, "POST", kb_path,
                                  {"name": binding, "type": "document"})
        expect(status, 201, "create owned empty KB")
        kb = body.get("data") if isinstance(body, dict) else None
        kb_id = kb.get("id") if isinstance(kb, dict) else None
        if (not isinstance(kb_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", kb_id) or
                kb.get("name") != binding or str(kb.get("tenant_id")) != tenant_id):
            raise RuntimeError("knowledge base create returned a different fixture")
        owned_kb = True

        stage = "establish exact fresh pair"
        status, body = nc_request(admin, csrf, pair_url, "POST", {
            "operation_id": pair_op, "tenant_id": tenant_id, "knowledge_base_id": kb_id})
        expect(status, 201, "prepare fresh source pair")
        prepared = body.get("pairing") if isinstance(body, dict) else None
        machine_token = body.get("token") if isinstance(body, dict) else None
        if (not isinstance(prepared, dict) or prepared.get("state") != "pending" or
                prepared.get("operation_id") != pair_op or prepared.get("binding_id") != binding or
                prepared.get("knowledge_base_id") != kb_id or
                not isinstance(machine_token, str) or not machine_token):
            raise RuntimeError("fresh pairing preparation returned a different intent")
        key_id = prepared.get("key_id")
        payload = {"knowledge_base_id": kb_id, "base_url": machine_origin,
                   "binding_id": binding, "operation_id": pair_op,
                   "instance_id": prepared.get("instance_id"),
                   "publication_epoch": prepared.get("publication_epoch"),
                   "key_id": key_id, "token": machine_token}
        status, _ = wk_request(wk_base, wk_token, "POST", pair_path, payload)
        del payload
        if status not in (200, 201, 202):
            raise RuntimeError(f"WeKnora fresh pairing HTTP {status}")
        for attempt in range(4):
            nc_status, nc_body = nc_request(admin, csrf, pair_url, "GET")
            wk_status, wk_body = wk_request(wk_base, wk_token, "GET", f"{pair_path}/{pair_op}")
            if nc_status == 200 and wk_status == 200:
                nc_pair = object_row(nc_status, nc_body, "pairing", "Nextcloud pair status")
                wk_pair = object_row(wk_status, wk_body, "pairing", "WeKnora pair status")
                if nc_pair.get("state") == wk_pair.get("state") == "active":
                    break
            if attempt == 3:
                raise RuntimeError("fresh pair is not active; recover with the pair operation UUID")
            retry_status, _ = wk_request(wk_base, wk_token, "POST", f"{pair_path}/{pair_op}/retry")
            if retry_status not in (200, 202):
                raise RuntimeError(f"pair retry HTTP {retry_status}")
            time.sleep(0.5)
        same_fields(wk_pair, nc_pair,
                    ("operation_id", "binding_id", "knowledge_base_id", "data_source_id",
                     "instance_id", "key_id"), "fresh pair")
        source_id = wk_pair["data_source_id"]
        if not isinstance(source_id, str) or not source_id:
            raise RuntimeError("active pair has no source ID")

        stage = "begin source retirement"
        status, body = nc_request(admin, csrf, decom_url, "POST", {"operation_id": decom_op})
        nc_intent = object_row(status, body, "decommission", "Nextcloud begin")
        if (nc_intent.get("state") != "prepared" or
                nc_intent.get("cleanup_state") != "pending"):
            raise RuntimeError("Nextcloud did not prepare empty-source retirement")
        expected = {"operation_id": decom_op, "pair_operation_id": pair_op,
                    "binding_id": binding, "instance_id": prepared["instance_id"],
                    "tenant_id": tenant_id, "knowledge_base_id": kb_id,
                    "data_source_id": source_id, "key_id": key_id}
        same_fields(nc_intent, expected, expected.keys(), "Nextcloud begin")
        status, body = nc_request(admin, csrf, decom_url, "POST", {"operation_id": decom_op})
        repeated = object_row(status, body, "decommission", "idempotent Nextcloud begin")
        same_fields(repeated, nc_intent, expected.keys(), "idempotent Nextcloud begin")
        expect(nc_request(admin, csrf, decom_url, "POST", {"operation_id": wrong_op})[0],
               409, "wrong begin operation")
        expect(nc_request(admin, csrf, decom_url + "/finalize", "POST",
                          {"operation_id": decom_op})[0], 409, "finalize before remote ACK")
        expect(wk_request(wk_base, wk_token, "POST", f"{pair_path}/{pair_op}/decommission",
                          {"operation_id": wrong_op})[0], 409, "wrong remote operation")

        stage = "remote empty-inventory ACK"
        wk_decom = None
        for attempt in range(5):
            status, body = wk_request(wk_base, wk_token, "POST",
                                      f"{pair_path}/{pair_op}/decommission",
                                      {"operation_id": decom_op})
            if status == 200:
                wk_decom = object_row(status, body, "decommission", "WeKnora ACK")
                if wk_decom.get("state") == "acknowledged":
                    break
            elif status != 202:
                raise RuntimeError(f"WeKnora decommission HTTP {status}")
            if attempt == 4:
                raise RuntimeError("remote ACK remains uncertain; retry the same decommission UUID")
            time.sleep(0.5)
        same_fields(wk_decom, expected,
                    ("operation_id", "pair_operation_id", "binding_id", "instance_id",
                     "knowledge_base_id", "data_source_id", "key_id"), "WeKnora ACK")
        if wk_decom.get("inventory_sha256") != EMPTY_SHA256:
            raise RuntimeError("WeKnora did not attest an empty inventory")
        status, body = nc_request(admin, csrf, decom_url, "GET")
        acknowledged = object_row(status, body, "decommission", "Nextcloud ACK status")
        same_fields(acknowledged, nc_intent, expected.keys(), "Nextcloud ACK status")
        if acknowledged.get("state") != "acknowledged" or acknowledged.get("inventory_sha256") != EMPTY_SHA256:
            raise RuntimeError("Nextcloud has no durable empty-inventory ACK")
        status, body = wk_request(wk_base, wk_token, "GET", f"{pair_path}/{pair_op}/decommission")
        remote_status = object_row(status, body, "decommission", "WeKnora ACK status")
        if remote_status.get("state") != "acknowledged" or remote_status.get("operation_id") != decom_op:
            raise RuntimeError("WeKnora has no durable empty-inventory checkpoint")
        status, body = wk_request(wk_base, wk_token, "POST", f"{pair_path}/{pair_op}/decommission",
                                  {"operation_id": decom_op})
        retry = object_row(status, body, "decommission", "idempotent WeKnora ACK")
        same_fields(retry, wk_decom, ("operation_id", "pair_operation_id", "binding_id",
                                      "knowledge_base_id", "data_source_id", "key_id"),
                    "idempotent WeKnora ACK")

        stage = "finalize exact retirement"
        expect(nc_request(admin, csrf, decom_url + "/finalize", "POST",
                          {"operation_id": wrong_op})[0], 404, "wrong finalize operation")
        status, body = nc_request(admin, csrf, decom_url + "/finalize", "POST",
                                  {"operation_id": decom_op})
        final = object_row(status, body, "decommission", "Nextcloud finalize")
        if final.get("state") != "finalized" or final.get("cleanup_state") != "empty_confirmed":
            raise RuntimeError("Nextcloud did not finalize exact empty source")
        same_fields(final, nc_intent, expected.keys(), "Nextcloud finalize")
        finalized = True
        owned_binding = False
        status, body = nc_request(admin, csrf, decom_url + "/finalize", "POST",
                                  {"operation_id": decom_op})
        repeated_final = object_row(status, body, "decommission", "idempotent finalize")
        same_fields(repeated_final, final, expected.keys(), "idempotent finalize")
        if repeated_final.get("state") != "finalized":
            raise RuntimeError("finalize retry did not preserve state")

        stage = "verify retired credentials and paused tombstone"
        signed_intent_url = f"{api}/bindings/{binding}/decommission/{decom_op}"
        expect(request(machine, signed_intent_url, headers={
            "Authorization": "Bearer " + machine_token, "X-WeKnora-Key-Id": key_id})[0],
            401, "retired pair credential")
        status, body = nc_request(admin, csrf, f"{api}/admin/bindings", "GET")
        expect(status, 200, "binding list after finalize")
        bindings = body.get("bindings") if isinstance(body, dict) else None
        if not isinstance(bindings, list) or any(isinstance(row, dict) and row.get("id") == binding
                                                  for row in bindings):
            raise RuntimeError("retired binding remains in active list")
        status, body = wk_request(wk_base, wk_token, "GET", f"/api/v1/datasource/{source_id}")
        expect(status, 200, "paused source tombstone")
        if body.get("id") != source_id or body.get("status") != "paused" or body.get("knowledge_base_id") != kb_id:
            raise RuntimeError("WeKnora source is not the paused owned tombstone")
        status, body = wk_request(wk_base, wk_token, "GET", f"{pair_path}/{pair_op}/decommission")
        remote_status = object_row(status, body, "decommission", "remote tombstone")
        if remote_status.get("operation_id") != decom_op or remote_status.get("state") != "acknowledged":
            raise RuntimeError("WeKnora decommission tombstone changed")
        del machine_token
        machine_token = None
        passed = True
    finally:
        # Never delete an uncertain paired binding or its KB. The paused source
        # and immutable pairing are intentional evidence even after success.
        if finalized and owned_folder:
            try:
                if empty_dav_folder(machine, folder_url, dav_headers):
                    status, _ = request(machine, folder_url, "DELETE", dav_headers)
                    if status == 204:
                        owned_folder = False
                if owned_folder:
                    cleanup_failed = True
            except Exception:
                cleanup_failed = True
        if owned_binding or owned_folder or owned_kb:
            print(f"owned fixture: binding={binding} kb={kb_id or 'uncreated'} "
                  f"source={source_id or 'uncreated'} pair_operation={pair_op} "
                  f"decommission_operation={decom_op} stage={stage} "
                  f"finalized={str(finalized).lower()}; discard the disposable stacks after review",
                  file=sys.stderr)
    if cleanup_failed:
        raise RuntimeError("owned empty folder cleanup was incomplete")
    if passed:
        print("empty-source decommission smoke passed: exact ACK, retired credential, paused tombstone")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Imported helpers can include response bodies or credentials in an
        # exception. Emit only the stage-neutral exception type.
        print(f"empty-source decommission smoke failed ({type(error).__name__}); "
              "use the printed fixture UUIDs to inspect the isolated stacks",
              file=sys.stderr)
        sys.exit(1)
