#!/usr/bin/env python3
"""Exercise move, rename, trash, and restore against an owned paired fixture.

The default invocation prepares, bootstraps, probes, and destroys a unique
loopback-only synthetic LDAP Compose project. ``--scratch`` reuses an already
bootstrapped owned fixture and removes only this probe's uniquely named files.
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


def smoke(scratch):
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
        return evidence
    finally:
        # Only the UUID-scoped probe paths are touched. The fixture owner
        # removes the entire project when this script created it.
        for url in (inside, outside):
            dav_request(url, "DELETE", admin_headers)


def isolated_run(image):
    prepared = run(sys.executable, str(HERE / "synthetic-ldap-fixture.py"), "prepare",
                   "--weknora-image", image, "--mode", "direct", timeout=90)
    scratch = Path(json.loads(prepared)["scratch"])
    owned_state(scratch)
    try:
        run(sys.executable, str(HERE / "synthetic-ldap-fixture.py"), "up",
            "--scratch", str(scratch), timeout=900)
        run(sys.executable, str(HERE / "synthetic-ldap-e2e.py"), "bootstrap",
            "--scratch", str(scratch), timeout=900)
        return smoke(scratch)
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
    args = parser.parse_args()
    result = isolated_run(args.weknora_image) if args.weknora_image else smoke(args.scratch)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.CalledProcessError) as error:
        print("Isolated move/trash/restore smoke failed: " + str(error), file=sys.stderr)
        sys.exit(1)
