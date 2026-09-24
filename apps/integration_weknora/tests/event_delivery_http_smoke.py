#!/usr/bin/env python3
"""End-to-end local sender faults with an isolated Docker-network receiver.

Run after `docker compose up -d --force-recreate nextcloud cron` and `occ upgrade`.
The Compose development allowlist must include event-receiver-smoke:8080.
Only synthetic bindings, rows and a container created by this process are
removed. One-time secrets are held in process memory and never printed.
"""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.parse
import uuid

from changes_http_smoke import file_id, request as dav_request
from outbox_retention_http_smoke import prune, sql
from publication_http_smoke import PROJECT, load_env, login, request


COMPOSE_NAME = os.environ.get("COMPOSE_PROJECT_NAME", "nextcloud-weknora-dev")
MOCK_NAME = COMPOSE_NAME + "-event-receiver-smoke"
MOCK_URL = "http://event-receiver-smoke:8080/api/v1/integrations/nextcloud/events"
JOB_CLASS = "OCA\\IntegrationWeknora\\BackgroundJob\\EventDeliveryJob"
STATUS_JOB_CLASS = "OCA\\IntegrationWeknora\\BackgroundJob\\EventAppliedStatusJob"


def compose(*args):
    result = subprocess.run(["docker", "compose", *args], cwd=PROJECT,
                            text=True, capture_output=True, check=True)
    return result.stdout.strip()


def post_json(admin, url, payload, csrf):
    return request(admin, url, "POST", {"requesttoken": csrf,
                   "Content-Type": "application/json"},
                   json.dumps(payload, separators=(",", ":")).encode())


def status(admin, url, csrf):
    code, body = request(admin, url, headers={"requesttoken": csrf})
    assert code == 200, f"sender status HTTP {code}"
    data = json.loads(body)
    assert "secret" not in data and "secret_ciphertext" not in data
    return data


def job_id(job_class=JOB_CLASS):
    data = json.loads(compose("exec", "-T", "-u", "www-data", "nextcloud", "php", "occ",
                              "background-job:list", "--class=" + job_class,
                              "--output=json"))
    assert len(data) == 1 and data[0]["class"] == job_class, data
    return data[0]["id"]


def run_job(identifier):
    compose("exec", "-T", "-u", "www-data", "nextcloud", "php", "occ",
            "background-job:execute", "--force-execute", identifier)


def requests_seen(state_dir):
    path = state_dir / "requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def status_requests_seen(state_dir):
    path = state_dir / "status_requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def verify_signature(request_record, secret):
    raw = request_record["body"].encode()
    payload = json.loads(raw)
    canonical = "\n".join([
        "nextcloud-event-hmac-sha256-v1", "POST",
        "/api/v1/integrations/nextcloud/events", "",
        hashlib.sha256(raw).hexdigest(), request_record["timestamp"],
        request_record["nonce"], request_record["connection_id"],
        request_record["key_id"],
    ]).encode()
    expected = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(expected, request_record["signature"])
    assert request_record["connection_id"] == payload["connection_id"]
    assert request_record["key_id"]
    assert len(request_record["nonce"]) == 32
    assert payload["events"] and len(payload["events"]) <= 200
    return payload


def approve_private_https_policy():
    php = (
        "require '/var/www/html/custom_apps/integration_weknora/lib/Service/"
        "EventReceiverPolicy.php'; "
        "$p = new \\OCA\\IntegrationWeknora\\Service\\EventReceiverPolicy(); "
        "$v = $p->requireApproved('https://weknora.internal/api/v1/"
        "integrations/nextcloud/events'); echo $v['allow_local'] ? 'approved' : 'blocked';"
    )
    result = subprocess.run([
        "docker", "compose", "exec", "-T", "-e",
        "WEKNORA_EVENT_ALLOWED_ORIGINS=https://weknora.internal", "-e",
        "WEKNORA_EVENT_DEV_HTTP=0", "nextcloud", "php", "-r", php,
    ], cwd=PROJECT, text=True, capture_output=True, check=True)
    assert result.stdout == "approved"


