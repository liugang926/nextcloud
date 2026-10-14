#!/usr/bin/env python3
"""Exercise file and nested folder moves, trash, and restore in owned fixtures.

The default invocation prepares, bootstraps, probes, and destroys a unique
loopback-only synthetic LDAP Compose project, including a second non-overlapping
binding with its own knowledge base. ``--scratch`` reuses an already bootstrapped
owned fixture and removes only this probe's uniquely named files.
``--cross-bindings-only`` starts a fresh fixture and runs that folder drill
without the earlier single-binding move/trash/restore sequence.
No shared development Compose project, credentials, or remote URL is accepted.
"""

import argparse
import base64
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "apps/integration_weknora/tests"))
from changes_http_smoke import file_id, request as dav_request  # noqa: E402
from publication_http_smoke import login  # noqa: E402

owner = runpy.run_path(str(HERE / "synthetic-ldap-fixture.py"))
event = runpy.run_path(str(HERE / "isolated-event-rebind-smoke.py"))
index = runpy.run_path(str(HERE / "local-indexed-withdrawal-smoke.py"))
synthetic = runpy.run_path(str(HERE / "synthetic-ldap-e2e.py"))
handoff = runpy.run_path(str(HERE / "synthetic-ldap-ask-handoff.py"))
owned_state = owner["owned_state"]
sql_json = index["sql_json"]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(*args, timeout=60):
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout,
                          check=True).stdout


def dav(url, method, headers, body=None, expected=(200,)):
    status, response = dav_request(url, method, headers, body)
    require(status in expected, f"DAV {method} returned HTTP {status}; expected {expected}")
    return response


def move(source, destination, headers):
    dav(source, "MOVE", {**headers, "Destination": destination}, expected=(201, 204))


def event_id(state, binding, file_number, event_type, after=0):
    require(re.fullmatch(r"[A-Za-z0-9._-]{1,128}", binding) is not None,
            "invalid fixture binding")
    require(type(file_number) is int and file_number > 0 and type(after) is int and after >= 0,
            "invalid fixture event identity")
    require(event_type in {"upsert", "metadata", "delete", "subtree_deleted", "subtree_scan"},
            "unexpected event type")
    sql = ("SELECT COALESCE(MAX(id),0) FROM oc_weknora_outbox "
           f"WHERE binding_id='{binding}' AND file_id={file_number} "
           f"AND event_type='{event_type}' AND id>{after}")
    value = run("docker", "exec", state["project"] + "-nc-db-1", "psql", "-U",
                "nextcloud", "-d", "nextcloud", "-Atc", sql).strip()
    require(value.isdecimal() and int(value) > after,
            f"{event_type} did not persist a new outbox event")
    return int(value)


def binding_cursor(state, binding):
    require(re.fullmatch(r"[A-Za-z0-9._-]{1,128}", binding) is not None,
            "invalid fixture binding")
    sql = ("SELECT COALESCE(MAX(id),0) FROM oc_weknora_outbox "
           f"WHERE binding_id='{binding}'")
    value = run("docker", "exec", state["project"] + "-nc-db-1", "psql", "-U",
                "nextcloud", "-d", "nextcloud", "-Atc", sql).strip()
    require(value.isdecimal(), "invalid binding outbox cursor")
    return int(value)


def deletion_hints_after(state, binding, after):
    require(re.fullmatch(r"[A-Za-z0-9._-]{1,128}", binding) is not None and
            type(after) is int and after >= 0, "invalid deletion hint scope")
    sql = ("SELECT COUNT(*) FROM oc_weknora_outbox "
           f"WHERE binding_id='{binding}' AND id>{after} "
           "AND event_type IN ('delete','subtree_deleted')")
    value = run("docker", "exec", state["project"] + "-nc-db-1", "psql", "-U",
                "nextcloud", "-d", "nextcloud", "-Atc", sql).strip()
    require(value.isdecimal(), "invalid deletion hint count")
    return int(value)


