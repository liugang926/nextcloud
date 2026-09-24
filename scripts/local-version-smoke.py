#!/usr/bin/env python3
"""Manual synthetic Nextcloud-to-WeKnora publication version smoke."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "apps" / "integration_weknora" / "tests"))
from machine_auth import signed_headers  # noqa: E402


class SmokeError(RuntimeError):
    pass


def local_env() -> dict[str, str]:
    values = {}
    for line in (PROJECT / ".env").read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("NEXTCLOUD_ADMIN_USER", "NEXTCLOUD_ADMIN_PASSWORD", "WEKNORA_SERVICE_TOKEN"):
        if not values.get(key):
            raise SmokeError(f"local .env is missing {key}")
    return values


def http(method: str, url: str, *, headers: dict[str, str] | None = None,
         body: bytes | None = None, signed: bool = False) -> tuple[int, bytes]:
    headers = headers or {}
    if signed:
        headers = signed_headers(method, url, headers, body)
    request = urllib.request.Request(url, method=method, headers=headers, data=body)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def required_http(method: str, url: str, expected: tuple[int, ...], *,
                  headers: dict[str, str] | None = None, body: bytes | None = None,
                  signed: bool = False) -> bytes:
    status, result = http(method, url, headers=headers, body=body, signed=signed)
    if status not in expected:
        raise SmokeError(f"{method} request returned HTTP {status}, expected {expected}")
    return result


def postgres_json(sql: str):
    # The SQL includes only validated UUIDs and a generated numeric file ID.
    command = [
        "docker", "exec", "weknora-ldap-local-postgres-1", "sh", "-c",
        'psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"',
        "sh", sql,
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=20)
    if result.returncode != 0:
        raise SmokeError("local WeKnora PostgreSQL query failed; check its container and schema")
    output = result.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as error:
        raise SmokeError("local WeKnora PostgreSQL returned invalid JSON") from error


def validate_data_source(data_source_id: str) -> None:
    row = postgres_json(
        "SELECT to_jsonb(d) FROM (SELECT type, status, sync_deletions, "
        "config->'resource_ids' AS resource_ids FROM data_sources "
        f"WHERE id = '{data_source_id}' AND deleted_at IS NULL) d"
    )
    if not row or row["type"] != "nextcloud" or row["status"] != "active" or not row["sync_deletions"]:
        raise SmokeError("data source must be an active Nextcloud source with deletion sync enabled")
    if row["resource_ids"] != ["dev-published"]:
        raise SmokeError("data source must select only the local dev-published binding")


def version_snapshot(data_source_id: str, external_id: str) -> dict:
    # external_id is built from a server instance ID and a numeric WebDAV ID.
    query = f"""
        SELECT jsonb_build_object(
            'version', (SELECT jsonb_build_object(
                'state', v.state, 'desired_etag', v.desired_etag,
                'candidate_id', v.candidate_knowledge_id)
                FROM nextcloud_source_versions v
                WHERE v.tenant_id = d.tenant_id AND v.knowledge_base_id = d.knowledge_base_id
                  AND v.datasource_id = d.id AND v.external_id = '{external_id}'),
            'rows', (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'id', k.id, 'etag', COALESCE(k.metadata->>'nextcloud_etag', ''),
                'parse', k.parse_status, 'enabled', k.enable_status,
                'deleted', k.deleted_at IS NOT NULL)), '[]'::jsonb)
                FROM knowledges k
                WHERE k.tenant_id = d.tenant_id AND k.knowledge_base_id = d.knowledge_base_id
                  AND k.channel = 'nextcloud'
                  AND k.metadata->>'datasource_id' = d.id
                  AND k.metadata->>'external_id' = '{external_id}')
        ) FROM data_sources d WHERE d.id = '{data_source_id}'
    """
    result = postgres_json(query)
    if not isinstance(result, dict):
        raise SmokeError("data source disappeared during version probe")
    return result


def visible_rows(snapshot: dict) -> list[dict]:
    return [row for row in snapshot["rows"] if
            not row["deleted"] and row["enabled"] == "enabled" and
            row["parse"] == "completed" and row["etag"]]


def poll(description: str, timeout_seconds: int, predicate):
    deadline = time.monotonic() + timeout_seconds
    latest = None
    while time.monotonic() < deadline:
        latest = predicate()
        if latest is not None:
            return latest
        time.sleep(2)
    raise SmokeError(f"timed out waiting for {description}")


def login(weknora_base: str, email: str, password: str) -> str:
    data = json.dumps({"email": email, "password": password}).encode()
    body = required_http(
        "POST", f"{weknora_base}/api/v1/auth/login", (200,),
        headers={"Content-Type": "application/json"}, body=data,
    )
    response = json.loads(body)
    token = response.get("token")
    if not response.get("success") or not isinstance(token, str) or not token:
        raise SmokeError("WeKnora test administrator login did not return a token")
    return token


def trigger_sync(weknora_base: str, data_source_id: str, token: str) -> str:
    body = required_http(
        "POST", f"{weknora_base}/api/v1/datasource/{data_source_id}/sync", (200,),
        headers={"Authorization": f"Bearer {token}"}, body=b"",
    )
    log_id = json.loads(body).get("id")
    try:
        return str(uuid.UUID(log_id))
    except (ValueError, TypeError) as error:
        raise SmokeError("WeKnora manual sync did not return a log ID") from error


def wait_sync(data_source_id: str, log_id: str, timeout_seconds: int) -> None:
    def check():
        row = postgres_json(
            "SELECT to_jsonb(l) FROM (SELECT status, items_failed FROM sync_logs "
            f"WHERE id = '{log_id}' AND data_source_id = '{data_source_id}') l"
        )
        if row is None or row["status"] == "running":
            return None
        if row["status"] != "success" or row["items_failed"]:
            raise SmokeError(f"WeKnora sync {log_id} ended with status {row['status']}")
        return row
    poll(f"sync log {log_id}", timeout_seconds, check)


def wait_published(data_source_id: str, external_id: str, timeout_seconds: int,
                   previous_id: str = "", previous_etag: str = "") -> dict:
    def check():
        snapshot = version_snapshot(data_source_id, external_id)
        version = snapshot["version"]
        if not version or version["state"] != "published":
            return None
        candidate_id = version["candidate_id"]
        if not candidate_id or candidate_id == previous_id or not version["desired_etag"] or \
                version["desired_etag"] == previous_etag:
            return None
        visible = visible_rows(snapshot)
        if len(visible) != 1 or visible[0]["id"] != candidate_id or \
                visible[0]["etag"] != version["desired_etag"]:
            return None
        if previous_id and not any(row["id"] == previous_id for row in snapshot["rows"]):
            raise SmokeError("older knowledge row was physically removed during overwrite")
        return version
    return poll("one published candidate", timeout_seconds, check)


def wait_tombstone(data_source_id: str, external_id: str, timeout_seconds: int) -> None:
    def check():
        snapshot = version_snapshot(data_source_id, external_id)
        version = snapshot["version"]
        if version and version["state"] == "tombstone" and not version["candidate_id"] and \
                not version["desired_etag"] and not visible_rows(snapshot):
            return True
        return None
    poll("deletion tombstone and zero visible candidates", timeout_seconds, check)


def webdav_file_id(url: str, headers: dict[str, str]) -> int:
    query = (b'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
             b'<d:prop><oc:fileid/></d:prop></d:propfind>')
    body = required_http("PROPFIND", url, (207,), headers={
        **headers, "Depth": "0", "Content-Type": "application/xml",
    }, body=query)
    value = ET.fromstring(body).find(".//{http://owncloud.org/ns}fileid")
    if value is None or not value.text or not value.text.isdigit():
        raise SmokeError("WebDAV did not return a numeric file ID")
    return int(value.text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-source-id", required=True, help="local WeKnora Nextcloud data source UUID")
    parser.add_argument("--timeout-seconds", type=int, default=180, help="bound for each sync or parse phase")
    args = parser.parse_args()
    if args.timeout_seconds < 10 or args.timeout_seconds > 600:
        parser.error("--timeout-seconds must be between 10 and 600")
    try:
        data_source_id = str(uuid.UUID(args.data_source_id))
    except ValueError:
        parser.error("--data-source-id must be a UUID")

    email = os.environ.get("WEKNORA_TEST_ADMIN_EMAIL")
    password = os.environ.get("WEKNORA_TEST_ADMIN_PASSWORD")
    if not email or not password:
        parser.error("WEKNORA_TEST_ADMIN_EMAIL and WEKNORA_TEST_ADMIN_PASSWORD are required")
    env = local_env()
    port = env.get("NEXTCLOUD_HTTP_PORT", "18082")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise SmokeError("invalid local Nextcloud HTTP port in .env")
    nextcloud_base = f"http://127.0.0.1:{port}"
    weknora_base = "http://127.0.0.1:18081"
    validate_data_source(data_source_id)

    api = f"{nextcloud_base}/index.php/apps/integration_weknora/api/v1"
    machine_headers = {"Authorization": f"Bearer {env['WEKNORA_SERVICE_TOKEN']}"}
    capabilities = json.loads(required_http("GET", f"{api}/capabilities", (200,),
                                            headers=machine_headers, signed=True))
    instance_id = capabilities.get("instance_id", "")
    if not instance_id.isalnum():
        raise SmokeError("Nextcloud capabilities returned an invalid instance ID")
    token = login(weknora_base, email, password)

    auth = base64.b64encode(
        f"{env['NEXTCLOUD_ADMIN_USER']}:{env['NEXTCLOUD_ADMIN_PASSWORD']}".encode()
    ).decode()
    dav_headers = {"Authorization": f"Basic {auth}"}
    filename = f"version-smoke-{secrets.token_hex(8)}.md"
    file_url = (f"{nextcloud_base}/remote.php/dav/files/"
                f"{urllib.parse.quote(env['NEXTCLOUD_ADMIN_USER'], safe='')}/Published/{filename}")
    attempted_upload = False
    deleted = False
    cleaned = False
    try:
        attempted_upload = True
        required_http("PUT", file_url, (201,), headers=dav_headers,
                      body=f"# Synthetic version one\n\nProbe {filename}\n".encode())
        file_id = webdav_file_id(file_url, dav_headers)
        external_id = f"nextcloud:{instance_id}:{file_id}"
        print(f"Created synthetic {filename} (file ID {file_id})")

        first_sync = trigger_sync(weknora_base, data_source_id, token)
        wait_sync(data_source_id, first_sync, args.timeout_seconds)
        first = wait_published(data_source_id, external_id, args.timeout_seconds)
        print(f"First candidate published: {first['candidate_id']}")

        required_http("PUT", file_url, (204,), headers=dav_headers,
                      body=f"# Synthetic version two\n\nChanged probe {filename}\n".encode())
        if webdav_file_id(file_url, dav_headers) != file_id:
            raise SmokeError("WebDAV overwrite changed the source file ID")
        second_sync = trigger_sync(weknora_base, data_source_id, token)
        wait_sync(data_source_id, second_sync, args.timeout_seconds)
        second = wait_published(data_source_id, external_id, args.timeout_seconds,
                                first["candidate_id"], first["desired_etag"])
        print(f"Overwrite published a new candidate: {second['candidate_id']}")

        required_http("DELETE", file_url, (204,), headers=dav_headers)
        deleted = True
        for number in (1, 2):
            sync_id = trigger_sync(weknora_base, data_source_id, token)
            wait_sync(data_source_id, sync_id, args.timeout_seconds)
            print(f"Deletion reconciliation sync {number} completed")
        wait_tombstone(data_source_id, external_id, args.timeout_seconds)
        cleaned = True
        print("Version smoke passed: candidate changed; tombstone has zero visible candidates")
    finally:
        if attempted_upload and not deleted:
            status, _ = http("DELETE", file_url, headers=dav_headers)
            if status in (204, 404):
                deleted = True
            else:
                print(f"WARNING: probe file cleanup returned HTTP {status}", file=sys.stderr)
        if deleted and not cleaned:
            # Reconcile a failed probe too, so its candidate cannot remain
            # published after the source file has been removed.
            for _ in range(2):
                try:
                    sync_id = trigger_sync(weknora_base, data_source_id, token)
                    wait_sync(data_source_id, sync_id, args.timeout_seconds)
                except (SmokeError, subprocess.TimeoutExpired) as error:
                    print(f"WARNING: cleanup sync failed: {error}", file=sys.stderr)
                    break


if __name__ == "__main__":
    try:
        main()
    except (SmokeError, OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as error:
        print(f"Version smoke failed: {error}", file=sys.stderr)
        sys.exit(1)
