#!/usr/bin/env python3
"""Rotate an established local Nextcloud ↔ WeKnora source-pair credential.

The one-time Nextcloud token is passed directly to WeKnora and never printed.
Keep both operation IDs for status, retry, or safe pending abort.
"""

import argparse
import json
from pathlib import Path
import re
import runpy
import sys
import urllib.error
import urllib.parse
import uuid
from typing import NamedTuple

_client = runpy.run_path(str(Path(__file__).with_name("local-source-pairing.py")))
load_env = _client["load_env"]
login = _client["login"]
nc_request = _client["nc_request"]
wk_login = _client["wk_login"]
wk_request = _client["wk_request"]


def rotation_body(body):
    value = body.get("rotation")
    return value if isinstance(value, dict) else None


def event_connection_url(nc_api, binding_id):
    return (nc_api + "/admin/bindings/" + urllib.parse.quote(binding_id, safe="") +
            "/event-connection")


def require_rotation_scope(pair, nc_rotation, wk_rotation, binding, pair_id,
                           operation_id, tenant_id):
    if (not isinstance(pair, dict) or not isinstance(nc_rotation, dict) or
            not isinstance(wk_rotation, dict)):
        raise RuntimeError("source rotation scope is unavailable")
    expected = {
        "binding_id": binding,
        "operation_id": pair_id,
        "tenant_id": tenant_id,
    }
    if any(pair.get(name) != value for name, value in expected.items()):
        raise RuntimeError("active source pairing scope changed")
    for name in ("binding_id", "instance_id", "tenant_id", "knowledge_base_id",
                 "data_source_id", "publication_epoch"):
        if nc_rotation.get(name) != pair.get(name):
            raise RuntimeError("Nextcloud rotation differs from active source pairing")
    if (nc_rotation.get("operation_id") != operation_id or
            nc_rotation.get("pair_operation_id") != pair_id or
            pair.get("key_id") != nc_rotation.get("new_key_id")):
        raise RuntimeError("Nextcloud rotation operation or key differs from active pairing")
    for name in ("binding_id", "instance_id", "knowledge_base_id", "data_source_id",
                 "old_key_id", "new_key_id"):
        if wk_rotation.get(name) != nc_rotation.get(name):
            raise RuntimeError("WeKnora rotation differs from Nextcloud")
    if (wk_rotation.get("operation_id") != operation_id or
            wk_rotation.get("pair_operation_id") != pair_id):
        raise RuntimeError("WeKnora rotation operation differs from Nextcloud")


def event_id(body, field):
    value = body.get(field) if isinstance(body, dict) else None
    if (not isinstance(value, str) or
            re.fullmatch(r"(?:0|[1-9][0-9]{0,18})", value) is None or
            int(value) > 9223372036854775807):
        raise RuntimeError(f"invalid event {field}")
    return int(value)


class EventScope(NamedTuple):
    connection_id: str
    key_id: str
    sender_received: int
    sender_applied: int
    receiver_received: int
    receiver_dispatched: int
    receiver_applied: int


def receiver_progress(body):
    received = event_id(body, "received_through_event_id")
    dispatched = event_id(body, "dispatched_through_event_id")
    applied = event_id(body, "applied_through_event_id")
    if applied > dispatched or dispatched > received:
        raise RuntimeError("event receiver watermarks are out of order")
    return received, dispatched, applied


def has_safe_dispatch_status(body):
    state = body.get("dispatch_state") if isinstance(body, dict) else None
    return state in ("idle", "queued", "leased", "retry")


def checked_event_scope(sender, receiver, binding, instance_id):
    if not isinstance(sender, dict) or not isinstance(receiver, dict):
        raise RuntimeError("event connection status is unavailable")
    connection_id = sender.get("connection_id")
    key_id = sender.get("key_id")
    if (sender.get("binding_id") != binding or receiver.get("binding_id") != binding or
            receiver.get("nextcloud_instance_id") != instance_id or
            not isinstance(connection_id, str) or
            re.fullmatch(r"[A-Za-z0-9_-]{16,128}", connection_id) is None or
            not isinstance(key_id, str) or
            re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key_id) is None or
            receiver.get("connection_id") != connection_id or
            receiver.get("key_id") != key_id):
        raise RuntimeError("event sender and receiver connection scope differs")
    sender_url = sender.get("receiver_url")
    receiver_path = receiver.get("receiver_url")
    parsed = urllib.parse.urlsplit(sender_url) if isinstance(sender_url, str) else None
    if (parsed is None or parsed.scheme not in ("http", "https") or not parsed.netloc or
            not isinstance(receiver_path, str) or parsed.path != receiver_path or
            parsed.query or parsed.fragment):
        raise RuntimeError("event sender targets a different receiver path")
    sender_received = event_id(sender, "received_through_event_id")
    sender_applied = event_id(sender, "applied_through_event_id")
    receiver_received, receiver_dispatched, receiver_applied = receiver_progress(receiver)
    if (sender_applied > sender_received or sender_received > receiver_received or
            sender_applied > receiver_applied):
        raise RuntimeError("event sender and receiver watermarks diverged")
    return EventScope(connection_id, key_id, sender_received, sender_applied,
                      receiver_received, receiver_dispatched, receiver_applied)