def version(db, source_id, file_number):
    require(re.fullmatch(r"[0-9a-fA-F-]{36}", source_id) is not None and
            type(file_number) is int and file_number > 0, "invalid version identity")
    query = ("SELECT jsonb_build_object("
             "'state',v.state,'candidate_id',v.candidate_knowledge_id,"
             "'desired_etag',v.desired_etag,'visible_etag',k.metadata->>'nextcloud_etag',"
             "'path',k.metadata->>'nextcloud_path','file_name',k.file_name,"
             "'parse',k.parse_status,'enabled',k.enable_status,"
             "'ready_chunks',(SELECT COUNT(*) FROM chunks c WHERE c.knowledge_id=k.id "
             "AND c.deleted_at IS NULL AND c.is_enabled AND c.index_status='ready'),"
             "'embeddings',(SELECT COUNT(*) FROM embeddings e WHERE e.knowledge_id=k.id "
             "AND e.is_enabled),'visible_count',(SELECT COUNT(*) FROM knowledges q "
             "WHERE q.metadata->>'datasource_id'=v.datasource_id "
             "AND q.metadata->>'external_id'=v.external_id AND q.deleted_at IS NULL "
             "AND q.metadata->>'nextcloud_etag'<>'')) "
             "FROM nextcloud_source_versions v LEFT JOIN knowledges k "
             "ON k.id=v.candidate_knowledge_id "
             f"WHERE v.datasource_id='{source_id}' AND v.external_id LIKE '%:{file_number}'")
    return sql_json(db, query)


def published(db, source_id, file_number):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            value = version(db, source_id, file_number)
        except (RuntimeError, ValueError):
            value = None
        if (value and value["state"] == "published" and value["parse"] == "completed" and
                value["enabled"] == "enabled" and value["desired_etag"] and
                value["visible_etag"] == value["desired_etag"] and
                value["ready_chunks"] >= 1 and value["embeddings"] >= 1 and
                value["visible_count"] == 1):
            return value
        time.sleep(2)
    raise RuntimeError("event applied without a ready published index")


def tombstone(db, source_id, file_number):
    value = version(db, source_id, file_number)
    require(value["state"] == "tombstone" and not value["candidate_id"] and
            value["visible_count"] == 0,
            "event applied without a hidden source tombstone")
    return value


def source_content(nc_base, runtime, file_number, expected_status, expected_body=None):
    url = (nc_base + "/index.php/apps/integration_weknora/api/v1/bindings/" +
           urllib.parse.quote(runtime["binding_id"], safe="") +
           f"/files/{file_number}/content")
    code, body = dav_request(url, "GET", {
        "Authorization": "Bearer " + runtime["token"],
        "X-WeKnora-Key-Id": runtime["key_id"],
    })
    require(code in expected_status, f"source access returned HTTP {code}")
    if expected_body is not None:
        require(body == expected_body, "source content differs from the DAV document")
    return code


def trash_entry(nc_base, headers, filename, expected_location):
    root = nc_base + "/remote.php/dav/trashbin/devadmin/trash/"
    propfind = (b'<d:propfind xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns">'
                b'<d:prop><nc:trashbin-filename/><nc:trashbin-original-location/>'
                b'</d:prop></d:propfind>')
    xml = dav(root, "PROPFIND", {**headers, "Depth": "1",
                                     "Content-Type": "application/xml"},
              propfind, (207,))
    matches = []
    for item in ET.fromstring(xml).findall("{DAV:}response"):
        actual = item.findtext(".//{http://nextcloud.org/ns}trashbin-filename")
        location = item.findtext(".//{http://nextcloud.org/ns}trashbin-original-location")
        href = item.findtext("{DAV:}href")
        if (actual != filename or not location or
                location.lstrip("/") != expected_location or not href):
            continue
        url = urllib.parse.urljoin(nc_base, href)
        parsed = urllib.parse.urlsplit(url)
        require(url.startswith(root) and parsed.netloc == urllib.parse.urlsplit(nc_base).netloc,
                "trash listing returned a non-fixture location")
        matches.append((url, location))
    require(len(matches) == 1, "expected one exact disposable trash item")
    return matches[0]


