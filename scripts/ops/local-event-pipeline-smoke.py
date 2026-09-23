#!/usr/bin/env python3
"""Exercise Nextcloud outbox -> signed delivery -> WeKnora durable receipt.

Only operates on the two local loopback development stacks. Requires an
existing synthetic Nextcloud data source and an unpaired dev-published binding.
Set WEKNORA_TEST_ADMIN_EMAIL/PASSWORD in the process environment. The test
creates a uniquely named WebDAV file and revokes both temporary connections.
It does not claim that WeKnora has applied or published the event.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "apps/integration_weknora/tests"))
from changes_http_smoke import file_id, load_env, request as dav_request  # noqa: E402
from publication_http_smoke import login, request as session_request  # noqa: E402

WEKNORA = "http://127.0.0.1:18081"
RECEIVER_PATH = "/api/v1/integrations/nextcloud/events"
JOB_CLASS = r"OCA\IntegrationWeknora\BackgroundJob\EventDeliveryJob"


def weknora_request(method, path, *, token=None):
    headers = {} if token is None else {"Authorization": "Bearer " + token}
    req = urllib.request.Request(WEKNORA + path, data=b"" if method == "POST" else None,
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def expect(actual, expected, stage):
    if actual != expected:
        raise AssertionError(f"{stage}: HTTP {actual}, expected {expected}")


def weknora_login():
    email = os.environ.get("WEKNORA_TEST_ADMIN_EMAIL")
    password = os.environ.get("WEKNORA_TEST_ADMIN_PASSWORD")
    if not email or not password:
        raise RuntimeError("set WEKNORA_TEST_ADMIN_EMAIL and WEKNORA_TEST_ADMIN_PASSWORD")
    body = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(WEKNORA + "/api/v1/auth/login", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.load(response)
    token = result.get("token")
    if result.get("success") is not True or not isinstance(token, str) or not token:
        raise AssertionError("WeKnora administrator login returned no token")
    return token


def admin_request(admin, csrf, url, method, payload=None):
    headers = {"requesttoken": csrf}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode()
    return session_request(admin, url, method, headers, body)


def delivery_job_id():
    command = ["docker", "compose", "exec", "-T", "-u", "www-data", "nextcloud",
               "php", "occ", "background-job:list", "--output=json"]
    result = subprocess.run(command, cwd=PROJECT, check=True, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    matches = [item["id"] for item in json.loads(result.stdout)
               if item.get("class") == JOB_CLASS]
    if len(matches) != 1:
        raise AssertionError("exactly one Nextcloud event delivery job is required")
    return str(matches[0])


def run_delivery(job_id):
    command = ["docker", "compose", "exec", "-T", "-u", "www-data", "nextcloud",
               "php", "occ", "background-job:execute", "--force-execute", job_id]
    subprocess.run(command, cwd=PROJECT, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def outbox_event_id(binding_id, source_file_id):
    query = ("SELECT COALESCE(MAX(id),0) FROM oc_weknora_outbox "
             f"WHERE binding_id='{binding_id}' AND file_id={source_file_id} "
             "AND event_type='upsert'")
    command = ["docker", "compose", "exec", "-T", "db", "psql", "-U",
               "nextcloud", "-d", "nextcloud", "-Atc", query]
    result = subprocess.run(command, cwd=PROJECT, check=True, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    event_id = int(result.stdout.strip())
    if event_id <= 0:
        raise AssertionError("WebDAV write did not persist an outbox hint")
    return event_id


def receipt_watermark(body):
    value = json.loads(body).get("received_through_event_id")
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]*", value):
        raise AssertionError("status did not expose a decimal receipt watermark")
    return int(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-source-id", required=True)
    parser.add_argument("--binding", default="dev-published")
    args = parser.parse_args()
    data_source_id = str(uuid.UUID(args.data_source_id))
    if args.binding != "dev-published":
        parser.error("this local smoke is limited to the synthetic dev-published binding")

    values = load_env()
    base = f"http://127.0.0.1:{values.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    api = base + "/index.php/apps/integration_weknora/api/v1"
    event_url = f"{api}/admin/bindings/{args.binding}/event-connection"
    source_url = f"/api/v1/datasource/{data_source_id}/nextcloud-event-connection"
    source_admin = weknora_login()
    cloud_admin, csrf = login(base, values["NEXTCLOUD_ADMIN_USER"],
                              values["NEXTCLOUD_ADMIN_PASSWORD"])

    status, body = weknora_request("GET", source_url, token=source_admin)
    if status == 200:
        if json.loads(body).get("status") != "revoked":
            raise AssertionError("preflight WeKnora source already has an active connection")
    else:
        expect(status, 404, "preflight unpaired WeKnora source")
    status, _ = admin_request(cloud_admin, csrf, event_url, "GET")
    expect(status, 404, "preflight unpaired Nextcloud binding")

    owner = urllib.parse.quote(values["NEXTCLOUD_ADMIN_USER"])
    path = (f"{base}/remote.php/dav/files/{owner}/Published/"
            f"event-pipeline-{secrets.token_hex(8)}.txt")
    basic = base64.b64encode((values["NEXTCLOUD_ADMIN_USER"] + ":" +
                              values["NEXTCLOUD_ADMIN_PASSWORD"]).encode()).decode()
    dav_headers = {"Authorization": "Basic " + basic}
    paired_source = False
    paired_cloud = False
    created_file = False
    try:
        status, body = weknora_request("POST", source_url, token=source_admin)
        expect(status, 201, "pair synthetic WeKnora source")
        paired_source = True
        credential = json.loads(body)
        if credential.get("binding_id") != args.binding or \
                credential.get("receiver_url") != RECEIVER_PATH or \
                not isinstance(credential.get("secret"), str):
            raise AssertionError("pair response has unexpected binding or receiver")
        credential["receiver_url"] = "http://app:8080" + RECEIVER_PATH
        status, _ = admin_request(cloud_admin, csrf, event_url, "POST", credential)
        expect(status, 201, "configure Nextcloud sender")
        paired_cloud = True
        del credential

        status, _ = dav_request(path, "PUT", dav_headers,
                                b"synthetic local event delivery probe\n")
        expect(status, 201, "create synthetic file")
        created_file = True
        source_file_id = file_id(path, dav_headers)
        event_id = outbox_event_id(args.binding, source_file_id)
        job_id = delivery_job_id()
        previous_received = -1
        for _ in range(40):
            run_delivery(job_id)
            status, body = admin_request(cloud_admin, csrf, event_url, "GET")
            expect(status, 200, "read Nextcloud sender status")
            cloud_status = json.loads(body)
            cloud_received = receipt_watermark(body)
            if cloud_received >= event_id or cloud_status.get("status") != "active":
                break
            if cloud_received == previous_received:
                # The production worker yields a busy binding for one second
                # between bounded five-batch runs to avoid starving others.
                time.sleep(1.1)
            previous_received = cloud_received
        status, body = weknora_request("GET", source_url, token=source_admin)
        expect(status, 200, "read WeKnora inbox status")
        receiver_status = json.loads(body)
        receiver_received = receipt_watermark(body)
        if cloud_received < event_id or receiver_received < event_id or \
                cloud_received != receiver_received:
            raise AssertionError(
                "sender and receiver watermark mismatch: "
                f"event={event_id}, sender={cloud_received}, receiver={receiver_received}, "
                f"sender_state={cloud_status.get('status')}, "
                f"sender_error={cloud_status.get('last_error_code')}, "
                f"receiver_state={receiver_status.get('status')}")
    finally:
        cleanup_errors = []
        if created_file:
            try:
                status, _ = dav_request(path, "DELETE", dav_headers)
                expect(status, 204, "cleanup synthetic file")
            except Exception as error:
                cleanup_errors.append(error)
        if paired_cloud:
            try:
                status, _ = admin_request(cloud_admin, csrf, event_url, "DELETE")
                expect(status, 200, "revoke Nextcloud sender")
            except Exception as error:
                cleanup_errors.append(error)
        if paired_source:
            try:
                status, _ = weknora_request("DELETE", source_url, token=source_admin)
                expect(status, 204, "revoke WeKnora event connection")
            except Exception as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            raise AssertionError("local event probe cleanup failed") from cleanup_errors[0]
    print("local event pipeline smoke passed: signed delivery and durable receipt")


if __name__ == "__main__":
    main()
