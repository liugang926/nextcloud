#!/usr/bin/env python3
"""Verify finalized source-key rotation and event rebind in an owned fixture.

Prepare/up/bootstrap an isolated synthetic LDAP fixture first. This script
accepts only that fixture's private scratch marker and loopback ports. It
creates two disposable DAV documents and never prints credentials. Destroy
the fixture with synthetic-ldap-fixture.py destroy after collecting evidence.
"""

import argparse
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/integration_weknora/tests"))
from changes_http_smoke import file_id, request as dav_request  # noqa: E402
from publication_http_smoke import login  # noqa: E402

owner = runpy.run_path(str(Path(__file__).with_name("synthetic-ldap-fixture.py")))
pairing = runpy.run_path(str(Path(__file__).with_name("local-source-pairing.py")))
owned_state = owner["owned_state"]
nc_request = pairing["nc_request"]
wk_request = pairing["wk_request"]
rotation_cli = runpy.run_path(str(Path(__file__).with_name("local-source-rotation.py")))
rotation_main = rotation_cli["main"]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def decimal(body, field):
    value = body.get(field)
    require(isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]*", value),
            f"invalid {field}")
    return int(value)


def command(*args, timeout=60):
    return subprocess.run(args, check=True, text=True, capture_output=True,
                          timeout=timeout).stdout


def background_job(container, job_class):
    rows = json.loads(command("docker", "exec", "-u", "www-data", container,
                             "php", "occ", "background-job:list", "--output=json"))
    matches = [str(item["id"]) for item in rows if
               item.get("class") == job_class]
    require(len(matches) == 1, "expected one isolated background job")
    return matches[0]


def deliver(container, job_id):
    command("docker", "exec", "-u", "www-data", container, "php", "occ",
            "background-job:execute", "--force-execute", job_id, timeout=120)


def outbox_event(state, binding, file_number):
    require(re.fullmatch(r"[A-Za-z0-9._-]{1,128}", binding) is not None,
            "invalid fixture binding")
    require(isinstance(file_number, int) and file_number > 0, "invalid fixture file")
    sql = ("SELECT COALESCE(MAX(id),0) FROM oc_weknora_outbox "
           f"WHERE binding_id='{binding}' AND file_id={file_number} AND event_type='upsert'")
    row = command("docker", "exec", state["project"] + "-nc-db-1", "psql",
                  "-U", "nextcloud", "-d", "nextcloud", "-Atc", sql).strip()
    require(row.isdecimal() and int(row) > 0, "DAV write has no outbox event")
    return int(row)


def wk_status(wk_base, token, source_id):
    code, body = wk_request(wk_base, token, "GET",
                            f"/api/v1/datasource/{source_id}/nextcloud-event-connection")
    require(code == 200 and isinstance(body, dict),
            f"read isolated receiver status failed: HTTP {code}")
    return body


def nc_status(admin, csrf, url):
    code, body = nc_request(admin, csrf, url, "GET")
    require(code == 200 and isinstance(body, dict),
            f"read isolated sender status failed: HTTP {code}")
    return body


def put_document(nc_base, password, name):
    url = nc_base + "/remote.php/dav/files/devadmin/Published/" + urllib.parse.quote(name)
    basic = base64.b64encode(("devadmin:" + password).encode()).decode()
    headers = {"Authorization": "Basic " + basic, "Content-Type": "text/plain"}
    body = ("Synthetic event rebind proof " + name + "\n").encode()
    code, _ = dav_request(url, "PUT", headers, body)
    require(code in (201, 204), f"DAV write failed: HTTP {code}")
    return file_id(url, headers)


def wait_applied(state, nc_admin, csrf, nc_event_url, wk_base, wk_token,
                 source_id, container, delivery_job_id, applied_job_id, target):
    deadline = time.monotonic() + 600
    last = (0, 0, 0, 0)
    while time.monotonic() < deadline:
        deliver(container, delivery_job_id)
        deliver(container, applied_job_id)
        sender = nc_status(nc_admin, csrf, nc_event_url)
        receiver = wk_status(wk_base, wk_token, source_id)
        last = (decimal(sender, "received_through_event_id"),
                decimal(sender, "applied_through_event_id"),
                decimal(receiver, "received_through_event_id"),
                decimal(receiver, "applied_through_event_id"))
        require(last[0] <= last[2], "receiver receipt fell behind sender")
        require(last[1] <= last[0] and last[3] <= last[2], "applied watermark exceeds receipt")
        if last[0] >= target and last[1] >= target and last[2] >= target and last[3] >= target:
            return sender, receiver
        require(sender.get("status") == "active" and receiver.get("dispatch_state") != "blocked",
                "sender or receiver blocked before applied proof")
        time.sleep(5)
    raise RuntimeError(f"applied event {target} timed out at watermarks {last}")


