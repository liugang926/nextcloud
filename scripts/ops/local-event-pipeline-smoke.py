#!/usr/bin/env python3
"""Exercise Nextcloud outbox -> signed delivery -> WeKnora durable receipt.

Only operates on local loopback development stacks. Requires an actively
source-paired synthetic WeKnora data source and an unconfigured event connection.
Set WEKNORA_TEST_ADMIN_EMAIL/PASSWORD in the process environment. The test
creates a uniquely named WebDAV file and revokes both temporary connections.
By default it checks the durable receipt only. --expect-dispatch also waits for
the WeKnora worker to accept a full-source sync into its queue. --expect-applied
requires the real applied watermark on both services to cover the probe event.
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
from changes_http_smoke import file_id, request as dav_request  # noqa: E402
from publication_http_smoke import login, request as session_request  # noqa: E402

RECEIVER_PATH = "/api/v1/integrations/nextcloud/events"
JOB_CLASS = r"OCA\IntegrationWeknora\BackgroundJob\EventDeliveryJob"
DISPATCH_TIMEOUT_SECONDS = 120
DISPATCH_POLL_SECONDS = 3
APPLIED_TIMEOUT_SECONDS = 240
APPLIED_POLL_SECONDS = 5


def weknora_request(base, method, path, *, token=None):
    headers = {} if token is None else {"Authorization": "Bearer " + token}
    req = urllib.request.Request(base + path, data=b"" if method == "POST" else None,
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def expect(actual, expected, stage):
    if actual != expected:
        raise AssertionError(f"{stage}: HTTP {actual}, expected {expected}")


def weknora_login(base):
    email = os.environ.get("WEKNORA_TEST_ADMIN_EMAIL")
    password = os.environ.get("WEKNORA_TEST_ADMIN_PASSWORD")
    if not email or not password:
        raise RuntimeError("set WEKNORA_TEST_ADMIN_EMAIL and WEKNORA_TEST_ADMIN_PASSWORD")
    body = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(base + "/api/v1/auth/login", data=body,
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


def compose_command(compose_dir, env_file, *args):
    return ["docker", "compose", "--project-directory", str(compose_dir),
            "--env-file", str(env_file), *args]


def delivery_job_id(compose_dir, env_file):
    command = compose_command(compose_dir, env_file, "exec", "-T", "-u", "www-data", "nextcloud",
                              "php", "occ", "background-job:list", "--output=json")
    result = subprocess.run(command, cwd=compose_dir, check=True, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    matches = [item["id"] for item in json.loads(result.stdout)
               if item.get("class") == JOB_CLASS]
    if len(matches) != 1:
        raise AssertionError("exactly one Nextcloud event delivery job is required")
    return str(matches[0])


def run_delivery(compose_dir, env_file, job_id):
    command = compose_command(compose_dir, env_file, "exec", "-T", "-u", "www-data", "nextcloud",
                              "php", "occ", "background-job:execute", "--force-execute", job_id)
    subprocess.run(command, cwd=compose_dir, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def outbox_event_id(compose_dir, env_file, binding_id, source_file_id, event_type="upsert"):
    if event_type not in ("upsert", "delete"):
        raise ValueError("invalid event type")
    query = ("SELECT COALESCE(MAX(id),0) FROM oc_weknora_outbox "
             f"WHERE binding_id='{binding_id}' AND file_id={source_file_id} "
             f"AND event_type='{event_type}'")
    command = compose_command(compose_dir, env_file, "exec", "-T", "db", "psql", "-U",
                              "nextcloud", "-d", "nextcloud", "-Atc", query)
    result = subprocess.run(command, cwd=compose_dir, check=True, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    event_id = int(result.stdout.strip())
    if event_id <= 0:
        raise AssertionError(f"WebDAV {event_type} did not persist an outbox hint")
    return event_id


def decimal_status_id(status, field):
    value = status.get(field)
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]*", value):
        raise AssertionError(f"status did not expose a decimal {field}")
    return int(value)


def load_env_file(path):
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"').strip("'")
    return values


def local_origin(value, label):
    parts = urllib.parse.urlsplit(value)
    if (parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost") or
            parts.username is not None or parts.password is not None or
            parts.path or parts.query or parts.fragment or parts.port is None or
            value != f"http://{parts.hostname}:{parts.port}"):
        raise ValueError(f"{label} must be a canonical loopback HTTP origin with a port")
    return value


def receiver_origin(value):
    parts = urllib.parse.urlsplit(value)
    host = parts.hostname
    if (parts.scheme != "http" or host is None or
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or
            ".." in host or parts.username is not None or parts.password is not None or
            parts.path or parts.query or parts.fragment or parts.port is None or
            value != f"http://{host}:{parts.port}"):
        raise ValueError("--receiver-origin must be a canonical container HTTP origin with a port")
    return value


def wait_for_dispatch(weknora_base, source_url, source_admin, event_id):
    """Observe the durable queue-accepted checkpoint."""
    deadline = time.monotonic() + DISPATCH_TIMEOUT_SECONDS
    observed = None
    while True:
        http_status, body = weknora_request(weknora_base, "GET", source_url,
                                            token=source_admin)
        if http_status != 200:
            raise AssertionError(f"WeKnora dispatch status: HTTP {http_status}, expected 200")
        observed = json.loads(body)
        received = decimal_status_id(observed, "received_through_event_id")
        dispatched = decimal_status_id(observed, "dispatched_through_event_id")
        applied = decimal_status_id(observed, "applied_through_event_id")
        dispatch_state = observed.get("dispatch_state")
        error_code = observed.get("last_error_code")
        if not isinstance(dispatch_state, str) or not isinstance(error_code, str):
            raise AssertionError("WeKnora dispatch status lacks state or error code")
        if received < event_id:
            raise AssertionError(
                f"WeKnora receipt regressed during dispatch: event={event_id}, received={received}")
        # The dispatcher advances this ID only after enqueue succeeds. Its
        # state can later become retry while the queued sync is processed.
        if dispatched >= event_id:
            return observed
        if dispatch_state == "blocked":
            raise AssertionError(
                "WeKnora dispatch blocked before queue acceptance: "
                f"event={event_id}, received={received}, dispatched={dispatched}, "
                f"state={dispatch_state}, error={error_code or 'none'}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(
                "WeKnora dispatch timed out before queue acceptance: "
                f"event={event_id}, received={received}, dispatched={dispatched}, "
                f"applied={applied}, state={dispatch_state}, error={error_code or 'none'}")
        time.sleep(min(DISPATCH_POLL_SECONDS, remaining))


def wait_for_applied(weknora_base, source_url, source_admin, event_url,
                     cloud_admin, csrf, event_id, compose_dir, env_file, job_id):
    """Require WeKnora's applied ACK and Nextcloud's verified signed status."""
    deadline = time.monotonic() + APPLIED_TIMEOUT_SECONDS
    receiver_applied = sender_applied = 0
    sender_error = ""
    while True:
        status, body = weknora_request(weknora_base, "GET", source_url,
                                       token=source_admin)
        expect(status, 200, "read WeKnora applied status")
        receiver = json.loads(body)
        receiver_applied = decimal_status_id(receiver, "applied_through_event_id")
        receiver_received = decimal_status_id(receiver, "received_through_event_id")
        if receiver_applied > receiver_received:
            raise AssertionError("WeKnora applied checkpoint exceeds durable receipt")

        # The production delivery job polls signed applied status at most once
        # every 30 seconds. Repeated executions are safe and honor that gate.
        run_delivery(compose_dir, env_file, job_id)
        status, body = admin_request(cloud_admin, csrf, event_url, "GET")
        expect(status, 200, "read Nextcloud applied status")
        sender = json.loads(body)
        sender_applied = decimal_status_id(sender, "applied_through_event_id")
        sender_received = decimal_status_id(sender, "received_through_event_id")
        sender_error = sender.get("applied_error_code")
        if not isinstance(sender_error, str):
            raise AssertionError("Nextcloud status lacks applied error code")
        if sender_applied > sender_received:
            raise AssertionError("Nextcloud applied checkpoint exceeds durable receipt")
        if receiver_applied >= event_id and sender_applied >= event_id:
            return receiver, sender
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(
                "applied checkpoint timed out: "
                f"event={event_id}, receiver_applied={receiver_applied}, "
                f"sender_applied={sender_applied}, sender_error={sender_error or 'none'}")
        time.sleep(min(APPLIED_POLL_SECONDS, remaining))


def wait_for_receipt(weknora_base, source_url, source_admin, event_url,
                     cloud_admin, csrf, event_id, compose_dir, env_file, job_id):
    previous_received = -1
    for _ in range(40):
        run_delivery(compose_dir, env_file, job_id)
        status, body = admin_request(cloud_admin, csrf, event_url, "GET")
        expect(status, 200, "read Nextcloud sender status")
        cloud_status = json.loads(body)
        cloud_received = decimal_status_id(cloud_status, "received_through_event_id")
        if cloud_received >= event_id or cloud_status.get("status") != "active":
            break
        if cloud_received == previous_received:
            # The worker yields a busy binding between bounded five-batch runs.
            time.sleep(1.1)
        previous_received = cloud_received
    status, body = weknora_request(weknora_base, "GET", source_url,
                                   token=source_admin)
    expect(status, 200, "read WeKnora inbox status")
    receiver_status = json.loads(body)
    receiver_received = decimal_status_id(receiver_status, "received_through_event_id")
    if cloud_received < event_id or receiver_received < event_id or \
            receiver_received < cloud_received:
        raise AssertionError(
            "sender and receiver receipt mismatch: "
            f"event={event_id}, sender={cloud_received}, receiver={receiver_received}, "
            f"sender_state={cloud_status.get('status')}, "
            f"sender_error={cloud_status.get('last_error_code')}, "
            f"receiver_state={receiver_status.get('status')}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-source-id", required=True)
    parser.add_argument("--binding", default="dev-published")
    parser.add_argument("--dav-folder-url",
                        help="existing synthetic binding root WebDAV URL; required outside dev-published")
    parser.add_argument("--weknora-base-url", default="http://127.0.0.1:18081",
                        help="loopback WeKnora administrator API origin")
    parser.add_argument("--receiver-origin", default="http://app:8080",
                        help="WeKnora container origin reachable from Nextcloud")
    parser.add_argument("--nextcloud-base-url", help="loopback Nextcloud HTTP origin")
    parser.add_argument("--compose-directory", type=Path, default=PROJECT,
                        help="directory containing the running Nextcloud Compose stack")
    parser.add_argument("--env-file", type=Path,
                        help="Nextcloud stack .env (defaults to compose directory/.env)")
    parser.add_argument("--expect-dispatch", action="store_true",
                        help="also wait up to 120 seconds for WeKnora queue acceptance")
    parser.add_argument("--expect-applied", action="store_true",
                        help="require both real WeKnora and Nextcloud applied checkpoints")
    args = parser.parse_args()
    try:
        data_source_id = str(uuid.UUID(args.data_source_id))
        weknora_base = local_origin(args.weknora_base_url, "--weknora-base-url")
        receiver_base = receiver_origin(args.receiver_origin)
    except ValueError as error:
        parser.error(str(error))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", args.binding):
        parser.error("--binding must be a valid Nextcloud binding ID")
    compose_dir = args.compose_directory.resolve()
    env_file = (args.env_file or compose_dir / ".env").resolve()
    if not (compose_dir / "compose.yaml").is_file() or not env_file.is_file():
        parser.error("--compose-directory must contain compose.yaml and --env-file must exist")
    values = load_env_file(env_file)
    try:
        base = local_origin(args.nextcloud_base_url or
                            f"http://127.0.0.1:{values.get('NEXTCLOUD_HTTP_PORT', '18082')}",
                            "--nextcloud-base-url")
        owner = urllib.parse.quote(values["NEXTCLOUD_ADMIN_USER"], safe="")
        folder = args.dav_folder_url or (f"{base}/remote.php/dav/files/{owner}/Published"
                                         if args.binding == "dev-published" else None)
        if folder is None:
            parser.error("--dav-folder-url is required outside dev-published")
        parsed_folder = urllib.parse.urlsplit(folder)
        prefix = f"/remote.php/dav/files/{owner}/"
        if (folder != parsed_folder.geturl() or
                f"{parsed_folder.scheme}://{parsed_folder.netloc}" != base or
                not parsed_folder.path.startswith(prefix) or
                not parsed_folder.path[len(prefix):].strip("/") or
                parsed_folder.query or parsed_folder.fragment or
                any(segment in (".", "..") for segment in
                    urllib.parse.unquote(parsed_folder.path).split("/"))):
            parser.error("--dav-folder-url must be a folder below the local admin WebDAV root")
        folder = folder.rstrip("/")
    except (KeyError, ValueError) as error:
        parser.error(f"invalid local stack settings: {error}")

    api = base + "/index.php/apps/integration_weknora/api/v1"
    event_url = f"{api}/admin/bindings/{args.binding}/event-connection"
    source_url = f"/api/v1/datasource/{data_source_id}/nextcloud-event-connection"
    source_admin = weknora_login(weknora_base)
    cloud_admin, csrf = login(base, values["NEXTCLOUD_ADMIN_USER"],
                              values["NEXTCLOUD_ADMIN_PASSWORD"])

    status, body = weknora_request(weknora_base, "GET", source_url,
                                   token=source_admin)
    if status == 200:
        if json.loads(body).get("status") != "revoked":
            raise AssertionError("preflight WeKnora source already has an active connection")
    else:
        expect(status, 404, "preflight unpaired WeKnora source")
    status, _ = admin_request(cloud_admin, csrf, event_url, "GET")
    expect(status, 404, "preflight unpaired Nextcloud binding")

    path = f"{folder}/event-pipeline-{secrets.token_hex(8)}.txt"
    basic = base64.b64encode((values["NEXTCLOUD_ADMIN_USER"] + ":" +
                              values["NEXTCLOUD_ADMIN_PASSWORD"]).encode()).decode()
    dav_headers = {"Authorization": "Basic " + basic}
    paired_source = False
    paired_cloud = False
    created_file = False
    probe_applied = False
    connection_id = key_id = None
    source_file_id = None
    job_id = None
    try:
        status, body = weknora_request(weknora_base, "POST", source_url,
                                       token=source_admin)
        expect(status, 201, "pair synthetic WeKnora source")
        paired_source = True
        credential = json.loads(body)
        if credential.get("binding_id") != args.binding or \
                credential.get("receiver_url") != RECEIVER_PATH or \
                not isinstance(credential.get("secret"), str):
            raise AssertionError("pair response has unexpected binding or receiver")
        connection_id = credential.get("connection_id")
        key_id = credential.get("key_id")
        if not isinstance(connection_id, str) or not isinstance(key_id, str):
            raise AssertionError("pair response lacks connection identity")
        credential["receiver_url"] = receiver_base + RECEIVER_PATH
        status, _ = admin_request(cloud_admin, csrf, event_url, "POST", credential)
        expect(status, 201, "configure Nextcloud sender")
        paired_cloud = True
        del credential

        status, _ = dav_request(path, "PUT", dav_headers,
                                b"synthetic local event delivery probe\n")
        expect(status, 201, "create synthetic file")
        created_file = True
        source_file_id = file_id(path, dav_headers)
        event_id = outbox_event_id(compose_dir, env_file, args.binding, source_file_id)
        job_id = delivery_job_id(compose_dir, env_file)
        wait_for_receipt(weknora_base, source_url, source_admin, event_url,
                         cloud_admin, csrf, event_id, compose_dir, env_file, job_id)
        if args.expect_dispatch or args.expect_applied:
            wait_for_dispatch(weknora_base, source_url, source_admin, event_id)
        if args.expect_applied:
            wait_for_applied(weknora_base, source_url, source_admin, event_url,
                             cloud_admin, csrf, event_id, compose_dir, env_file, job_id)
            probe_applied = True
    finally:
        cleanup_errors = []
        if created_file:
            try:
                status, _ = dav_request(path, "DELETE", dav_headers)
                expect(status, 204, "cleanup synthetic file")
                if probe_applied:
                    # A published probe file must also be removed downstream.
                    delete_id = outbox_event_id(compose_dir, env_file, args.binding,
                                                source_file_id, "delete")
                    wait_for_receipt(weknora_base, source_url, source_admin, event_url,
                                     cloud_admin, csrf, delete_id, compose_dir, env_file, job_id)
                    wait_for_applied(weknora_base, source_url, source_admin, event_url,
                                     cloud_admin, csrf, delete_id, compose_dir, env_file, job_id)
            except Exception as error:
                cleanup_errors.append(error)
        if paired_source and not paired_cloud and connection_id is not None:
            try:
                status, body = admin_request(cloud_admin, csrf, event_url, "GET")
                if status == 200:
                    current = json.loads(body)
                    paired_cloud = (current.get("connection_id") == connection_id and
                                    current.get("key_id") == key_id)
                elif status != 404:
                    cleanup_errors.append(AssertionError(
                        f"inspect Nextcloud sender during cleanup: HTTP {status}"))
            except Exception as error:
                cleanup_errors.append(error)
        if paired_cloud:
            try:
                status, body = admin_request(cloud_admin, csrf, event_url, "GET")
                expect(status, 200, "inspect Nextcloud sender before cleanup")
                current = json.loads(body)
                if current.get("connection_id") != connection_id or \
                        current.get("key_id") != key_id:
                    raise AssertionError("Nextcloud sender identity changed; not revoking")
                status, _ = admin_request(cloud_admin, csrf, event_url, "DELETE")
                expect(status, 200, "revoke Nextcloud sender")
            except Exception as error:
                cleanup_errors.append(error)
        if paired_source:
            try:
                status, body = weknora_request(weknora_base, "GET", source_url,
                                               token=source_admin)
                expect(status, 200, "inspect WeKnora connection before cleanup")
                current = json.loads(body)
                if current.get("connection_id") != connection_id or \
                        current.get("key_id") != key_id:
                    raise AssertionError("WeKnora connection identity changed; not revoking")
                status, _ = weknora_request(weknora_base, "DELETE", source_url,
                                             token=source_admin)
                expect(status, 204, "revoke WeKnora event connection")
            except Exception as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            raise AssertionError("local event probe cleanup failed") from cleanup_errors[0]
    result = "signed delivery and durable receipt"
    if args.expect_dispatch or args.expect_applied:
        result += "; WeKnora full-source sync accepted into queue"
    if args.expect_applied:
        result += "; probe upsert and cleanup delete applied on both sides"
    print(f"local event pipeline smoke passed: {result}")


if __name__ == "__main__":
    main()
