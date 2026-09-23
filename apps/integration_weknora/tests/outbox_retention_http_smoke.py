#!/usr/bin/env python3
"""Exercise Nextcloud outbox retention against local HTTP and PostgreSQL.

Requires the bootstrapped Compose stack and app migration. All test events
belong to a temporary binding; cleanup removes that binding and its DB rows.
"""

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import secrets
import subprocess
import time
import urllib.parse

from changes_http_smoke import drain, file_id, load_env, request, PROJECT
from publication_http_smoke import (
    issue_machine_key, login, remove_binding, request as session_request,
    revoke_machine_key,
)


def sql(statement):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-X", "-A", "-t",
         "-v", "ON_ERROR_STOP=1", "-U", "nextcloud", "-d", "nextcloud",
         "-c", statement],
        cwd=PROJECT, check=True, text=True, capture_output=True,
    )
    return result.stdout.strip()


def prune(binding_id):
    php = ("require '/var/www/html/lib/base.php'; "
           "echo \\OC::$server->get(\\OCA\\IntegrationWeknora\\Service\\ChangeOutboxService::class)"
           f"->pruneExpiredBinding('{binding_id}');")
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "-u", "www-data", "nextcloud",
         "php", "-r", php], cwd=PROJECT, check=True, text=True,
        capture_output=True,
    )
    return int(result.stdout.strip())


def change_id(events, target_file_id):
    ids = [int(item["event_id"]) for item in events
           if item["type"] == "upsert" and item["file_id"] == target_file_id]
    assert ids, events
    return max(ids)


def check_page_lock(changes_url, headers, cursor):
    """A page request must wait for the lock also used by cleanup/append."""
    process = subprocess.Popen(
        ["docker", "compose", "exec", "-T", "db", "psql", "-X", "-A", "-t",
         "-v", "ON_ERROR_STOP=1", "-U", "nextcloud", "-d", "nextcloud"],
        cwd=PROJECT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    try:
        assert process.stdin and process.stdout
        process.stdin.write("BEGIN;\nSELECT id FROM oc_weknora_outbox_lock WHERE id = 1 FOR UPDATE;\n")
        process.stdin.flush()
        for _ in range(10):
            if process.stdout.readline().strip() == "1":
                break
        else:
            raise AssertionError("could not acquire outbox lock")
        with ThreadPoolExecutor(max_workers=1) as pool:
            url = changes_url + "?" + urllib.parse.urlencode({"cursor": cursor})
            pending = pool.submit(request, url, headers=headers)
            time.sleep(0.4)
            assert not pending.done(), "change page did not wait for outbox lock"
            process.stdin.write("COMMIT;\n\\q\n")
            process.stdin.flush()
            status, body = pending.result(timeout=20)
            assert status == 200, (status, body[:300])
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)


