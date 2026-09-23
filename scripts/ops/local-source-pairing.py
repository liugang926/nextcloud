#!/usr/bin/env python3
"""Pair one local Nextcloud binding with an empty WeKnora knowledge base.

The two services keep durable operation state. If a request fails, rerun with
``--operation-id`` to inspect or retry the same operation. This tool never logs
the one-time Nextcloud machine token and never deletes a knowledge base.

Requires WEKNORA_TEST_ADMIN_EMAIL and WEKNORA_TEST_ADMIN_PASSWORD in the
process environment, plus the local Nextcloud .env admin credentials.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "apps/integration_weknora/tests"))
from changes_http_smoke import load_env  # noqa: E402
from publication_http_smoke import login, request as session_request  # noqa: E402


def wk_login(base):
    email = os.environ.get("WEKNORA_TEST_ADMIN_EMAIL")
    password = os.environ.get("WEKNORA_TEST_ADMIN_PASSWORD")
    if not email or not password:
        raise RuntimeError("set WEKNORA_TEST_ADMIN_EMAIL and WEKNORA_TEST_ADMIN_PASSWORD")
    payload = json.dumps({"email": email, "password": password}).encode()
    request = urllib.request.Request(
        base + "/api/v1/auth/login", data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)
    token = result.get("token")
    if result.get("success") is not True or not isinstance(token, str) or not token:
        raise RuntimeError("WeKnora administrator login failed")
    active_tenant = result.get("active_tenant")
    tenant_id = active_tenant.get("id") if isinstance(active_tenant, dict) else None
    if not isinstance(tenant_id, int) or isinstance(tenant_id, bool) or tenant_id <= 0:
        raise RuntimeError("WeKnora login returned no active tenant")
    return token, str(tenant_id)


def wk_request(base, token, method, path, payload=None):
    headers = {"Authorization": "Bearer " + token}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode()
    request = urllib.request.Request(base + path, data=body, method=method,
                                     headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        # Error details may contain remote data. Return only a stable code.
        return error.code, {}


def nc_request(session, csrf, url, method, payload=None):
    headers = {"requesttoken": csrf}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode()
    status, response = session_request(session, url, method, headers, body)
    try:
        return status, json.loads(response)
    except (TypeError, ValueError):
        return status, {}


def require_pairing(status, body, stage):
    if status != 200 or not isinstance(body.get("pairing"), dict):
        raise RuntimeError(f"{stage}: HTTP {status}")
    return body["pairing"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pair", "retry", "status"))
    parser.add_argument("--binding", required=True)
    parser.add_argument("--operation-id", help="UUID; generated for a new pair")
    parser.add_argument("--tenant-id", help="canonical decimal WeKnora tenant ID")
    parser.add_argument("--knowledge-base-id", help="precreated empty dedicated KB ID")
    parser.add_argument("--nextcloud-machine-base-url", default="http://nextcloud",
                        help="Nextcloud origin reachable from WeKnora")
    parser.add_argument("--weknora-base-url", default="http://127.0.0.1:18081",
                        help="local administrator API origin")
    args = parser.parse_args()

    if args.action != "pair" and not args.operation_id:
        parser.error("--operation-id is required for retry/status")
    if args.action == "pair" and (not args.tenant_id or not args.knowledge_base_id):
        parser.error("pair requires --tenant-id and --knowledge-base-id")
    if args.tenant_id and (not args.tenant_id.isascii() or
                           not args.tenant_id.isdecimal() or
                           args.tenant_id.startswith("0") or
                           len(args.tenant_id) > 20 or
                           int(args.tenant_id) > 18446744073709551615):
        parser.error("--tenant-id must be a canonical positive decimal string")
    try:
        operation_id = str(uuid.UUID(args.operation_id)) if args.operation_id else str(uuid.uuid4())
    except ValueError:
        parser.error("--operation-id must be a UUID")
    print(f"operation_id={operation_id}", file=sys.stderr, flush=True)

    values = load_env()
    nc_base = f"http://127.0.0.1:{values.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    nc_api = nc_base + "/index.php/apps/integration_weknora/api/v1"
    binding = urllib.parse.quote(args.binding, safe="")
    nc_pair_url = f"{nc_api}/admin/bindings/{binding}/source-pairing"
    wk_pair_path = "/api/v1/datasource/nextcloud-source-pairings"
    wk_base = args.weknora_base_url.rstrip("/")

    nc_session, csrf = login(nc_base, values["NEXTCLOUD_ADMIN_USER"],
                             values["NEXTCLOUD_ADMIN_PASSWORD"])
    wk_token, wk_tenant_id = wk_login(wk_base)
    if args.action == "pair" and args.tenant_id != wk_tenant_id:
        raise RuntimeError("--tenant-id does not match the WeKnora login tenant")

    if args.action == "pair":
        status, prepared = nc_request(nc_session, csrf, nc_pair_url, "POST", {
            "operation_id": operation_id,
            "tenant_id": args.tenant_id,
            "knowledge_base_id": args.knowledge_base_id,
        })
        if status not in (200, 201) or not isinstance(prepared.get("pairing"), dict):
            raise RuntimeError(f"Nextcloud prepare: HTTP {status}; operation_id={operation_id}")
        pairing = prepared["pairing"]
        if status == 201:
            one_time_token = prepared.get("token")
            if not isinstance(one_time_token, str) or not one_time_token:
                raise RuntimeError("Nextcloud prepare returned no one-time token")
            wk_status, _ = wk_request(wk_base, wk_token, "POST", wk_pair_path, {
                "knowledge_base_id": args.knowledge_base_id,
                "base_url": args.nextcloud_machine_base_url,
                "binding_id": args.binding,
                "operation_id": operation_id,
                "instance_id": pairing.get("instance_id"),
                "publication_epoch": pairing.get("publication_epoch"),
                "key_id": pairing.get("key_id"),
                "token": one_time_token,
            })
            del one_time_token
            if wk_status not in (200, 201, 202):
                raise RuntimeError(
                    f"WeKnora pair: HTTP {wk_status}; inspect operation_id={operation_id}")
        else:
            # The token is deliberately not recoverable from a repeated prepare.
            wk_status, _ = wk_request(wk_base, wk_token, "POST",
                                      f"{wk_pair_path}/{operation_id}/retry")
            if wk_status not in (200, 202):
                raise RuntimeError(
                    f"WeKnora retry: HTTP {wk_status}; inspect operation_id={operation_id}")
    elif args.action == "retry":
        status, _ = wk_request(wk_base, wk_token, "POST",
                               f"{wk_pair_path}/{operation_id}/retry")
        if status not in (200, 202):
            raise RuntimeError(f"WeKnora retry: HTTP {status}; operation_id={operation_id}")

    nc_status, nc_body = nc_request(nc_session, csrf, nc_pair_url, "GET")
    nc_pair = require_pairing(nc_status, nc_body, "Nextcloud status")
    wk_status, wk_body = wk_request(wk_base, wk_token, "GET",
                                    f"{wk_pair_path}/{operation_id}")
    if wk_status != 200:
        raise RuntimeError(f"WeKnora status: HTTP {wk_status}; operation_id={operation_id}")
    wk_pair = wk_body.get("pairing", wk_body)
    if not isinstance(wk_pair, dict):
        raise RuntimeError("WeKnora status has no pairing object")
    if (nc_pair.get("operation_id") != operation_id or
            wk_pair.get("operation_id") != operation_id or
            nc_pair.get("binding_id") != args.binding or
            wk_pair.get("binding_id") != args.binding or
            nc_pair.get("knowledge_base_id") != wk_pair.get("knowledge_base_id") or
            nc_pair.get("instance_id") != wk_pair.get("instance_id")):
        raise RuntimeError("source pairing identities differ across services")
    if nc_pair.get("state") == "active" and wk_pair.get("state") == "active":
        if (not isinstance(nc_pair.get("data_source_id"), str) or
                nc_pair.get("data_source_id") != wk_pair.get("data_source_id")):
            raise RuntimeError("active source pairing data sources differ")
    print(json.dumps({
        "operation_id": operation_id,
        "nextcloud_state": nc_pair.get("state"),
        "weknora_state": wk_pair.get("state"),
        "data_source_id": wk_pair.get("data_source_id"),
    }, separators=(",", ":")))
    if nc_pair.get("state") != "active" or wk_pair.get("state") != "active":
        raise RuntimeError("pairing remains pending; retry the same operation after repair")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, urllib.error.URLError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