def add_idle_rows(prefix):
    ids = [f"aa-event-idle-{prefix}-{index:02d}" for index in range(10)]
    values = []
    for binding_id in ids:
        values.append("(" + ",".join([
            f"'{binding_id}'", f"'{uuid.uuid4()}'", "'evt_idle'",
            "'synthetic-never-decrypted'", f"'{MOCK_URL}'",
            "0", "'active'", "0", "0", "''", "0", "0",
        ]) + ")")
    sql("INSERT INTO oc_weknora_event_conn (binding_id,connection_id,key_id,"
        "secret_ciphertext,receiver_url,received_id,status,attempt_count,"
        "next_attempt_at,last_error_code,created_at,updated_at) VALUES " +
        ",".join(values))
    return ids


def clear_idle_rows(ids):
    if ids:
        sql("DELETE FROM oc_weknora_event_conn WHERE binding_id IN (" +
            ",".join(f"'{value}'" for value in ids) + ")")


def force_due(binding_id):
    sql("UPDATE oc_weknora_event_conn SET next_attempt_at = 0 "
        f"WHERE binding_id = '{binding_id}'")


def force_status_due(binding_id):
    sql("UPDATE oc_weknora_event_conn SET applied_checked_at = 0 "
        f"WHERE binding_id = '{binding_id}'")


def poll_status(binding_id):
    php = ("require '/var/www/html/lib/base.php'; "
           "echo \\OC::$server->get(\\OCA\\IntegrationWeknora\\Service\\EventAppliedStatusService::class)"
           f"->poll('{binding_id}') ? '1' : '0';")
    return compose("exec", "-T", "-u", "www-data", "nextcloud", "php", "-r", php) == "1"


def update_mock_credential(state_dir, credential):
    (state_dir / "config.json").write_text(json.dumps(credential))