def rotate_source(nc_admin, csrf, nc_base, wk_base, wk_token, binding, pair_id):
    rotation_id = str(uuid.uuid4())
    nc_path = (nc_base + "/index.php/apps/integration_weknora/api/v1/admin/bindings/" +
               urllib.parse.quote(binding, safe="") + "/source-pairing/rotation")
    code, prepared = nc_request(nc_admin, csrf, nc_path, "POST",
                                {"operation_id": rotation_id})
    require(code == 201 and isinstance(prepared, dict), "prepare isolated source rotation failed")
    nc_rotation = prepared.get("rotation", {})
    token = prepared.get("token")
    require(nc_rotation.get("operation_id") == rotation_id and
            nc_rotation.get("pair_operation_id") == pair_id and
            isinstance(token, str) and bool(token), "rotation prepare identity mismatch")
    wk_path = ("/api/v1/datasource/nextcloud-source-pairings/" + pair_id +
               "/rotations")
    code, _ = wk_request(wk_base, wk_token, "POST", wk_path, {
        "operation_id": rotation_id, "new_key_id": nc_rotation["new_key_id"], "token": token})
    del token
    require(code in (200, 201, 202), f"WeKnora source rotation failed: HTTP {code}")
    for _ in range(15):
        n_code, n_body = nc_request(nc_admin, csrf, nc_path, "GET")
        w_code, w_body = wk_request(wk_base, wk_token, "GET", wk_path + "/" + rotation_id)
        require(n_code == w_code == 200, "read source rotation state failed")
        n_state = n_body.get("rotation", {}).get("state")
        w_state = w_body.get("rotation", {}).get("state")
        if n_state == w_state == "finalized":
            return rotation_id
        code, _ = wk_request(wk_base, wk_token, "POST", wk_path + "/" + rotation_id + "/retry")
        require(code in (200, 202), f"source rotation retry failed: HTTP {code}")
        time.sleep(1)
    raise RuntimeError("source rotation did not finalize on both services")


def operator_rebind(state, passwords, wk_base, binding, pair_id, rotation_id):
    output = io.StringIO()
    credentials = {"WEKNORA_TEST_ADMIN_EMAIL": "synthetic-admin@example.test",
                   "WEKNORA_TEST_ADMIN_PASSWORD": passwords["wk_admin"]}
    local_values = {"NEXTCLOUD_HTTP_PORT": str(state["ports"]["nextcloud"]),
                    "NEXTCLOUD_ADMIN_USER": "devadmin",
                    "NEXTCLOUD_ADMIN_PASSWORD": passwords["nc_admin"]}
    with mock.patch.dict(os.environ, credentials), contextlib.redirect_stdout(output):
        rotation_main(["rebind", "--binding", binding,
                       "--pair-operation-id", pair_id,
                       "--operation-id", rotation_id,
                       "--weknora-base-url", wk_base], values_override=local_values)
    result = json.loads(output.getvalue())
    require(result.get("rotation_operation_id") == rotation_id,
            "operator rebind did not confirm the rotation")
    return result