def rebind_and_resume_event_sender(nc_session, csrf, nc_event_url, wk_base, wk_token,
                                   source_id, binding, instance_id, operation_id):
    wk_event_path = ("/api/v1/datasource/" + urllib.parse.quote(source_id, safe="") +
                     "/nextcloud-event-connection")
    nc_status, sender_before = nc_request(nc_session, csrf, nc_event_url, "GET")
    wk_status, receiver_before = wk_request(wk_base, wk_token, "GET", wk_event_path)
    if nc_status != 200 or wk_status != 200:
        raise RuntimeError(f"event connection status unavailable: Nextcloud HTTP {nc_status}, "
                           f"WeKnora HTTP {wk_status}")
    before = checked_event_scope(sender_before, receiver_before, binding, instance_id)
    if before.sender_received != before.receiver_received:
        raise RuntimeError("event sender and receiver receipt watermarks differ")
    if sender_before.get("status") not in ("active", "paused") or receiver_before.get(
            "status") not in ("active", "source_changed", "source_unpaired"):
        raise RuntimeError("event sender or receiver is not eligible for source rotation rebind")
    wk_status, rebound = wk_request(wk_base, wk_token, "POST", wk_event_path + "/rebind",
                                    {"operation_id": operation_id})
    if wk_status != 200 or not isinstance(rebound, dict):
        raise RuntimeError(f"WeKnora event connection rebind: HTTP {wk_status}; "
                           "inspect dispatch status before retry")
    rebound_progress = receiver_progress(rebound)
    if (rebound.get("rotation_id") != operation_id or rebound.get("status") != "active" or
            rebound.get("connection_id") != before.connection_id or
            rebound.get("key_id") != before.key_id or
            not isinstance(rebound.get("rebound"), bool) or
            any(current < previous for current, previous in zip(
                rebound_progress, (before.receiver_received, before.receiver_dispatched,
                                   before.receiver_applied)))):
        raise RuntimeError("WeKnora rebind response changed connection identity or regressed watermarks")
    nc_status, sender_after = nc_request(nc_session, csrf, nc_event_url, "GET")
    wk_status, receiver_after = wk_request(wk_base, wk_token, "GET", wk_event_path)
    if wk_status != 200 or nc_status != 200:
        raise RuntimeError("event status unavailable after WeKnora rebind")
    after = checked_event_scope(sender_after, receiver_after, binding, instance_id)
    if (after.connection_id != before.connection_id or after.key_id != before.key_id or
            receiver_after.get("status") != "active" or
            sender_after.get("status") not in ("active", "paused")):
        raise RuntimeError("event connection changed after WeKnora rebind")
    if (after.sender_received < before.sender_received or
            after.sender_applied < before.sender_applied or
            any(current < previous for current, previous in zip(
                (after.receiver_received, after.receiver_dispatched, after.receiver_applied),
                rebound_progress))):
        raise RuntimeError("event connection watermarks regressed after WeKnora rebind")
    if not has_safe_dispatch_status(rebound) or not has_safe_dispatch_status(receiver_after):
        raise RuntimeError("WeKnora rebind completed; event dispatch needs manual review")
    if sender_after["status"] == "paused":
        if (sender_after.get("last_error_code") != "receiver_unauthorized" or
                after.sender_received != before.sender_received or
                after.receiver_received != after.sender_received):
            raise RuntimeError("WeKnora rebind completed; paused Nextcloud sender needs "
                               "manual review before retry")
        nc_status, resumed = nc_request(nc_session, csrf, nc_event_url + "/retry", "POST", {
            "connection_id": before.connection_id, "key_id": before.key_id,
            "received_through_event_id": sender_after["received_through_event_id"],
        })
        if nc_status != 200 or not isinstance(resumed, dict):
            raise RuntimeError(f"WeKnora rebind completed; Nextcloud sender CAS retry "
                               f"failed: HTTP {nc_status}")
        wk_status, receiver_after_retry = wk_request(wk_base, wk_token, "GET", wk_event_path)
        if wk_status != 200:
            raise RuntimeError("Nextcloud sender resumed; receiver status unavailable")
        retry_scope = checked_event_scope(resumed, receiver_after_retry, binding, instance_id)
        if (receiver_after_retry.get("status") != "active" or
                not has_safe_dispatch_status(receiver_after_retry)):
            raise RuntimeError("Nextcloud sender resumed; receiver dispatch now needs manual review")
        if any(current < previous for current, previous in zip(
                (retry_scope.receiver_received, retry_scope.receiver_dispatched,
                 retry_scope.receiver_applied),
                (after.receiver_received, after.receiver_dispatched,
                 after.receiver_applied))):
            raise RuntimeError("Nextcloud sender resumed; receiver watermarks regressed")
        if (resumed.get("status") != "active" or retry_scope.connection_id != before.connection_id or
                retry_scope.key_id != before.key_id or
                retry_scope.sender_received != after.sender_received or
                retry_scope.sender_applied < after.sender_applied):
            raise RuntimeError("Nextcloud sender retry did not preserve its connection and receipt")
        sender_action = "resumed"
        sender_after = resumed
        receiver_after = receiver_after_retry
    else:
        sender_action = "already_active"
    return {
        "connection_id": before.connection_id, "key_id": before.key_id,
        "rebound": rebound["rebound"],
        "received_through_event_id": receiver_after["received_through_event_id"],
        "dispatched_through_event_id": receiver_after["dispatched_through_event_id"],
        "applied_through_event_id": receiver_after["applied_through_event_id"],
        "dispatch_state": receiver_after["dispatch_state"],
        "last_error_code": receiver_after.get("last_error_code", ""),
    }, {
        "status": sender_after["status"], "action": sender_action,
        "received_through_event_id": sender_after["received_through_event_id"],
        "applied_through_event_id": sender_after["applied_through_event_id"],
    }


