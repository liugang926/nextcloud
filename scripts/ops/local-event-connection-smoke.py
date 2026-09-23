#!/usr/bin/env python3
"""Exercise a disposable event connection against the local WeKnora API.

Requires WEKNORA_TEST_ADMIN_EMAIL and WEKNORA_TEST_ADMIN_PASSWORD in the
process environment and an already configured synthetic Nextcloud data source.
The created connection is revoked in finally, including after an assertion.
"""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.request
import uuid


BASE = "http://127.0.0.1:18081"
RECEIVER = "/api/v1/integrations/nextcloud/events"


def request(method, path, *, token=None, body=None, headers=None):
    outgoing = dict(headers or {})
    if token is not None:
        outgoing["Authorization"] = "Bearer " + token
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    if data is not None:
        outgoing["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=outgoing)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def expect(status, expected, label):
    if status != expected:
        raise AssertionError(f"{label}: HTTP {status}, expected {expected}")


def login(email, password):
    status, data = request("POST", "/api/v1/auth/login",
                           body={"email": email, "password": password})
    expect(status, 200, "administrator login")
    result = json.loads(data)
    token = result.get("token")
    if result.get("success") is not True or not isinstance(token, str) or not token:
        raise AssertionError("administrator login returned no token")
    return token


def signed_event(credential, after_id, event_id, nonce=None):
    connection_id = credential["connection_id"]
    key_id = credential["key_id"]
    secret = credential["secret"]
    body = {
        "connection_id": connection_id,
        "nextcloud_instance_id": credential["nextcloud_instance_id"],
        "binding_id": credential["binding_id"],
        "after_event_id": str(after_id),
        "events": [{"event_id": str(event_id), "type": "reconcile"}],
    }
    raw = json.dumps(body, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    nonce = nonce or secrets.token_hex(16)
    canonical = "\n".join([
        "nextcloud-event-hmac-sha256-v1", "POST", RECEIVER, "",
        hashlib.sha256(raw).hexdigest(), timestamp, nonce, connection_id, key_id,
    ]).encode()
    signature = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Nextcloud-Connection-Id": connection_id,
        "X-Nextcloud-Key-Id": key_id,
        "X-Nextcloud-Timestamp": timestamp,
        "X-Nextcloud-Nonce": nonce,
        "X-Nextcloud-Signature": signature,
    }
    req = urllib.request.Request(BASE + RECEIVER, data=raw, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read(), nonce
    except urllib.error.HTTPError as error:
        return error.code, error.read(), nonce


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-source-id", required=True,
                        help="UUID of the local synthetic Nextcloud data source")
    args = parser.parse_args()
    try:
        data_source_id = str(uuid.UUID(args.data_source_id))
    except ValueError:
        parser.error("--data-source-id must be a UUID")
    email = os.environ.get("WEKNORA_TEST_ADMIN_EMAIL")
    password = os.environ.get("WEKNORA_TEST_ADMIN_PASSWORD")
    if not email or not password:
        parser.error("set WEKNORA_TEST_ADMIN_EMAIL and WEKNORA_TEST_ADMIN_PASSWORD")
    admin = login(email, password)
    path = f"/api/v1/datasource/{data_source_id}/nextcloud-event-connection"
    status, data = request("POST", path, token=admin)
    expect(status, 201, "pair synthetic data source")
    credential = json.loads(data)
    if credential.get("receiver_url") != RECEIVER or not credential.get("secret"):
        raise AssertionError("pair response did not return a one-time event secret")
    try:
        status, data, nonce = signed_event(credential, 0, 1)
        expect(status, 202, "durable event receipt")
        receipt = json.loads(data)
        if receipt.get("received_through_event_id") != "1" or receipt.get("durable_receipt_only") is not True:
            raise AssertionError("event receipt did not report durable-only watermark")
        status, _, _ = signed_event(credential, 0, 1, nonce=nonce)
        expect(status, 401, "nonce replay")
        status, _, _ = signed_event(credential, 0, 1)
        expect(status, 202, "idempotent retry with fresh nonce")

        status, data = request("POST", path + "/rotate", token=admin)
        expect(status, 200, "rotate event key")
        replacement = json.loads(data)
        if replacement.get("connection_id") != credential["connection_id"] or \
                replacement.get("secret") == credential["secret"]:
            raise AssertionError("rotation did not replace the connection secret")
        status, _, _ = signed_event(credential, 1, 2)
        expect(status, 401, "old event key after rotation")
        status, _, _ = signed_event(replacement, 1, 2)
        expect(status, 202, "rotated event key")

        status, _ = request("GET", path, token=admin)
        expect(status, 200, "connection status")
    finally:
        status, _ = request("DELETE", path, token=admin)
        expect(status, 204, "revoke synthetic event connection")
    status, _, _ = signed_event(replacement, 2, 3)
    expect(status, 401, "revoked event connection")
    print("local event connection smoke passed: receipt, replay, rotation, status, revocation")


if __name__ == "__main__":
    main()