def main():
    env = load_env()
    assert sql("SELECT COUNT(*) FROM pg_indexes WHERE tablename = 'oc_weknora_outbox' "
               "AND indexname = 'weknora_outbox_retention'") == "1"
    assert sql("SELECT COUNT(*) FROM oc_jobs WHERE "
               "class = 'OCA\\IntegrationWeknora\\BackgroundJob\\OutboxRetentionJob'") == "1"
    base = f"http://127.0.0.1:{env.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    owner = urllib.parse.quote(env["NEXTCLOUD_ADMIN_USER"])
    dav_base = f"{base}/remote.php/dav/files/{owner}"
    auth = base64.b64encode(
        f"{env['NEXTCLOUD_ADMIN_USER']}:{env['NEXTCLOUD_ADMIN_PASSWORD']}".encode()
    ).decode()
    dav_headers = {"Authorization": f"Basic {auth}"}
    machine_headers = None
    key_id = None
    admin = csrf = None
    api = f"{base}/index.php/apps/integration_weknora/api/v1"
    suffix = secrets.token_hex(8)
    binding_id = f"retention-{suffix}"
    root_url = f"{dav_base}/{binding_id}"
    changes_url = (f"{base}/index.php/apps/integration_weknora/api/v1/"
                   f"bindings/{binding_id}/changes")
    configured = False
    try:
        status, body = request(root_url, "MKCOL", dav_headers)
        assert status == 201, (status, body[:300])
        root_id = file_id(root_url, dav_headers)
        admin, csrf = login(base, env["NEXTCLOUD_ADMIN_USER"],
                            env["NEXTCLOUD_ADMIN_PASSWORD"])
        payload = json.dumps({
            "id": binding_id, "name": "Retention test",
            "owner_uid": env["NEXTCLOUD_ADMIN_USER"], "root_file_id": root_id,
        }).encode()
        status, body = session_request(
            admin, f"{base}/index.php/apps/integration_weknora/api/v1/admin/bindings",
            "POST", {"requesttoken": csrf, "Content-Type": "application/json"}, payload,
        )
        assert status == 201, (status, body[:300])
        configured = True
        machine_headers, key_id = issue_machine_key(admin, api, binding_id, csrf)
        _, initial_cursor = drain(changes_url, machine_headers)

        ids = []
        cursor = initial_cursor
        for name in ("old", "fresh", "old-after-fresh"):
            url = f"{root_url}/{name}.txt"
            status, body = request(url, "PUT", dav_headers, name.encode())
            assert status in (201, 204), (status, body[:300])
            target_id = file_id(url, dav_headers)
            events, cursor = drain(changes_url, machine_headers, cursor)
            ids.append(change_id(events, target_id))
        old_id, fresh_id, later_old_id = ids
        assert old_id < fresh_id < later_old_id, ids

        prefix_ids = [int(value) for value in sql(
            "SELECT id FROM oc_weknora_outbox "
            f"WHERE binding_id = '{binding_id}' AND id <= {old_id} ORDER BY id"
        ).splitlines()]
        remaining_ids = [int(value) for value in sql(
            "SELECT id FROM oc_weknora_outbox "
            f"WHERE binding_id = '{binding_id}' AND id > {old_id} ORDER BY id"
        ).splitlines()]
        assert prefix_ids and fresh_id in remaining_ids and later_old_id in remaining_ids
        aged = int(time.time()) - 31 * 86400
        still_retained = int(time.time()) - 29 * 86400
        sql("UPDATE oc_weknora_outbox SET created_at = " + str(still_retained) +
            f" WHERE binding_id = '{binding_id}' AND id > {old_id} AND id <= {fresh_id}")
        sql("UPDATE oc_weknora_outbox SET created_at = " + str(aged) +
            f" WHERE binding_id = '{binding_id}' AND "
            f"(id <= {old_id} OR (id > {fresh_id} AND id <= {later_old_id}))")
        pruned = prune(binding_id)
        assert pruned == len(prefix_ids), pruned
        assert prune(binding_id) == 0
        assert sql("SELECT floor_id FROM oc_weknora_change_floor "
                   f"WHERE binding_id = '{binding_id}'") == str(old_id)
        remaining = sql("SELECT id FROM oc_weknora_outbox "
                        f"WHERE binding_id = '{binding_id}' ORDER BY id")
        assert [int(value) for value in remaining.splitlines()] == remaining_ids, remaining

        status, body = request(
            changes_url + "?" + urllib.parse.urlencode({"cursor": initial_cursor}),
            headers=machine_headers,
        )
        expired = json.loads(body)
        assert status == 409 and expired["rescan_required"] is True, (status, body[:300])
        events, checkpoint = drain(changes_url, machine_headers, expired["next_cursor"])
        assert [int(item["event_id"]) for item in events] == remaining_ids
        check_page_lock(changes_url, machine_headers, checkpoint)

        status, body = request(f"{root_url}/after-prune.txt", "PUT", dav_headers, b"new")
        assert status in (201, 204), (status, body[:300])
        events, _ = drain(changes_url, machine_headers, checkpoint)
        assert events and int(events[-1]["event_id"]) > later_old_id, events
        print("outbox retention HTTP/DB smoke passed")
    finally:
        try:
            if key_id is not None:
                revoke_machine_key(admin, api, binding_id, key_id, csrf)
        finally:
            try:
                if configured:
                    remove_binding(admin, api, binding_id, csrf)
            finally:
                try:
                    request(root_url, "DELETE", dav_headers)
                finally:
                    sql(f"DELETE FROM oc_weknora_outbox WHERE binding_id = '{binding_id}'; "
                        f"DELETE FROM oc_weknora_change_floor WHERE binding_id = '{binding_id}'")


if __name__ == "__main__":
    main()