def main(argv=None, values_override=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("rotate", "retry", "status", "abort", "rebind"))
    parser.add_argument("--binding", required=True)
    parser.add_argument("--pair-operation-id", required=True, help="established source pairing UUID")
    parser.add_argument("--operation-id", help="rotation UUID; generated for a new rotation")
    parser.add_argument("--weknora-base-url", default="http://127.0.0.1:18081")
    args = parser.parse_args(argv)
    if args.action != "rotate" and not args.operation_id:
        parser.error("--operation-id is required for retry/status/abort/rebind")
    try:
        pair_id = str(uuid.UUID(args.pair_operation_id))
        operation_id = str(uuid.UUID(args.operation_id)) if args.operation_id else str(uuid.uuid4())
    except ValueError:
        parser.error("both operation IDs must be UUIDs")
    print(f"rotation_operation_id={operation_id}", file=sys.stderr, flush=True)

    values = load_env() if values_override is None else values_override
    nc_base = f"http://127.0.0.1:{values.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    nc_api = nc_base + "/index.php/apps/integration_weknora/api/v1"
    binding = urllib.parse.quote(args.binding, safe="")
    nc_pair_url = f"{nc_api}/admin/bindings/{binding}/source-pairing"
    nc_rotation_url = nc_pair_url + "/rotation"
    wk_base = args.weknora_base_url.rstrip("/")
    wk_rotation_path = ("/api/v1/datasource/nextcloud-source-pairings/" + pair_id +
                        "/rotations")
    wk_operation_path = wk_rotation_path + "/" + operation_id
    nc_session, csrf = login(nc_base, values["NEXTCLOUD_ADMIN_USER"],
                             values["NEXTCLOUD_ADMIN_PASSWORD"])
    wk_token, wk_tenant = wk_login(wk_base)

    pair_status, pair_body = nc_request(nc_session, csrf, nc_pair_url, "GET")
    nc_pair = pair_body.get("pairing") if pair_status == 200 else None
    if (not isinstance(nc_pair, dict) or nc_pair.get("operation_id") != pair_id or
            nc_pair.get("binding_id") != args.binding or nc_pair.get("state") != "active" or
            nc_pair.get("tenant_id") != wk_tenant):
        raise RuntimeError("the active Nextcloud pairing does not match this tenant and binding")

    if args.action == "rotate":
        nc_status, prepared = nc_request(nc_session, csrf, nc_rotation_url, "POST",
                                         {"operation_id": operation_id})
        rotation = rotation_body(prepared)
        if nc_status not in (200, 201) or rotation is None:
            raise RuntimeError(f"Nextcloud rotation prepare: HTTP {nc_status}")
        if (rotation.get("operation_id") != operation_id or
                rotation.get("pair_operation_id") != pair_id or
                rotation.get("binding_id") != args.binding):
            raise RuntimeError("Nextcloud rotation returned a different source pairing")
        if nc_status == 201:
            token = prepared.get("token")
            if not isinstance(token, str) or not token:
                raise RuntimeError("Nextcloud rotation returned no one-time token")
            wk_status, _ = wk_request(wk_base, wk_token, "POST", wk_rotation_path, {
                "operation_id": operation_id,
                "new_key_id": rotation.get("new_key_id"),
                "token": token,
            })
            del token
            if wk_status not in (200, 201, 202):
                raise RuntimeError(f"WeKnora rotation: HTTP {wk_status}; inspect both statuses")
        else:
            wk_status, _ = wk_request(wk_base, wk_token, "POST", wk_operation_path + "/retry")
            if wk_status == 404:
                raise RuntimeError("one-time token was not stored in WeKnora; abort the pending "
                                   "Nextcloud rotation and start a new operation")
            if wk_status not in (200, 202):
                raise RuntimeError(f"WeKnora rotation retry: HTTP {wk_status}")
    elif args.action == "retry":
        wk_status, _ = wk_request(wk_base, wk_token, "POST", wk_operation_path + "/retry")
        if wk_status not in (200, 202):
            raise RuntimeError(f"WeKnora rotation retry: HTTP {wk_status}")
    elif args.action == "abort":
        wk_status, _ = wk_request(wk_base, wk_token, "POST", wk_operation_path + "/abort")
        if wk_status == 404:
            nc_status, _ = nc_request(nc_session, csrf, nc_rotation_url, "DELETE",
                                      {"operation_id": operation_id})
            if nc_status != 200:
                raise RuntimeError(f"Nextcloud pending rotation abort: HTTP {nc_status}")
        elif wk_status != 200:
            raise RuntimeError(f"WeKnora signed pending rotation abort: HTTP {wk_status}")

    nc_status, nc_body = nc_request(nc_session, csrf, nc_rotation_url, "GET")
    nc_rotation = rotation_body(nc_body) if nc_status == 200 else None
    wk_status, wk_body = wk_request(wk_base, wk_token, "GET", wk_operation_path)
    wk_rotation = rotation_body(wk_body) if wk_status == 200 else None
    if not isinstance(nc_rotation, dict) or nc_rotation.get("operation_id") != operation_id:
        raise RuntimeError("Nextcloud latest rotation does not match the requested operation")
    if wk_rotation is not None and (wk_rotation.get("operation_id") != operation_id or
                                   wk_rotation.get("pair_operation_id") != pair_id or
                                   wk_rotation.get("binding_id") != args.binding):
        raise RuntimeError("WeKnora rotation identity differs from Nextcloud")
    result = {"pair_operation_id": pair_id, "rotation_operation_id": operation_id,
              "nextcloud_state": nc_rotation.get("state"),
              "weknora_state": wk_rotation.get("state") if wk_rotation else "not_stored"}
    if args.action == "rebind":
        if result["nextcloud_state"] != "finalized" or result["weknora_state"] != "finalized":
            raise RuntimeError("source rotation is not finalized on both services")
        require_rotation_scope(nc_pair, nc_rotation, wk_rotation, args.binding, pair_id,
                               operation_id, wk_tenant)
        datasource_id = wk_rotation.get("data_source_id")
        if not isinstance(datasource_id, str) or not datasource_id:
            raise RuntimeError("WeKnora rotation has no matching data source")
        nc_event_url = event_connection_url(nc_api, args.binding)
        result["event_connection"], result["event_sender"] = rebind_and_resume_event_sender(
            nc_session, csrf, nc_event_url, wk_base, wk_token, datasource_id, args.binding,
            nc_pair["instance_id"], operation_id)
    print(json.dumps(result, separators=(",", ":")))
    if args.action == "abort":
        if result["nextcloud_state"] != "aborted" or result["weknora_state"] not in ("aborted", "not_stored"):
            raise RuntimeError("rotation abort is incomplete; inspect both statuses")
    elif args.action != "status" and (result["nextcloud_state"] != "finalized" or
                                      result["weknora_state"] != "finalized"):
        raise RuntimeError("rotation remains in progress; retry the same operation")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, urllib.error.URLError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
