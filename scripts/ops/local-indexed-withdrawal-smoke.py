#!/usr/bin/env python3
"""Exercise first-stage indexed withdrawal on two explicitly disposable stacks.

The operator supplies isolated Compose projects, a local Nextcloud admin env,
an isolated WeKnora test admin env, and an explicit embedding model ID. The
model must be configured for the isolated mock embedding service. This test creates its own folder,
document, binding, KB, source pair, and event connection. It intentionally
leaves the stopped binding and paused source in place as protocol evidence;
discard only the two disposable Compose projects after recording the result.
No credential or document body is printed.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/integration_weknora/tests"))
from changes_http_smoke import file_id  # noqa: E402
from publication_http_smoke import login, request  # noqa: E402

pairing = runpy.run_path(str(Path(__file__).with_name("local-source-pairing.py")))
nc_request = pairing["nc_request"]
wk_login = pairing["wk_login"]
wk_request = pairing["wk_request"]
helpers = runpy.run_path(str(Path(__file__).with_name("local-source-pairing-abort-smoke.py")))
origin = helpers["origin"]
env_values = helpers["env_file_values"]
compose_container = helpers["compose_container"]
inspect = helpers["inspect"]
require_isolated_containers = helpers["require_isolated_containers"]
start_relay = helpers["start_relay"]
SAFE_PROJECT = helpers["SAFE_PROJECT"]
SHARED_PROJECTS = helpers["SHARED_PROJECTS"]
RECEIVER = "/api/v1/integrations/nextcloud/events"


def expect(actual, wanted, step):
    if actual != wanted:
        raise RuntimeError(f"{step}: HTTP {actual}, expected {wanted}")


def row(status, body, field, step):
    expect(status, 200, step)
    value = body.get(field) if isinstance(body, dict) else None
    if not isinstance(value, dict):
        raise RuntimeError(f"{step}: missing {field}")
    return value


def sql_json(container, query):
    result = subprocess.run([
        "docker", "exec", container, "sh", "-c",
        'psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"',
        "sh", query,
    ], text=True, capture_output=True, timeout=25, check=False)
    if result.returncode:
        raise RuntimeError("isolated PostgreSQL read failed")
    lines = result.stdout.strip().splitlines()
    if len(lines) != 1:
        raise RuntimeError("isolated PostgreSQL returned an unexpected row count")
    return json.loads(lines[0])


def index_proof(container, source_id, file_id_value):
    # UUID and decimal file ID are validated before they enter this read-only SQL.
    query = ("SELECT jsonb_build_object("
             "'state',v.state,'parse',k.parse_status,'enabled',k.enable_status,"
             "'etag',k.metadata->>'nextcloud_etag','desired',v.desired_etag,"
             "'chunks',(SELECT COUNT(*) FROM chunks c WHERE c.knowledge_id=k.id "
             "AND c.deleted_at IS NULL AND c.is_enabled AND c.index_status='ready'),"
             "'embeddings',(SELECT COUNT(*) FROM embeddings e WHERE e.knowledge_id=k.id "
             "AND e.is_enabled)) FROM nextcloud_source_versions v "
             "JOIN knowledges k ON k.id=v.candidate_knowledge_id "
             f"WHERE v.datasource_id='{source_id}' "
             f"AND v.external_id LIKE '%:{file_id_value}' AND k.deleted_at IS NULL")
    try:
        proof = sql_json(container, query)
    except (ValueError, RuntimeError):
        return None
    if (proof.get("state") == "published" and proof.get("parse") == "completed" and
            proof.get("enabled") == "enabled" and proof.get("etag") and
            proof.get("etag") == proof.get("desired") and
            proof.get("chunks", 0) >= 1 and proof.get("embeddings", 0) >= 1):
        return proof
    return None


def running_syncs(container, source_id):
    return int(sql_json(container, "SELECT to_jsonb(COUNT(*)) FROM sync_logs "
                        f"WHERE data_source_id='{source_id}' AND status='running'"))


def signed_old_event(base, credential):
    body = {"connection_id": credential["connection_id"],
            "nextcloud_instance_id": credential["nextcloud_instance_id"],
            "binding_id": credential["binding_id"], "after_event_id": "0",
            "events": [{"event_id": "1", "type": "reconcile"}]}
    raw = json.dumps(body, separators=(",", ":")).encode()
    timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
    canonical = "\n".join((
        "nextcloud-event-hmac-sha256-v1", "POST", RECEIVER, "",
        hashlib.sha256(raw).hexdigest(), timestamp, nonce,
        credential["connection_id"], credential["key_id"],
    )).encode()
    headers = {"Content-Type": "application/json",
               "X-Nextcloud-Connection-Id": credential["connection_id"],
               "X-Nextcloud-Key-Id": credential["key_id"],
               "X-Nextcloud-Timestamp": timestamp, "X-Nextcloud-Nonce": nonce,
               "X-Nextcloud-Signature": hmac.new(credential["secret"].encode(),
                                                 canonical, hashlib.sha256).hexdigest()}
    outgoing = urllib.request.Request(base + RECEIVER, data=raw, method="POST",
                                      headers=headers)
    try:
        with urllib.request.urlopen(outgoing, timeout=20) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nextcloud-origin", required=True)
    parser.add_argument("--weknora-origin", required=True)
    parser.add_argument("--nextcloud-env-file", required=True, type=Path)
    parser.add_argument("--weknora-admin-env-file", required=True, type=Path)
    parser.add_argument("--nextcloud-compose-project", required=True)
    parser.add_argument("--weknora-compose-project", required=True)
    parser.add_argument("--weknora-app-service", default="app")
    parser.add_argument("--weknora-db-service", default="pg")
    parser.add_argument("--embedding-model-id", required=True,
                        help="ID of a model configured for the isolated mock embedding service")
    parser.add_argument("--relay-port", type=int, default=18092)
    args = parser.parse_args()
    nc_origin = origin(args.nextcloud_origin)
    wk_origin = origin(args.weknora_origin)
    for project in (args.nextcloud_compose_project, args.weknora_compose_project):
        if not SAFE_PROJECT.fullmatch(project) or project in SHARED_PROJECTS:
            parser.error("only explicit disposable Compose projects are allowed")
    if args.nextcloud_compose_project == args.weknora_compose_project:
        parser.error("Compose projects must differ")
    for service in (args.weknora_app_service, args.weknora_db_service):
        if not SAFE_PROJECT.fullmatch(service):
            parser.error("invalid isolated service")
    if not SAFE_PROJECT.fullmatch(args.embedding_model_id):
        parser.error("invalid isolated embedding model ID")
    if not 1024 <= args.relay_port <= 65535:
        parser.error("invalid relay port")
    values = env_values(args.nextcloud_env_file)
    account = env_values(args.weknora_admin_env_file)
    owner, password = values.get("NEXTCLOUD_ADMIN_USER"), values.get("NEXTCLOUD_ADMIN_PASSWORD")
    if not owner or not password or not account.get("WEKNORA_TEST_ADMIN_EMAIL") or not account.get("WEKNORA_TEST_ADMIN_PASSWORD"):
        parser.error("isolated administrator credentials are missing")
    os.environ.update({k: account[k] for k in
                       ("WEKNORA_TEST_ADMIN_EMAIL", "WEKNORA_TEST_ADMIN_PASSWORD")})
    nc_container = compose_container(args.nextcloud_compose_project, "nextcloud")
    wk_container = compose_container(args.weknora_compose_project, args.weknora_app_service)
    db_container = compose_container(args.weknora_compose_project, args.weknora_db_service)
    db_info = inspect(db_container)
    if db_info["Config"]["Labels"].get("com.docker.compose.project") != args.weknora_compose_project:
        parser.error("PostgreSQL is outside the isolated WeKnora project")
    upstream = require_isolated_containers(inspect(nc_container), inspect(wk_container),
                                           nc_origin.port, wk_origin.port, args.relay_port)
    nc_base, wk_base = args.nextcloud_origin, args.weknora_origin
    nc_api = nc_base + "/index.php/apps/integration_weknora/api/v1"
    admin, csrf = login(nc_base, owner, password)
    wk_token, tenant_id = wk_login(wk_base)
    suffix = secrets.token_hex(8)
    binding = "indexed-drill-" + suffix
    pair_op, decom_op = str(uuid.uuid4()), str(uuid.uuid4())
    folder = nc_base + "/remote.php/dav/files/" + urllib.parse.quote(owner, safe="") + "/" + binding
    document = folder + "/indexed.txt"
    dav_headers = {"Authorization": "Basic " + base64.b64encode(
        f"{owner}:{password}".encode()).decode()}
    binding_url = nc_api + "/admin/bindings/" + binding
    pair_url, decom_url = binding_url + "/source-pairing", binding_url + "/decommission"
    wk_pairs = "/api/v1/datasource/nextcloud-source-pairings"
    machine = urllib.request.build_opener()
    relay = None
    stage = "create fixture"
    passed = False
    kb_id = source_id = key_id = None
    try:
        relay = start_relay(wk_container, binding, pair_op, args.relay_port,
                            "python:3.12-alpine", upstream, no_fault=True)
        expect(request(machine, folder, "MKCOL", dav_headers)[0], 201, "create folder")
        expect(request(machine, document, "PUT", dav_headers,
                       b"synthetic indexed withdrawal fixture, no user content\n")[0],
               201, "create document")
        source_file_id = file_id(document, dav_headers)
        if not re.fullmatch(r"[1-9][0-9]*", str(source_file_id)):
            raise RuntimeError("invalid synthetic file ID")
        root_id = file_id(folder, dav_headers)
        status, body = nc_request(admin, csrf, nc_api + "/admin/bindings", "POST", {
            "id": binding, "name": binding, "owner_uid": owner, "root_file_id": root_id})
        expect(status, 201, "create binding")
        if body.get("binding", {}).get("id") != binding:
            raise RuntimeError("binding identity mismatch")
        status, body = wk_request(wk_base, wk_token, "POST", "/api/v1/knowledge-bases",
                                  {"name": binding, "type": "document",
                                   "embedding_model_id": args.embedding_model_id})
        expect(status, 201, "create dedicated KB")
        kb = body.get("data") if isinstance(body, dict) else None
        kb_id = kb.get("id") if isinstance(kb, dict) else None
        if not isinstance(kb_id, str) or kb.get("name") != binding or str(kb.get("tenant_id")) != tenant_id:
            raise RuntimeError("KB identity mismatch")

        stage = "pair source"
        status, body = nc_request(admin, csrf, pair_url, "POST", {
            "operation_id": pair_op, "tenant_id": tenant_id, "knowledge_base_id": kb_id})
        expect(status, 201, "prepare exact pair")
        prepared = body.get("pairing") if isinstance(body, dict) else None
        machine_token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(prepared, dict) or prepared.get("operation_id") != pair_op or not machine_token:
            raise RuntimeError("pair preparation mismatch")
        key_id = prepared["key_id"]
        status, _ = wk_request(wk_base, wk_token, "POST", wk_pairs, {
            "knowledge_base_id": kb_id, "base_url": f"http://127.0.0.1:{args.relay_port}",
            "binding_id": binding, "operation_id": pair_op,
            "instance_id": prepared["instance_id"],
            "publication_epoch": prepared["publication_epoch"],
            "key_id": key_id, "token": machine_token})
        if status not in (200, 201, 202):
            raise RuntimeError(f"WeKnora pair HTTP {status}")
        for _ in range(8):
            nc_status, nc_body = nc_request(admin, csrf, pair_url, "GET")
            wk_status, wk_body = wk_request(wk_base, wk_token, "GET", wk_pairs + "/" + pair_op)
            if nc_status == wk_status == 200 and nc_body.get("pairing", {}).get("state") == wk_body.get("pairing", {}).get("state") == "active":
                break
            status, _ = wk_request(wk_base, wk_token, "POST", wk_pairs + "/" + pair_op + "/retry")
            if status not in (200, 202):
                raise RuntimeError(f"pair retry HTTP {status}")
            time.sleep(.5)
        else:
            raise RuntimeError("pair did not become active")
        source_id = wk_body["pairing"]["data_source_id"]
        if str(uuid.UUID(source_id)) != source_id:
            raise RuntimeError("invalid source UUID")

        stage = "index synthetic document"
        status, _ = wk_request(wk_base, wk_token, "POST", f"/api/v1/datasource/{source_id}/sync")
        if status not in (200, 409):
            raise RuntimeError(f"initial sync HTTP {status}")
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            proof = index_proof(db_container, source_id, source_file_id)
            if proof and running_syncs(db_container, source_id) == 0:
                break
            time.sleep(2)
        else:
            raise RuntimeError("synthetic document did not reach published indexed state")

        stage = "create old event key"
        event_path = f"/api/v1/datasource/{source_id}/nextcloud-event-connection"
        status, credential = wk_request(wk_base, wk_token, "POST", event_path)
        expect(status, 201, "create old event key")
        if credential.get("status") != "active" or not credential.get("secret"):
            raise RuntimeError("event credential missing")

        stage = "stop source and withdraw"
        status, body = nc_request(admin, csrf, decom_url, "POST", {"operation_id": decom_op})
        intent = row(status, body, "decommission", "prepare stopped intent")
        if intent.get("state") != "prepared" or intent.get("cleanup_state") != "pending":
            raise RuntimeError("Nextcloud intent not prepared")
        for field, value in (("operation_id", decom_op), ("pair_operation_id", pair_op),
                             ("binding_id", binding), ("knowledge_base_id", kb_id),
                             ("data_source_id", source_id), ("key_id", key_id)):
            if intent.get(field) != value:
                raise RuntimeError("decommission tuple mismatch")
        status, body = wk_request(wk_base, wk_token, "POST",
                                  f"{wk_pairs}/{pair_op}/indexed-withdrawal",
                                  {"operation_id": decom_op})
        expect(status, 202, "indexed withdrawal")
        withdrawn = body.get("withdrawal") if isinstance(body, dict) else None
        if not isinstance(withdrawn, dict) or withdrawn.get("logical_withdrawn") is not True or withdrawn.get("inventory_complete") is not False:
            raise RuntimeError("indexed withdrawal response overclaimed completion")
        status, body = wk_request(wk_base, wk_token, "GET",
                                  f"{wk_pairs}/{pair_op}/indexed-withdrawal")
        status_row = row(status, body, "withdrawal", "read durable withdrawal")
        if status_row.get("operation_id") != decom_op or not status_row.get("logical_withdrawn") or status_row.get("inventory_complete") is not False:
            raise RuntimeError("durable withdrawal mismatch")
        inventory = sql_json(db_container, "SELECT jsonb_object_agg(kind,n) FROM "
                             "(SELECT kind,COUNT(*) n FROM nextcloud_indexed_withdrawal_items "
                             f"WHERE operation_id='{decom_op}' GROUP BY kind) x")
        for kind in ("knowledge", "chunk", "postgres_embedding", "unverified_external_index"):
            if inventory.get(kind, 0) < 1:
                raise RuntimeError("observed inventory omitted indexed object kind")

        stage = "verify retained identities and revoked event key"
        current = row(*nc_request(admin, csrf, decom_url, "GET"),
                      "decommission", "read Nextcloud intent")
        if current.get("state") != "prepared" or current.get("cleanup_state") != "pending" or current.get("inventory_sha256"):
            raise RuntimeError("unexpected Nextcloud ACK")
        expect(nc_request(admin, csrf, decom_url + "/finalize", "POST",
                          {"operation_id": decom_op})[0], 409, "reject premature finalization")
        status, body = nc_request(admin, csrf, nc_api + "/admin/bindings", "GET")
        expect(status, 200, "read retained binding")
        listed = [item for item in body.get("bindings", [])
                  if isinstance(item, dict) and item.get("id") == binding]
        if len(listed) != 1 or listed[0].get("publication_state") != "stopped":
            raise RuntimeError("Nextcloud binding was not retained stopped")
        signed_intent = f"{nc_api}/bindings/{binding}/decommission/{decom_op}"
        expect(request(machine, signed_intent, "GET", {
            "Authorization": "Bearer " + machine_token, "X-WeKnora-Key-Id": key_id})[0],
            200, "retained paired source key")
        status, source = wk_request(wk_base, wk_token, "GET", f"/api/v1/datasource/{source_id}")
        expect(status, 200, "read paused source")
        if source.get("status") != "paused":
            raise RuntimeError("WeKnora source not paused")
        status, event_status = wk_request(wk_base, wk_token, "GET", event_path)
        expect(status, 200, "read revoked connection")
        if event_status.get("status") != "revoked":
            raise RuntimeError("event connection not revoked")
        expect(signed_old_event(wk_base, credential), 401, "old event key")
        passed = True
        print(json.dumps({"result": "passed", "binding_id": binding,
                          "knowledge_base_id": kb_id, "data_source_id": source_id,
                          "pair_operation_id": pair_op, "decommission_operation_id": decom_op,
                          "indexed_chunks": proof["chunks"],
                          "indexed_embeddings": proof["embeddings"],
                          "observed_inventory_kinds": inventory,
                          "logical_withdrawn": True, "inventory_complete": False,
                          "nextcloud_ack": False, "old_event_key_http": 401}, sort_keys=True))
    finally:
        if relay is not None:
            subprocess.run(["docker", "rm", "-f", relay], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=20, check=False)
        if not passed:
            print(f"owned fixture: binding={binding} kb={kb_id or 'uncreated'} "
                  f"source={source_id or 'uncreated'} pair_operation={pair_op} "
                  f"decommission_operation={decom_op} stage={stage}; "
                  "retain isolated stacks for diagnosis", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"indexed withdrawal smoke failed ({type(error).__name__}); "
              "inspect only the owned disposable stacks", file=sys.stderr)
        sys.exit(1)
