#!/usr/bin/env python3
"""Rotate an established local Nextcloud ↔ WeKnora source-pair credential.

The one-time Nextcloud token is passed directly to WeKnora and never printed.
Keep both operation IDs for status, retry, or safe pending abort.
"""

import argparse
import json
from pathlib import Path
import runpy
import sys
import urllib.error
import urllib.parse
import uuid

_client = runpy.run_path(str(Path(__file__).with_name("local-source-pairing.py")))
load_env = _client["load_env"]
login = _client["login"]
nc_request = _client["nc_request"]
wk_login = _client["wk_login"]
wk_request = _client["wk_request"]


def rotation_body(body):
    value = body.get("rotation")
    return value if isinstance(value, dict) else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("rotate", "retry", "status", "abort"))
    parser.add_argument("--binding", required=True)
    parser.add_argument("--pair-operation-id", required=True, help="established source pairing UUID")
    parser.add_argument("--operation-id", help="rotation UUID; generated for a new rotation")
    parser.add_argument("--weknora-base-url", default="http://127.0.0.1:18081")
    args = parser.parse_args()
    if args.action != "rotate" and not args.operation_id:
        parser.error("--operation-id is required for retry/status/abort")
    try:
        pair_id = str(uuid.UUID(args.pair_operation_id))
        operation_id = str(uuid.UUID(args.operation_id)) if args.operation_id else str(uuid.uuid4())
    except ValueError:
        parser.error("both operation IDs must be UUIDs")
    print(f"rotation_operation_id={operation_id}", file=sys.stderr, flush=True)

    values = load_env()
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