def main():
    approve_private_https_policy()
    if subprocess.run(["docker", "container", "inspect", MOCK_NAME],
                      capture_output=True).returncode == 0:
        raise RuntimeError("temporary mock container name is already in use")
    env = load_env()
    base = f"http://127.0.0.1:{env.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    api = f"{base}/index.php/apps/integration_weknora/api/v1"
    admin_uid = env["NEXTCLOUD_ADMIN_USER"]
    admin, csrf = login(base, admin_uid, env["NEXTCLOUD_ADMIN_PASSWORD"])
    dav_headers = {"Authorization": "Basic " + base64.b64encode(
        f"{admin_uid}:{env['NEXTCLOUD_ADMIN_PASSWORD']}".encode()).decode()}
    suffix = secrets.token_hex(5)
    binding_id = f"zz-event-smoke-{suffix}"
    root_url = f"{base}/remote.php/dav/files/{urllib.parse.quote(admin_uid)}/{binding_id}"
    connection_url = f"{api}/admin/bindings/{binding_id}/event-connection"
    binding_url = f"{api}/admin/bindings"
    mock_script = Path(__file__).with_name("event_receiver_mock.py")
    idle_ids = []
    mock_container = None
    folder_created = binding_created = connection_created = False
    with tempfile.TemporaryDirectory(prefix="weknora-event-smoke-") as tmp:
        # Drive both independent loops one pass at a time. Restore the status
        # worker only after cleanup so it cannot race watermark assertions.
        running_workers = set(compose(
            "ps", "--status", "running", "--services").splitlines())
        worker_running = "event-worker" in running_workers
        status_worker_running = "event-status-worker" in running_workers
        stopped_workers = []
        try:
            for worker in ("event-worker", "event-status-worker"):
                if worker in running_workers:
                    compose("stop", worker)
                    stopped_workers.append(worker)
        except Exception:
            for worker in stopped_workers:
                compose("start", worker)
            raise
        state_dir = Path(tmp)
        (state_dir / "mode").write_text("503")
        (state_dir / "received").write_text("0")
        (state_dir / "applied").write_text("0")
        try:
            image = "python:3.12-alpine@sha256:4c47124a8391cb7a9f571164147d154777cf012a4ece5f86097130d7a4478111"
            result = subprocess.run([
                "docker", "run", "--detach", "--rm", "--name", MOCK_NAME,
                "--network", COMPOSE_NAME + "_default",
                "--network-alias", "event-receiver-smoke",
                "--volume", f"{mock_script}:/srv/mock.py:ro",
                "--volume", f"{state_dir}:/state:rw",
                image, "python", "/srv/mock.py",
            ], cwd=PROJECT, text=True, capture_output=True, check=True)
            mock_container = result.stdout.strip()
            for _ in range(50):
                probe = subprocess.run([
                    "docker", "compose", "exec", "-T", "nextcloud", "curl", "-fsS",
                    "http://event-receiver-smoke:8080/health",
                ], cwd=PROJECT, capture_output=True)
                if probe.returncode == 0:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("mock receiver was not reachable from Nextcloud")

            code, _ = dav_request(root_url, "MKCOL", dav_headers)
            assert code == 201, f"create fixture folder HTTP {code}"
            folder_created = True
            root_id = file_id(root_url, dav_headers)
            code, _ = post_json(admin, binding_url, {
                "id": binding_id, "name": binding_id,
                "owner_uid": admin_uid, "root_file_id": root_id,
            }, csrf)
            assert code == 201, f"create fixture binding HTTP {code}"
            binding_created = True

            instance_id = compose("exec", "-T", "-u", "www-data", "nextcloud",
                                  "php", "occ", "config:system:get", "instanceid")
            connection_id = str(uuid.uuid4())
            secret = secrets.token_urlsafe(32)
            key_id = "evt_" + secrets.token_hex(16)
            credential = {
                "binding_id": binding_id,
                "nextcloud_instance_id": instance_id,
                "connection_id": connection_id,
                "key_id": key_id,
                "secret": secret,
                "receiver_url": MOCK_URL,
            }
            code, _ = request(admin, connection_url, "POST",
                              {"Content-Type": "application/json"},
                              json.dumps(credential).encode())
            assert code in (401, 403, 412), f"missing CSRF token HTTP {code}"
            code, _ = post_json(admin, connection_url,
                                {**credential, "receiver_url":
                                 "http://169.254.169.254/api/v1/integrations/nextcloud/events"}, csrf)
            assert code == 400, f"unapproved metadata origin HTTP {code}"
            code, _ = post_json(admin, connection_url,
                                {**credential, "nextcloud_instance_id": "wrong-instance"}, csrf)
            assert code == 400, f"wrong instance HTTP {code}"
            code, body = post_json(admin, connection_url, credential, csrf)
            assert code == 201, f"configure sender HTTP {code}"
            update_mock_credential(state_dir, credential)
            connection_created = True
            assert secret.encode() not in body
            assert status(admin, connection_url, csrf)["received_through_event_id"] == "0"
            ciphertext = sql("SELECT secret_ciphertext FROM oc_weknora_event_conn "
                             f"WHERE binding_id = '{binding_id}'")
            assert ciphertext and secret not in ciphertext

            file_url = root_url + "/first.txt"
            code, _ = dav_request(file_url, "PUT", dav_headers, b"first event")
            assert code in (201, 204), f"create fixture event HTTP {code}"
            idle_ids = add_idle_rows(suffix)
            identifier = job_id()
            run_job(identifier)
            assert requests_seen(state_dir) == [], "eleventh sender was not fairly queued"
            assert status(admin, connection_url, csrf)["received_through_event_id"] == "0"
            assert all(int(value) > 0 for value in sql(
                "SELECT next_attempt_at FROM oc_weknora_event_conn "
                f"WHERE binding_id LIKE 'aa-event-idle-{suffix}-%' ORDER BY binding_id"
            ).splitlines())
            clear_idle_rows(idle_ids)
            idle_ids = []

            run_job(identifier)
            first = status(admin, connection_url, csrf)
            assert first["status"] == "active" and first["attempt_count"] == 1
            assert first["last_error_code"] == "receiver_retryable"
            assert first["next_attempt_at"] > int(time.time())
            sent = requests_seen(state_dir)
            assert len(sent) == 1
            verify_signature(sent[-1], secret)
            run_job(identifier)
            assert len(requests_seen(state_dir)) == 1, "retry ignored persisted backoff"

            force_due(binding_id)
            (state_dir / "mode").write_text("bad_receipt")
            run_job(identifier)
            assert status(admin, connection_url, csrf)["last_error_code"] == "invalid_receipt"
            assert status(admin, connection_url, csrf)["received_through_event_id"] == "0"

            force_due(binding_id)
            (state_dir / "mode").write_text("ahead_receipt")
            run_job(identifier)
            ahead = status(admin, connection_url, csrf)
            assert ahead["status"] == "paused" and ahead["last_error_code"] == "checkpoint_divergence"
            assert ahead["received_through_event_id"] == "0"
            run_job(identifier)
            assert len(requests_seen(state_dir)) == 3, "paused sender retried"

            secret = secrets.token_urlsafe(32)
            credential.update(secret=secret, key_id="evt_" + secrets.token_hex(16))
            code, body = post_json(admin, connection_url, credential, csrf)
            assert code == 200 and secret.encode() not in body
            update_mock_credential(state_dir, credential)
            (state_dir / "mode").write_text("accept")
            force_status_due(binding_id)
            status_count = len(status_requests_seen(state_dir))
            run_job(identifier)
            received = status(admin, connection_url, csrf)
            assert received["status"] == "active" and int(received["received_through_event_id"]) > 0
            assert len(status_requests_seen(state_dir)) == status_count, (
                "sender synchronously polled applied status")
            payload = verify_signature(requests_seen(state_dir)[-1], secret)
            assert received["received_through_event_id"] == payload["events"][-1]["event_id"]

            # The separate command and ordinary cron fallback still verify
            # signed applied watermarks without running inside delivery.
            compose("exec", "-T", "-u", "www-data", "nextcloud", "php", "occ",
                    "integration_weknora:poll-event-status")
            assert len(status_requests_seen(state_dir)) == status_count + 1
            assert status(admin, connection_url, csrf)["applied_checked_at"] > 0
            force_status_due(binding_id)
            run_job(job_id(STATUS_JOB_CLASS))
            assert len(status_requests_seen(state_dir)) == status_count + 2

            # A durable 202 receipt cannot expire even an aged prefix. The
            # separate signed status must attest its applied watermark.
            aged = int(time.time()) - 31 * 86400
            sql("UPDATE oc_weknora_outbox SET created_at = " + str(aged) +
                f" WHERE binding_id = '{binding_id}'")
            assert prune(binding_id) == 0
            assert received["applied_through_event_id"] == "0"
            (state_dir / "applied").write_text(str(int(
                received["received_through_event_id"]) + 1))
            force_status_due(binding_id)
            assert not poll_status(binding_id)
            assert prune(binding_id) == 0, "ahead applied status released outbox"
            (state_dir / "applied").write_text(received["received_through_event_id"])
            (state_dir / "status_binding_override").write_text("wrong-binding")
            force_status_due(binding_id)
            assert not poll_status(binding_id)
            assert status(admin, connection_url, csrf)["applied_error_code"] == "status_invalid"
            assert prune(binding_id) == 0, "wrong-scope status released outbox"
            (state_dir / "status_binding_override").unlink()
            force_status_due(binding_id)
            assert poll_status(binding_id), "signed applied status was rejected"
            acknowledged = status(admin, connection_url, csrf)
            assert acknowledged["applied_through_event_id"] == received["received_through_event_id"]
            assert acknowledged["applied_error_code"] == ""
            (state_dir / "applied").write_text("0")
            force_status_due(binding_id)
            assert not poll_status(binding_id)
            assert prune(binding_id) == 0, "regressing applied status released outbox"
            (state_dir / "applied").write_text(received["received_through_event_id"])
            force_status_due(binding_id)
            assert poll_status(binding_id)
            assert prune(binding_id) > 0
            assert sql("SELECT COUNT(*) FROM oc_weknora_outbox "
                       f"WHERE binding_id = '{binding_id}' AND id <= "
                       f"{received['received_through_event_id']}") == "0"
            assert (state_dir / "status_requests.jsonl").exists()

            code, _ = dav_request(root_url + "/second.txt", "PUT", dav_headers, b"second event")
            assert code in (201, 204)
            assert int(sql("SELECT next_attempt_at FROM oc_weknora_event_conn "
                           f"WHERE binding_id = '{binding_id}'")) == 0, "new hint did not wake idle sender"
            (state_dir / "mode").write_text("redirect")
            run_job(identifier)
            redirected = status(admin, connection_url, csrf)
            assert redirected["status"] == "paused" and redirected["last_error_code"] == "redirect_rejected", redirected
            assert redirected["received_through_event_id"] == received["received_through_event_id"]

            pending = sql("SELECT id FROM oc_weknora_outbox "
                          f"WHERE binding_id = '{binding_id}' AND id > "
                          f"{received['received_through_event_id']} ORDER BY id")
            assert pending
            aged = int(time.time()) - 31 * 86400
            sql("UPDATE oc_weknora_outbox SET created_at = " + str(aged) +
                f" WHERE binding_id = '{binding_id}'")
            prune(binding_id)
            assert sql("SELECT id FROM oc_weknora_outbox "
                       f"WHERE binding_id = '{binding_id}' AND id > "
                       f"{received['received_through_event_id']} ORDER BY id") == pending

            secret = secrets.token_urlsafe(32)
            credential.update(secret=secret, key_id="evt_" + secrets.token_hex(16))
            code, _ = post_json(admin, connection_url, credential, csrf)
            assert code == 200
            update_mock_credential(state_dir, credential)
            (state_dir / "mode").write_text("401")
            run_job(identifier)
            unauthorized = status(admin, connection_url, csrf)
            assert unauthorized["status"] == "paused" and unauthorized["last_error_code"] == "receiver_unauthorized"
            assert unauthorized["received_through_event_id"] == received["received_through_event_id"]

            secret = secrets.token_urlsafe(32)
            credential.update(secret=secret, key_id="evt_" + secrets.token_hex(16))
            code, _ = post_json(admin, connection_url, credential, csrf)
            assert code == 200
            update_mock_credential(state_dir, credential)
            (state_dir / "mode").write_text("accept")
            run_job(identifier)
            final = status(admin, connection_url, csrf)
            assert final["status"] == "active"
            assert int(final["received_through_event_id"]) > int(received["received_through_event_id"])
            verify_signature(requests_seen(state_dir)[-1], secret)
            if worker_running:
                # Let the restarted worker take an empty pass, then verify
                # that a later file hint wakes and reaches the receiver.
                compose("start", "event-worker")
                time.sleep(6)
                code, _ = dav_request(root_url + "/worker.txt", "PUT", dav_headers,
                                      b"event worker delivery")
                assert code in (201, 204), f"create worker event HTTP {code}"
                started = time.monotonic()
                previous_id = int(final["received_through_event_id"])
                deadline = started + 20
                while time.monotonic() < deadline:
                    observed = status(admin, connection_url, csrf)
                    if int(observed["received_through_event_id"]) > previous_id:
                        verify_signature(requests_seen(state_dir)[-1], secret)
                        print(f"local event worker wake-to-receipt sample: {time.monotonic() - started:.2f}s")
                        break
                    time.sleep(0.25)
                else:
                    raise AssertionError("running event worker did not deliver a new file hint")
            code, _ = request(admin, f"{binding_url}/{binding_id}", "DELETE",
                              {"requesttoken": csrf})
            assert code == 200, f"delete fixture binding HTTP {code}"
            binding_created = False
            connection_created = False
            code, _ = request(admin, connection_url, headers={"requesttoken": csrf})
            assert code == 404, "binding deletion left event credential active"
            print("event delivery HTTP/fault smoke passed: fairness, signature, retry, receipts, pause, retention, rotation")
        finally:
            try:
                clear_idle_rows(idle_ids)
                if connection_created:
                    request(admin, connection_url, "DELETE", {"requesttoken": csrf})
                if binding_created:
                    request(admin, f"{binding_url}/{binding_id}", "DELETE", {"requesttoken": csrf})
                if folder_created:
                    dav_request(root_url, "DELETE", dav_headers)
                if mock_container:
                    subprocess.run(["docker", "stop", mock_container], cwd=PROJECT,
                                   text=True, capture_output=True, check=False)
            finally:
                try:
                    if worker_running:
                        compose("start", "event-worker")
                finally:
                    if status_worker_running:
                        compose("start", "event-status-worker")


if __name__ == "__main__":
    main()