def smoke(scratch):
    directory, state = owned_state(scratch)
    runtime = json.loads((directory / "runtime.json").read_text())
    require((directory / "fixture.json").is_file(), "fixture must be bootstrapped")
    expected_image = command("docker", "image", "inspect", state["weknora_image"],
                             "--format", "{{.Id}}").strip()
    require(expected_image == state["weknora_image_id"], "owned WeKnora image changed")
    passwords = json.loads((directory / "passwords.json").read_text())
    nc_base = f"http://127.0.0.1:{state['ports']['nextcloud']}"
    wk_base = f"http://127.0.0.1:{state['ports']['weknora']}"
    binding, source_id, pair_id = (runtime[name] for name in
                                  ("binding_id", "source_id", "operation_id"))
    nc_admin, csrf = login(nc_base, "devadmin", passwords["nc_admin"])
    login_body = json.dumps({"email": "synthetic-admin@example.test",
                             "password": passwords["wk_admin"]}).encode()
    login_request = urllib.request.Request(wk_base + "/api/v1/auth/login", data=login_body,
                                           method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(login_request, timeout=25) as response:
        auth = json.load(response)
    require(auth.get("success") is True and isinstance(auth.get("token"), str),
            "isolated WeKnora login failed")
    wk_token = auth["token"]
    nc_event_url = (nc_base + "/index.php/apps/integration_weknora/api/v1/admin/bindings/" +
                    urllib.parse.quote(binding, safe="") + "/event-connection")
    nc_container = state["project"] + "-nextcloud-1"
    job_id = background_job(nc_container,
                            r"OCA\IntegrationWeknora\BackgroundJob\EventDeliveryJob")
    applied_job_id = background_job(nc_container,
                                    r"OCA\IntegrationWeknora\BackgroundJob\EventAppliedStatusJob")
    before_sender = nc_status(nc_admin, csrf, nc_event_url)
    before_receiver = wk_status(wk_base, wk_token, source_id)
    require(before_sender["status"] == before_receiver["status"] == "active" and
            before_sender["connection_id"] == before_receiver["connection_id"] and
            before_sender["key_id"] == before_receiver["key_id"], "initial event pair mismatch")
    connection_id, key_id = before_sender["connection_id"], before_sender["key_id"]
    e1_file = put_document(nc_base, passwords["nc_admin"], "rebind-e1-" + uuid.uuid4().hex + ".txt")
    e1 = outbox_event(state, binding, e1_file)
    sender1, receiver1 = wait_applied(state, nc_admin, csrf, nc_event_url, wk_base,
                                      wk_token, source_id, nc_container, job_id, applied_job_id, e1)
    require(receiver1["dispatch_state"] == "idle" and
            decimal(receiver1, "dispatched_through_event_id") ==
            decimal(receiver1, "applied_through_event_id"), "e1 dispatch not quiescent")
    rotation_id = rotate_source(nc_admin, csrf, nc_base, wk_base, wk_token, binding, pair_id)
    stale = wk_status(wk_base, wk_token, source_id)
    require(stale["status"] == "source_unpaired" and
            decimal(stale, "received_through_event_id") == decimal(receiver1, "received_through_event_id"),
            "old event connection remained source-paired after rotation")
    e2_file = put_document(nc_base, passwords["nc_admin"], "rebind-e2-" + uuid.uuid4().hex + ".txt")
    e2 = outbox_event(state, binding, e2_file)
    deliver(nc_container, job_id)
    paused = nc_status(nc_admin, csrf, nc_event_url)
    rejected = wk_status(wk_base, wk_token, source_id)
    require(paused["status"] == "paused" and
            paused["last_error_code"] == "receiver_unauthorized" and
            decimal(paused, "received_through_event_id") == decimal(sender1, "received_through_event_id") and
            decimal(rejected, "received_through_event_id") == decimal(receiver1, "received_through_event_id"),
            "old event sender did not stop at source rotation")
    operator_result = operator_rebind(state, passwords, wk_base, binding, pair_id, rotation_id)
    rebound = operator_result["event_connection"]
    resumed = operator_result["event_sender"]
    require(rebound.get("rebound") is True and resumed.get("action") == "resumed" and
            resumed.get("status") == "active" and
            rebound.get("connection_id") == connection_id and rebound.get("key_id") == key_id and
            decimal(rebound, "received_through_event_id") == decimal(receiver1, "received_through_event_id") and
            decimal(rebound, "dispatched_through_event_id") == decimal(receiver1, "dispatched_through_event_id") and
            decimal(rebound, "applied_through_event_id") == decimal(receiver1, "applied_through_event_id"),
            "operator CLI did not rebind and resume exact event connection")
    replay = operator_rebind(state, passwords, wk_base, binding, pair_id, rotation_id)
    require(replay["event_connection"].get("rebound") is False and
            replay["event_sender"].get("action") == "already_active",
            "operator CLI rebind replay was not idempotent")
    sender2, receiver2 = wait_applied(state, nc_admin, csrf, nc_event_url, wk_base,
                                      wk_token, source_id, nc_container, job_id, applied_job_id, e2)
    require(sender2["connection_id"] == receiver2["connection_id"] == connection_id and
            sender2["key_id"] == receiver2["key_id"] == key_id,
            "connection or HMAC key changed after rebind")
    result = {"project": state["project"], "image_id": expected_image,
              "connection_id": connection_id, "rotation_id": rotation_id,
              "e1": e1, "e2": e2,
              "e1_applied": decimal(receiver1, "applied_through_event_id"),
              "e2_receiver_applied": decimal(receiver2, "applied_through_event_id"),
              "e2_sender_applied": decimal(sender2, "applied_through_event_id"),
              "preserved_key_id": key_id,
              "old_sender_paused": paused["status"] == "paused",
              "operator_sender_action": resumed["action"]}
    evidence = directory / "event-rebind-result.json"
    evidence.write_text(json.dumps(result, indent=2) + "\n")
    evidence.chmod(0o600)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=True, type=Path)
    args = parser.parse_args()
    try:
        smoke(args.scratch)
    except (KeyError, OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print("Isolated event rebind smoke failed: " + str(error), file=sys.stderr)
        sys.exit(1)