def second_binding(state, runtime, passwords, nc_base, wk_base, nc_admin, csrf,
                   wk_token, admin_headers, suffix):
    """Pair a fresh sibling root to a second dedicated KB in the owned stack."""
    name = "Published-cross-" + suffix
    binding = "synthetic-cross-" + suffix
    folder = nc_base + "/remote.php/dav/files/devadmin/" + name
    dav(folder, "MKCOL", admin_headers, expected=(201,))
    root_id = file_id(folder, admin_headers)
    require(root_id != runtime["root_file_id"], "second binding overlaps the first root")
    share = urllib.parse.urlencode({"path": "/" + name, "shareType": 1,
                                   "shareWith": "Engineering", "permissions": 1}).encode()
    share_url = nc_base + "/ocs/v2.php/apps/files_sharing/api/v1/shares"
    for attempt in range(15):
        code, raw = synthetic["request"](nc_admin, share_url, "POST", {
            "requesttoken": csrf, "OCS-APIRequest": "true",
            "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            share)
        if code == 200 and json.loads(raw)["ocs"]["meta"]["statuscode"] == 200:
            break
        require(code == 404 and attempt < 14,
                f"second folder group share failed: HTTP {code}")
        time.sleep(2)
    alice_headers = {"Authorization": "Basic " + base64.b64encode(
        ("alice:" + passwords["alice"]).encode()).decode(), "Depth": "0"}
    alice_folder = nc_base + "/remote.php/dav/files/alice/" + name
    require(dav_request(alice_folder, "PROPFIND", alice_headers)[0] == 207,
            "Engineering did not receive the second folder share")

    api = nc_base + "/index.php/apps/integration_weknora/api/v1"
    nc_request = synthetic["nc_request"]
    wk_request = synthetic["wk_request"]
    code, body = nc_request(nc_admin, csrf, api + "/admin/bindings", "POST", {
        "id": binding, "name": binding, "owner_uid": "devadmin", "root_file_id": root_id})
    require(code == 201 and body.get("binding", {}).get("id") == binding,
            f"second non-overlapping binding was rejected: HTTP {code}")
    key_id = "cross-key-" + suffix
    code, body = nc_request(nc_admin, csrf,
                            api + "/admin/bindings/" + binding + "/keys", "POST",
                            {"key_id": key_id})
    machine_token = body.get("token") if isinstance(body, dict) else None
    require(code == 201 and isinstance(machine_token, str) and machine_token,
            "second binding machine key was not created")

    tenant = runtime["tenant_id"]
    code, groups = synthetic["http_json"](
        wk_base, "GET", f"/api/v1/tenants/{tenant}/directory-groups?available=true",
        token=wk_token)
    catalog = groups.get("data", {}).get("groups") if isinstance(groups, dict) else None
    engineering = [item.get("directory_group_id") for item in catalog
                   if isinstance(item, dict) and item.get("display_name") == "Engineering"] \
                  if isinstance(catalog, list) else []
    require(code == 200 and len(engineering) == 1 and engineering[0],
            "second source cannot resolve the synthetic Engineering group")
    code, created = synthetic["http_json"](wk_base, "POST", "/api/v1/knowledge-bases", {
        "name": name, "type": "document", "embedding_model_id": runtime["model_id"],
        "summary_model_id": runtime["chat_model_id"]}, wk_token)
    kb_id = created.get("data", {}).get("id") if isinstance(created, dict) else None
    require(code == 201 and isinstance(kb_id, str) and kb_id,
            "second dedicated knowledge base was not created")
    code, _ = synthetic["http_json"](
        wk_base, "PUT", "/api/v1/group-access/knowledge_base/" + kb_id, {
            "mode": "restricted", "grants": [{"directory_id": state["directory_id"],
                                               "directory_group_id": engineering[0],
                                               "permission": "read"}]}, wk_token)
    require(code == 200, "second knowledge base was not restricted to Engineering")

    operation = str(uuid.uuid4())
    pair_url = api + "/admin/bindings/" + binding + "/source-pairing"
    code, prepared = nc_request(nc_admin, csrf, pair_url, "POST", {
        "operation_id": operation, "tenant_id": str(tenant), "knowledge_base_id": kb_id})
    pair = prepared.get("pairing") if isinstance(prepared, dict) else None
    one_time = prepared.get("token") if isinstance(prepared, dict) else None
    require(code == 201 and isinstance(pair, dict) and
            pair.get("operation_id") == operation and
            pair.get("binding_id") == binding and
            isinstance(one_time, str) and one_time,
            "second source pairing preparation failed")
    code, _ = wk_request(wk_base, wk_token, "POST",
                         "/api/v1/datasource/nextcloud-source-pairings", {
        "knowledge_base_id": kb_id, "base_url": "http://nextcloud",
        "binding_id": binding, "operation_id": operation,
        "instance_id": pair["instance_id"],
        "publication_epoch": pair["publication_epoch"],
        "key_id": pair["key_id"], "token": one_time})
    del one_time
    require(code in {200, 201, 202},
            f"second WeKnora source pairing failed: HTTP {code}")
    for _ in range(8):
        nc_code, nc_pair = nc_request(nc_admin, csrf, pair_url, "GET")
        wk_code, wk_pair = wk_request(
            wk_base, wk_token, "GET",
            "/api/v1/datasource/nextcloud-source-pairings/" + operation)
        if (nc_code == wk_code == 200 and
                nc_pair.get("pairing", {}).get("state") == "active" and
                wk_pair.get("pairing", {}).get("state") == "active"):
            source_id = wk_pair["pairing"]["data_source_id"]
            break
        retry_code, _ = wk_request(
            wk_base, wk_token, "POST",
            "/api/v1/datasource/nextcloud-source-pairings/" + operation + "/retry")
        require(retry_code in {200, 202},
                "second source pairing retry was rejected")
        time.sleep(.5)
    else:
        raise RuntimeError("second source pairing did not become active")
    result = {"binding_id": binding, "source_id": source_id,
              "operation_id": operation, "knowledge_base_id": kb_id,
              "root_file_id": root_id, "key_id": key_id, "token": machine_token,
              "folder_name": name, "folder_url": folder}
    synthetic["event_connection_setup"](
        state, passwords, nc_base, wk_base, result, source_id, wk_token)
    return result


def nested_cross_binding(state, runtime, passwords, nc_base, wk_base, nc_admin,
                         csrf, wk_token, admin_headers, alice_basic, bob_basic,
                         delivery, applied, suffix):
    """Prove three descendants migrate between two paired sibling roots."""
    other = second_binding(state, runtime, passwords, nc_base, wk_base,
                           nc_admin, csrf, wk_token, admin_headers, suffix)
    first_binding, first_source = runtime["binding_id"], runtime["source_id"]
    nc_container = state["project"] + "-nextcloud-1"
    wk_db = state["project"] + "-wk-db-1"
    first_event_url = (nc_base + "/index.php/apps/integration_weknora/api/v1/admin/bindings/" +
                       urllib.parse.quote(first_binding, safe="") + "/event-connection")
    second_event_url = (nc_base + "/index.php/apps/integration_weknora/api/v1/admin/bindings/" +
                        urllib.parse.quote(other["binding_id"], safe="") + "/event-connection")

    def applied_for(source, event_url, target):
        sent, received = event["wait_applied"](
            state, nc_admin, csrf, event_url, wk_base, wk_token, source,
            nc_container, delivery, applied, target)
        return {"event_id": target,
                "sender_applied": event["decimal"](sent, "applied_through_event_id"),
                "receiver_applied": event["decimal"](received, "applied_through_event_id")}

    tree = "cross-tree-" + suffix
    first_root = nc_base + "/remote.php/dav/files/devadmin/Published/" + tree
    second_root = other["folder_url"] + "/" + tree
    alice_root_a = nc_base + "/remote.php/dav/files/alice/Published/" + tree
    bob_root_a = nc_base + "/remote.php/dav/files/bob/Published/" + tree
    alice_root_b = (nc_base + "/remote.php/dav/files/alice/" +
                    other["folder_name"] + "/" + tree)
    bob_root_b = (nc_base + "/remote.php/dav/files/bob/" +
                  other["folder_name"] + "/" + tree)
    alice_headers = {"Authorization": "Basic " + alice_basic}
    bob_headers = {"Authorization": "Basic " + bob_basic}
    alice_token = handoff["wait_ldap_login"](
        wk_base, "alice", passwords["alice"])
    bob_token = handoff["wait_ldap_login"](
        wk_base, "bob", passwords["bob"])
    documents = {
        "top.txt": ("Synthetic cross-binding top " + suffix + "\n").encode(),
        "level-one/middle.txt": ("Synthetic cross-binding middle " + suffix + "\n").encode(),
        "level-one/level-two/deep.txt":
            ("Synthetic cross-binding deep " + suffix + "\n").encode(),
    }
    evidence = {"first_binding": first_binding, "second_binding": other["binding_id"],
                "first_source": first_source, "second_source": other["source_id"],
                "first_root_id": runtime["root_file_id"],
                "second_root_id": other["root_file_id"], "steps": {}}

    def current(source, binding_runtime, root, alice_root, bob_root, prior_ids):
        candidates = {}
        for relative, content in documents.items():
            source_url = root + "/" + relative
            current_id = file_id(source_url, admin_headers)
            require(current_id == file_ids[relative],
                    "cross-binding MOVE changed a descendant file ID")
            indexed = published(wk_db, source, current_id)
            require(indexed["path"] == tree + "/" + relative and
                    indexed["file_name"] == relative.rsplit("/", 1)[-1] and
                    indexed["candidate_id"] not in prior_ids[relative],
                    "current descendant has stale candidate or citation path")
            source_content(nc_base, binding_runtime, current_id, {200}, content)
            require(dav_request(alice_root + "/" + relative, "GET", alice_headers) ==
                    (200, content), "Alice cannot read current descendant")
            require(dav_request(bob_root + "/" + relative, "GET", bob_headers)[0]
                    in {403, 404}, "Bob can read current descendant")
            alice_code, alice_body = handoff["knowledge"](
                wk_base, indexed["candidate_id"], alice_token)
            bob_code, _ = handoff["knowledge"](
                wk_base, indexed["candidate_id"], bob_token)
            require(alice_code == 200 and bob_code in {403, 404} and
                    alice_body.get("data", {}).get("id") == indexed["candidate_id"],
                    "current WeKnora candidate does not enforce Alice/Bob ACL")
            candidates[relative] = indexed["candidate_id"]
        deep_id = file_ids["level-one/level-two/deep.txt"]
        code, status = handoff["status_for"](
            nc_base, {"file_id": deep_id}, passwords, "alice")
        require(code == 200 and status.get("knowledge_state") == "ready",
                "deep descendant has no ready Files answer target")
        selector = handoff["source_link"](
            status, wk_base, {"binding_id": binding_runtime["binding_id"],
                              "file_id": deep_id})
        alice_code, target = handoff["ask_target"](wk_base, selector, alice_token)
        bob_code, _ = handoff["ask_target"](wk_base, selector, bob_token)
        require(alice_code == 200 and bob_code in {403, 404} and
                target.get("data", {}).get("knowledge_id") ==
                candidates["level-one/level-two/deep.txt"],
                "deep current answer target selected the wrong candidate or ACL")
        return {"candidates": candidates, "alice_knowledge_http": 200,
                "bob_knowledge_denied": True, "alice_ask_http": alice_code,
                "bob_ask_http": bob_code}

    def withdrawn(source, binding_runtime, prior_candidates):
        for relative in documents:
            current_id = file_ids[relative]
            tombstone(wk_db, source, current_id)
            source_content(nc_base, binding_runtime, current_id, {403, 404})
            alice_code, _ = handoff["knowledge"](
                wk_base, prior_candidates[relative], alice_token)
            require(alice_code in {403, 404},
                    "withdrawn descendant remained directly readable in WeKnora")

    first_cursor = binding_cursor(state, first_binding)
    second_cursor = binding_cursor(state, other["binding_id"])
    file_ids = {}
    try:
        dav(first_root, "MKCOL", admin_headers, expected=(201,))
        dav(first_root + "/level-one", "MKCOL", admin_headers, expected=(201,))
        dav(first_root + "/level-one/level-two", "MKCOL", admin_headers,
            expected=(201,))
        folder_id = file_id(first_root, admin_headers)
        for relative, content in documents.items():
            url = first_root + "/" + relative
            dav(url, "PUT", {**admin_headers, "Content-Type": "text/plain"},
                content, expected=(201, 204))
            file_ids[relative] = file_id(url, admin_headers)
        events = [event_id(state, first_binding, number, "upsert", first_cursor)
                  for number in file_ids.values()]
        evidence["steps"]["create"] = applied_for(
            first_source, first_event_url, max(events))
        first = current(first_source, runtime, first_root, alice_root_a,
                        bob_root_a, {name: set() for name in documents})
        evidence["steps"]["create"].update(first)
        evidence["file_ids"] = file_ids

        # Alice has read-only group share access. A rejected DELETE must not
        # enqueue a false tombstone or withdraw the still-current generation.
        before_a = binding_cursor(state, first_binding)
        before_b = binding_cursor(state, other["binding_id"])
        deep = "level-one/level-two/deep.txt"
        denied, _ = dav_request(alice_root_a + "/" + deep, "DELETE", alice_headers)
        require(denied in {403, 404, 405} and
                deletion_hints_after(state, first_binding, before_a) == 0 and
                deletion_hints_after(state, other["binding_id"], before_b) == 0,
                "rejected DAV DELETE enqueued a false deletion hint")
        event["deliver"](nc_container, delivery)
        event["deliver"](nc_container, applied)
        retained = version(wk_db, first_source, file_ids[deep])
        require(retained["state"] == "published" and
                retained["candidate_id"] == first["candidates"][deep] and
                dav_request(alice_root_a + "/" + deep, "GET", alice_headers) ==
                (200, documents[deep]),
                "rejected DAV DELETE withdrew the original candidate or file")
        evidence["steps"]["rejected_delete"] = {
            "http": denied, "false_deletion_hints": 0,
            "current_candidate_retained": True, "alice_read_retained": True}

        move(first_root, second_root, admin_headers)
        require(file_id(second_root, admin_headers) == folder_id,
                "cross-binding MOVE changed the folder identity")
        out_event = event_id(state, first_binding, folder_id,
                             "subtree_deleted", before_a)
        in_event = event_id(state, other["binding_id"], folder_id,
                            "subtree_scan", before_b)
        for relative, number in file_ids.items():
            source_content(nc_base, runtime, number, {403, 404})
            source_content(nc_base, other, number, {200}, documents[relative])
        out_applied = applied_for(first_source, first_event_url, out_event)
        in_applied = applied_for(other["source_id"], second_event_url, in_event)
        withdrawn(first_source, runtime, first["candidates"])
        second = current(other["source_id"], other, second_root, alice_root_b,
                         bob_root_b,
                         {name: {first["candidates"][name]} for name in documents})
        evidence["steps"]["first_to_second"] = {
            "source_withdrawal": out_applied, "destination_ingest": in_applied,
            **second, "source_tombstones": len(documents)}

        before_a = binding_cursor(state, first_binding)
        before_b = binding_cursor(state, other["binding_id"])
        move(second_root, first_root, admin_headers)
        require(file_id(first_root, admin_headers) == folder_id,
                "reverse cross-binding MOVE changed the folder identity")
        out_event = event_id(state, other["binding_id"], folder_id,
                             "subtree_deleted", before_b)
        in_event = event_id(state, first_binding, folder_id,
                            "subtree_scan", before_a)
        out_applied = applied_for(other["source_id"], second_event_url, out_event)
        in_applied = applied_for(first_source, first_event_url, in_event)
        withdrawn(other["source_id"], other, second["candidates"])
        final = current(first_source, runtime, first_root, alice_root_a,
                        bob_root_a,
                        {name: {first["candidates"][name], second["candidates"][name]}
                         for name in documents})
        evidence["steps"]["second_to_first"] = {
            "source_withdrawal": out_applied, "destination_ingest": in_applied,
            **final, "source_tombstones": len(documents)}
        return evidence
    finally:
        # Reuse mode does not invoke this drill; automatic mode destroys both
        # paired bindings and the entire fixture after this owned path cleanup.
        dav_request(first_root, "DELETE", admin_headers)
        dav_request(second_root, "DELETE", admin_headers)


def smoke(scratch, *, cross_bindings=False, cross_bindings_only=False):
    directory, state = owned_state(scratch)
    require((directory / "fixture.json").is_file() and (directory / "runtime.json").is_file(),
            "owned fixture must be bootstrapped")
    require(run("docker", "image", "inspect", state["weknora_image"],
                "--format", "{{.Id}}").strip() == state["weknora_image_id"],
            "fixture WeKnora image changed")
    runtime = json.loads((directory / "runtime.json").read_text())
    passwords = json.loads((directory / "passwords.json").read_text())
    nc_base = f"http://127.0.0.1:{state['ports']['nextcloud']}"
    wk_base = f"http://127.0.0.1:{state['ports']['weknora']}"
    binding, source_id = runtime["binding_id"], runtime["source_id"]
    nc_admin, csrf = login(nc_base, "devadmin", passwords["nc_admin"])
    auth = json.dumps({"email": "synthetic-admin@example.test",
                       "password": passwords["wk_admin"]}).encode()
    outgoing = urllib.request.Request(wk_base + "/api/v1/auth/login", data=auth,
                                      method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(outgoing, timeout=25) as response:
        body = json.load(response)
    require(body.get("success") is True and isinstance(body.get("token"), str),
            "isolated WeKnora login failed")
    wk_token = body["token"]
    event_url = (nc_base + "/index.php/apps/integration_weknora/api/v1/admin/bindings/" +
                 urllib.parse.quote(binding, safe="") + "/event-connection")
    nc_container = state["project"] + "-nextcloud-1"
    wk_db = state["project"] + "-wk-db-1"
    delivery = event["background_job"](nc_container,
        r"OCA\IntegrationWeknora\BackgroundJob\EventDeliveryJob")
    applied = event["background_job"](nc_container,
        r"OCA\IntegrationWeknora\BackgroundJob\EventAppliedStatusJob")
    sender = event["nc_status"](nc_admin, csrf, event_url)
    receiver = event["wk_status"](wk_base, wk_token, source_id)
    require(sender["status"] == receiver["status"] == "active" and
            sender["connection_id"] == receiver["connection_id"],
            "isolated signed event connection is inactive")

    if cross_bindings_only:
        suffix = uuid.uuid4().hex
        basic = base64.b64encode(("devadmin:" + passwords["nc_admin"]).encode()).decode()
        alice_basic = base64.b64encode(("alice:" + passwords["alice"]).encode()).decode()
        bob_basic = base64.b64encode(("bob:" + passwords["bob"]).encode()).decode()
        proof = nested_cross_binding(
            state, runtime, passwords, nc_base, wk_base, nc_admin, csrf,
            wk_token, {"Authorization": "Basic " + basic}, alice_basic,
            bob_basic, delivery, applied, suffix)
        return {"project": state["project"], "image_id": state["weknora_image_id"],
                "steps": {"cross_binding": proof}}

    def applied_event(target):
        sent, received = event["wait_applied"](
            state, nc_admin, csrf, event_url, wk_base, wk_token, source_id,
            nc_container, delivery, applied, target)
        return {"event_id": target,
                "sender_applied": event["decimal"](sent, "applied_through_event_id"),
                "receiver_applied": event["decimal"](received, "applied_through_event_id")}

    suffix = uuid.uuid4().hex
    dav_root = nc_base + "/remote.php/dav/files/devadmin/"
    inside = dav_root + "Published/move-trash-" + suffix
    outside = dav_root + "move-trash-out-" + suffix
    original = inside + "/original.txt"
    renamed = inside + "/renamed.txt"
    outside_file = outside + "/renamed.txt"
    basic = base64.b64encode(("devadmin:" + passwords["nc_admin"]).encode()).decode()
    admin_headers = {"Authorization": "Basic " + basic}
    alice_basic = base64.b64encode(("alice:" + passwords["alice"]).encode()).decode()
    bob_basic = base64.b64encode(("bob:" + passwords["bob"]).encode()).decode()
    alice_url = nc_base + "/remote.php/dav/files/alice/Published/move-trash-" + suffix + "/renamed.txt"
    bob_url = nc_base + "/remote.php/dav/files/bob/Published/move-trash-" + suffix + "/renamed.txt"
    document = ("Synthetic move and trash race acceptance " + suffix + "\n").encode()
    evidence = {"project": state["project"], "image_id": state["weknora_image_id"],
                "steps": {}}
    cursor = 0
    try:
        dav(inside, "MKCOL", admin_headers, expected=(201,))
        dav(original, "PUT", {**admin_headers, "Content-Type": "text/plain"},
            document, (201, 204))
        folder_id, original_id = file_id(inside, admin_headers), file_id(original, admin_headers)
        cursor = event_id(state, binding, original_id, "upsert")
        evidence["steps"]["create"] = applied_event(cursor)
        first = published(wk_db, source_id, original_id)
        require(first["path"] == "move-trash-" + suffix + "/original.txt" and
                first["file_name"] == "original.txt",
                "initial indexed path does not match the source")
        source_content(nc_base, runtime, original_id, {200}, document)
        require(dav_request(alice_url.replace("renamed.txt", "original.txt"), "GET",
                            {"Authorization": "Basic " + alice_basic}) == (200, document),
                "Engineering member cannot read published source")
        evidence["steps"]["create"].update({"file_id": original_id,
                                               "candidate_id": first["candidate_id"]})

        move(original, renamed, admin_headers)
        require(file_id(renamed, admin_headers) == original_id,
                "rename changed the Nextcloud file identity")
        cursor = event_id(state, binding, original_id, "metadata", cursor)
        evidence["steps"]["rename"] = applied_event(cursor)
        renamed_index = published(wk_db, source_id, original_id)
        require(renamed_index["candidate_id"] == first["candidate_id"],
                "metadata rename unexpectedly replaced the published generation")
        require(renamed_index["path"] == "move-trash-" + suffix + "/renamed.txt" and
                renamed_index["file_name"] == "renamed.txt",
                "metadata rename did not update the published citation path")
        require(dav_request(alice_url, "GET", {"Authorization": "Basic " + alice_basic}) ==
                (200, document), "renamed source is not readable by Engineering")
        require(dav_request(bob_url, "GET", {"Authorization": "Basic " + bob_basic})[0]
                in {403, 404}, "non-member can read renamed source")
        source_content(nc_base, runtime, original_id, {200}, document)

        move(inside, outside, admin_headers)
        cursor = event_id(state, binding, folder_id, "subtree_deleted", cursor)
        source_content(nc_base, runtime, original_id, {403, 404})
        evidence["steps"]["move_out"] = applied_event(cursor)
        tombstone(wk_db, source_id, original_id)
        require(dav_request(alice_url, "GET", {"Authorization": "Basic " + alice_basic})[0]
                in {403, 404}, "moved-out source remains in the employee share")
        require(file_id(outside_file, admin_headers) == original_id,
                "move-out changed the Nextcloud file identity")

        move(outside, inside, admin_headers)
        cursor = event_id(state, binding, folder_id, "subtree_scan", cursor)
        evidence["steps"]["move_back"] = applied_event(cursor)
        second = published(wk_db, source_id, original_id)
        require(second["candidate_id"] != first["candidate_id"],
                "move-back reused a withdrawn candidate")
        source_content(nc_base, runtime, original_id, {200}, document)
        evidence["steps"]["move_back"]["candidate_id"] = second["candidate_id"]

        # Re-enter before event delivery: the final source state must win over
        # both hints, and the later applied watermark must cover the pair.
        move(inside, outside, admin_headers)
        flap_out = event_id(state, binding, folder_id, "subtree_deleted", cursor)
        move(outside, inside, admin_headers)
        cursor = event_id(state, binding, folder_id, "subtree_scan", flap_out)
        evidence["steps"]["rapid_move_pair"] = applied_event(cursor)
        published(wk_db, source_id, original_id)
        source_content(nc_base, runtime, original_id, {200}, document)
        require(cursor > flap_out, "rapid move pair has no ordered events")

        dav(renamed, "DELETE", admin_headers, expected=(204,))
        cursor = event_id(state, binding, original_id, "delete", cursor)
        source_content(nc_base, runtime, original_id, {403, 404})
        evidence["steps"]["trash"] = applied_event(cursor)
        tombstone(wk_db, source_id, original_id)
        expected_location = "Published/move-trash-" + suffix + "/renamed.txt"
        trashed_url, original_location = trash_entry(
            nc_base, admin_headers, "renamed.txt", expected_location)
        require(original_location.lstrip("/") == expected_location,
                "trash item original path differs from the probe document")
        restore_url = (nc_base + "/remote.php/dav/trashbin/devadmin/restore/" +
                       urllib.parse.quote(urllib.parse.unquote(trashed_url.rsplit("/", 1)[1]), safe=""))
        move(trashed_url, restore_url, admin_headers)
        restored_id = file_id(renamed, admin_headers)
        cursor = event_id(state, binding, restored_id, "upsert", cursor)
        evidence["steps"]["restore"] = applied_event(cursor)
        third = published(wk_db, source_id, restored_id)
        require(third["candidate_id"] != second["candidate_id"],
                "trash restore reused the pre-trash publication generation")
        if restored_id != original_id:
            tombstone(wk_db, source_id, original_id)
        source_content(nc_base, runtime, restored_id, {200}, document)
        require(dav_request(alice_url, "GET", {"Authorization": "Basic " + alice_basic}) ==
                (200, document), "restored source is not readable by Engineering")
        require(dav_request(bob_url, "GET", {"Authorization": "Basic " + bob_basic})[0]
                in {403, 404}, "non-member can read restored source")
        evidence["steps"]["restore"].update({"file_id": restored_id,
                                                "candidate_id": third["candidate_id"]})
        if cross_bindings:
            evidence["steps"]["cross_binding"] = nested_cross_binding(
                state, runtime, passwords, nc_base, wk_base, nc_admin, csrf,
                wk_token, admin_headers, alice_basic, bob_basic, delivery, applied,
                suffix)
        return evidence
    finally:
        # Only the UUID-scoped probe paths are touched. The fixture owner
        # removes the entire project when this script created it.
        for url in (inside, outside):
            dav_request(url, "DELETE", admin_headers)


def isolated_run(image, *, cross_bindings_only=False):
    prepared = run(sys.executable, str(HERE / "synthetic-ldap-fixture.py"), "prepare",
                   "--weknora-image", image, "--mode", "direct", timeout=90)
    scratch = Path(json.loads(prepared)["scratch"])
    owned_state(scratch)
    try:
        run(sys.executable, str(HERE / "synthetic-ldap-fixture.py"), "up",
            "--scratch", str(scratch), timeout=900)
        run(sys.executable, str(HERE / "synthetic-ldap-e2e.py"), "bootstrap",
            "--scratch", str(scratch), timeout=900)
        return smoke(scratch, cross_bindings=not cross_bindings_only,
                     cross_bindings_only=cross_bindings_only)
    finally:
        cleanup = subprocess.run([sys.executable, str(HERE / "synthetic-ldap-fixture.py"),
                                  "destroy", "--scratch", str(scratch)],
                                 text=True, capture_output=True, timeout=180)
        require(cleanup.returncode == 0,
                f"owned fixture cleanup failed; inspect scratch {scratch}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    location = parser.add_mutually_exclusive_group(required=True)
    location.add_argument("--weknora-image", help="local candidate image for an automatic isolated run")
    location.add_argument("--scratch", type=Path,
                          help="already bootstrapped owned synthetic fixture")
    parser.add_argument("--cross-bindings-only", action="store_true",
                        help="run the three-descendant two-binding drill in a fresh fixture")
    args = parser.parse_args()
    if args.cross_bindings_only and args.scratch:
        parser.error("--cross-bindings-only requires a fresh --weknora-image fixture")
    result = (isolated_run(args.weknora_image,
                           cross_bindings_only=args.cross_bindings_only)
              if args.weknora_image else smoke(args.scratch))
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.CalledProcessError) as error:
        print("Isolated move/trash/restore smoke failed: " + str(error), file=sys.stderr)
        sys.exit(1)
