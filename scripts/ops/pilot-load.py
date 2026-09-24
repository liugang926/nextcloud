#!/usr/bin/env python3
"""Measure a synthetic Nextcloud to WeKnora pilot on two disposable stacks.

Creates one owned folder, binding, knowledge base and paired data source. The
fixture is intentionally retained for inspection and must be discarded by
removing the two disposable Compose projects and their volumes. Never point
this at the shared development stacks. No credentials or document text are
written to the report.
"""

import argparse
import base64
import json
import math
import os
from pathlib import Path
import re
import runpy
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/integration_weknora/tests"))
from changes_http_smoke import file_id, request as dav_request  # noqa: E402
from publication_http_smoke import login  # noqa: E402

pairing = runpy.run_path(str(Path(__file__).with_name("local-source-pairing.py")))
nc_request = pairing["nc_request"]
wk_login = pairing["wk_login"]
wk_request = pairing["wk_request"]
helpers = runpy.run_path(str(Path(__file__).with_name("local-source-pairing-abort-smoke.py")))
origin = helpers["origin"]
env_values = helpers["env_file_values"]
compose_container = helpers["compose_container"]
inspect = helpers["inspect"]
event_helpers = runpy.run_path(str(Path(__file__).with_name("local-event-pipeline-smoke.py")))
decimal_status_id = event_helpers["decimal_status_id"]
wk_no_content_request = event_helpers["weknora_request"]
RECEIVER = "/api/v1/integrations/nextcloud/events"
PILOT_PROJECT = re.compile(r"pilot-[a-z0-9][a-z0-9-]{0,59}\Z")
SAFE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")


def require_status(actual, wanted, stage):
    if actual != wanted:
        raise RuntimeError(f"{stage}: HTTP {actual}, expected {wanted}")


def docker_sql(db_container, query):
    # Query interpolation is limited to UUIDs and decimal IDs validated by the
    # caller. The database is an explicitly isolated Compose service.
    result = subprocess.run([
        "docker", "exec", db_container, "sh", "-c",
        'psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"',
        "sh", query,
    ], text=True, capture_output=True, timeout=25, check=False)
    if result.returncode:
        raise RuntimeError("isolated WeKnora PostgreSQL read failed")
    rows = result.stdout.strip().splitlines()
    if len(rows) != 1:
        raise RuntimeError("isolated WeKnora PostgreSQL returned an unexpected row count")
    return json.loads(rows[0])


def process_rss_bytes(container):
    result = subprocess.run([
        "docker", "exec", container, "sh", "-c",
        "awk '/^VmRSS:/ {print $2}' /proc/1/status",
    ], text=True, capture_output=True, timeout=10, check=False)
    if result.returncode or not result.stdout.strip().isdigit():
        raise RuntimeError("cannot sample WeKnora process RSS")
    return int(result.stdout.strip()) * 1024


class RssSampler:
    """Process RSS samples, not a claim about connector-only Go heap."""

    def __init__(self, container, interval):
        self.container = container
        self.interval = interval
        self.samples = []
        self.errors = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        while not self.stop.is_set():
            try:
                self.samples.append(process_rss_bytes(self.container))
            except RuntimeError as error:
                self.errors.append(str(error))
                return
            self.stop.wait(self.interval)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=15)
        if self.errors or not self.samples:
            raise RuntimeError("WeKnora RSS sampling failed")


def assert_loopback_project(project):
    if not PILOT_PROJECT.fullmatch(project):
        raise ValueError("Compose projects must be explicitly named pilot-*")
    ids = subprocess.run([
        "docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}",
    ], text=True, capture_output=True, timeout=20, check=True).stdout.splitlines()
    if not ids:
        raise ValueError(f"disposable Compose project {project} has no running containers")
    for identifier in ids:
        info = inspect(identifier)
        if info["Config"]["Labels"].get("com.docker.compose.project") != project:
            raise ValueError("Docker project label mismatch")
        for mappings in info["NetworkSettings"]["Ports"].values():
            for mapping in mappings or []:
                if mapping["HostIp"] not in ("127.0.0.1", "::1"):
                    raise ValueError(f"{project} publishes a non-loopback port")


def require_direct_source_network(nc_info, wk_info, nc_port, wk_port, nc_project):
    nc_ports = nc_info["NetworkSettings"]["Ports"].get("80/tcp") or []
    wk_ports = wk_info["NetworkSettings"]["Ports"]
    if not any(int(item["HostPort"]) == nc_port for item in nc_ports):
        raise ValueError("Nextcloud origin does not map to the disposable container")
    if not any(int(item["HostPort"]) == wk_port for mappings in wk_ports.values()
               for item in mappings or []):
        raise ValueError("WeKnora origin does not map to the disposable container")
    network = nc_project + "_default"
    wk_project = wk_info["Config"]["Labels"].get("com.docker.compose.project")
    if (set(nc_info["NetworkSettings"]["Networks"]) != {network} or
            set(wk_info["NetworkSettings"]["Networks"]) != {network, wk_project + "_default"}):
        raise ValueError("pilot application has a non-disposable network attachment")
    nc_network = nc_info["NetworkSettings"]["Networks"].get(network)
    wk_network = wk_info["NetworkSettings"]["Networks"].get(network)
    if not nc_network or not wk_network or nc_network.get("NetworkID") != wk_network.get("NetworkID"):
        raise ValueError("disposable Nextcloud and WeKnora do not share the expected project network")
    if "nextcloud" not in (nc_network.get("Aliases") or []):
        raise ValueError("Nextcloud service alias is missing on the expected project network")
    # Ensure the trusted service alias cannot resolve another attached
    # container. It is safe only on this disposable project's own network.
    network_info = inspect_network(network)
    peers = list((network_info.get("Containers") or {}).keys())
    alias_owners = []
    for peer in peers:
        peer_info = inspect(peer)
        aliases = peer_info["NetworkSettings"]["Networks"].get(network, {}).get("Aliases") or []
        if "nextcloud" in aliases:
            alias_owners.append(peer_info["Id"])
    if alias_owners != [nc_info["Id"]]:
        raise ValueError("Nextcloud service alias is ambiguous")
    env = dict(item.split("=", 1) for item in wk_info["Config"]["Env"] if "=" in item)
    allowed = {item.strip() for item in env.get("WEKNORA_NEXTCLOUD_ALLOWED_ORIGINS", "").split(",")}
    if env.get("WEKNORA_NEXTCLOUD_DEV_HTTP") != "1" or "http://nextcloud" not in allowed:
        raise ValueError("WeKnora has not approved the isolated Nextcloud service origin")


def inspect_network(name):
    result = subprocess.run(["docker", "network", "inspect", name], text=True,
                            capture_output=True, timeout=20, check=True)
    rows = json.loads(result.stdout)
    if len(rows) != 1:
        raise ValueError("expected one disposable project network")
    return rows[0]


def payload(index, size):
    prefix = f"synthetic pilot file {index:06d}; no user data\n".encode()
    return (prefix * ((size + len(prefix) - 1) // len(prefix)))[:size]


def upload_synthetic_file(url, headers, body, timeout):
    outgoing = urllib.request.Request(url, data=body, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(outgoing, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def percentile_nearest_rank(values, percentile):
    if not values:
        raise ValueError("percentile needs at least one sample")
    return sorted(values)[math.ceil(percentile * len(values)) - 1]


def sync_row(db_container, source_id, sync_id):
    query = ("SELECT jsonb_build_object('status', status, 'duration_seconds', "
             "EXTRACT(EPOCH FROM finished_at-started_at), 'items_total', items_total, "
             "'items_created', items_created, 'items_updated', items_updated, "
             "'items_failed', items_failed) FROM sync_logs "
             f"WHERE id='{sync_id}' AND data_source_id='{source_id}'")
    return docker_sql(db_container, query)


def outbox_event_id(db_container, binding_id, source_file_id):
    if not re.fullmatch(r"pilot-load-[0-9a-f]{16}", binding_id) or source_file_id <= 0:
        raise ValueError("invalid pilot source identity")
    return docker_sql(db_container,
        "SELECT to_jsonb(COALESCE(MAX(id), 0)) FROM oc_weknora_outbox "
        f"WHERE binding_id='{binding_id}' AND file_id={source_file_id} AND event_type='upsert'")


def outbox_watermark(db_container, binding_id):
    if not re.fullmatch(r"pilot-load-[0-9a-f]{16}", binding_id):
        raise ValueError("invalid pilot binding identity")
    return docker_sql(db_container,
        "SELECT to_jsonb(COALESCE(MAX(id), 0)) FROM oc_weknora_outbox "
        f"WHERE binding_id='{binding_id}'")


def wait_published_versions(db_container, source_id, minimum, timeout):
    deadline = time.monotonic() + timeout
    while True:
        published = docker_sql(db_container,
            "SELECT to_jsonb(COUNT(*)) FROM nextcloud_source_versions "
            f"WHERE datasource_id='{source_id}' AND state='published'")
        if published >= minimum:
            return
        failed = docker_sql(db_container,
            "SELECT to_jsonb(COUNT(*)) FROM nextcloud_source_versions v "
            "JOIN knowledges k ON k.id=v.candidate_knowledge_id "
            f"WHERE v.datasource_id='{source_id}' AND k.parse_status='failed'")
        if failed:
            raise RuntimeError("pilot candidate parsing failed before latency sampling")
        if time.monotonic() >= deadline:
            raise RuntimeError("pilot candidates did not publish before latency sampling")
        time.sleep(1)


def wait_sync(db_container, source_id, sync_id, timeout):
    deadline = time.monotonic() + timeout
    while True:
        row = sync_row(db_container, source_id, sync_id)
        if row["status"] in ("success", "failed", "partial", "canceled"):
            if row["status"] != "success" or row["items_failed"]:
                raise RuntimeError(f"bulk sync did not succeed: {row['status']}")
            if not isinstance(row["duration_seconds"], (float, int)) or row["duration_seconds"] <= 0:
                raise RuntimeError("bulk sync has no positive measured duration")
            return row
        if time.monotonic() >= deadline:
            raise RuntimeError("bulk sync timed out")
        time.sleep(0.5)


def wait_checkpoint(base, token, path, field, event_id, timeout):
    deadline = time.monotonic() + timeout
    while True:
        status, row = wk_request(base, token, "GET", path)
        require_status(status, 200, f"read {field}")
        observed = decimal_status_id(row, field)
        if observed >= event_id:
            return time.monotonic(), row
        if row.get("dispatch_state") == "blocked":
            raise RuntimeError("event dispatch became blocked")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"{field} did not reach event {event_id}")
        time.sleep(0.5)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nextcloud-origin", required=True)
    parser.add_argument("--weknora-origin", required=True)
    parser.add_argument("--nextcloud-env-file", required=True, type=Path)
    parser.add_argument("--weknora-admin-env-file", required=True, type=Path)
    parser.add_argument("--nextcloud-compose-project", required=True)
    parser.add_argument("--weknora-compose-project", required=True)
    parser.add_argument("--nextcloud-compose-directory", required=True, type=Path)
    parser.add_argument("--weknora-app-service", default="app")
    parser.add_argument("--weknora-db-service", default="pg")
    parser.add_argument("--embedding-model-id", required=True)
    parser.add_argument("--file-count", type=int, default=8)
    parser.add_argument("--file-bytes", type=int, default=1024)
    parser.add_argument("--event-samples", type=int, default=20)
    parser.add_argument("--sync-timeout", type=int, default=600)
    parser.add_argument("--event-timeout", type=int, default=600)
    parser.add_argument("--upload-timeout", type=int, default=120)
    parser.add_argument("--rss-interval", type=float, default=0.5)
    parser.add_argument("--pilot-10k-100gb", action="store_true",
                        help="opt into exactly 10,000 synthetic files x 10,000,000 bytes = 100 GB")
    parser.add_argument("--allow-large-pilot", action="store_true",
                        help="opt into custom fixture above 100 files or 100 MiB total")
    parser.add_argument("--report", type=Path, help="optional JSON report path; contains no credentials")
    args = parser.parse_args()
    if args.pilot_10k_100gb:
        if args.file_count != 8 or args.file_bytes != 1024:
            parser.error("--pilot-10k-100gb cannot be combined with file count/size overrides")
        args.file_count, args.file_bytes = 10_000, 10_000_000
    if not 1 <= args.file_count <= 10_000 or not 1 <= args.file_bytes <= 64 * 1024 * 1024:
        parser.error("file count must be 1..10,000 and file bytes 1..64 MiB")
    if not args.pilot_10k_100gb and not args.allow_large_pilot and (
            args.file_count > 100 or args.file_count * args.file_bytes > 100 * 1024 * 1024):
        parser.error("larger custom fixture requires --allow-large-pilot")
    if not 2 <= args.event_samples <= 1000 or args.sync_timeout < 30 or args.event_timeout < 30:
        parser.error("need 2..1,000 event samples and timeouts of at least 30 seconds")
    if not 20 <= args.upload_timeout <= 3600:
        parser.error("--upload-timeout must be 20..3,600 seconds")
    if not 0.1 <= args.rss_interval <= 10:
        parser.error("--rss-interval must be 0.1..10 seconds")
    for project in (args.nextcloud_compose_project, args.weknora_compose_project):
        if not PILOT_PROJECT.fullmatch(project):
            parser.error("both Compose project names must start with pilot-")
    if args.nextcloud_compose_project == args.weknora_compose_project:
        parser.error("Compose projects must differ")
    for value in (args.weknora_app_service, args.weknora_db_service, args.embedding_model_id):
        if not SAFE_ID.fullmatch(value):
            parser.error("invalid service or model ID")
    try:
        origin(args.nextcloud_origin)
        origin(args.weknora_origin)
    except ValueError as error:
        parser.error(str(error))
    if not (args.nextcloud_compose_directory / "compose.yaml").is_file():
        parser.error("--nextcloud-compose-directory must contain compose.yaml")
    if not args.nextcloud_env_file.is_file() or not args.weknora_admin_env_file.is_file():
        parser.error("isolated credential env files are required")
    return args


def run(args):
    assert_loopback_project(args.nextcloud_compose_project)
    assert_loopback_project(args.weknora_compose_project)
    values = env_values(args.nextcloud_env_file)
    account = env_values(args.weknora_admin_env_file)
    owner, password = values.get("NEXTCLOUD_ADMIN_USER"), values.get("NEXTCLOUD_ADMIN_PASSWORD")
    email, wk_password = account.get("WEKNORA_TEST_ADMIN_EMAIL"), account.get("WEKNORA_TEST_ADMIN_PASSWORD")
    if not all((owner, password, email, wk_password)):
        raise ValueError("isolated administrator credentials are missing")
    os.environ.update({"WEKNORA_TEST_ADMIN_EMAIL": email,
                       "WEKNORA_TEST_ADMIN_PASSWORD": wk_password})
    nc_container = compose_container(args.nextcloud_compose_project, "nextcloud")
    nc_db_container = compose_container(args.nextcloud_compose_project, "db")
    wk_container = compose_container(args.weknora_compose_project, args.weknora_app_service)
    db_container = compose_container(args.weknora_compose_project, args.weknora_db_service)
    nc_image_id = inspect(nc_container)["Image"]
    wk_image_id = inspect(wk_container)["Image"]
    if inspect(db_container)["Config"]["Labels"].get("com.docker.compose.project") != args.weknora_compose_project:
        raise ValueError("PostgreSQL belongs to another project")
    expected_compose = str((args.nextcloud_compose_directory / "compose.yaml").resolve())
    actual_compose = inspect(nc_container)["Config"]["Labels"].get(
        "com.docker.compose.project.config_files", "")
    if expected_compose not in actual_compose.split(","):
        raise ValueError("Nextcloud container does not use the supplied disposable Compose file")
    require_direct_source_network(
        inspect(nc_container), inspect(wk_container),
        origin(args.nextcloud_origin).port, origin(args.weknora_origin).port,
        args.nextcloud_compose_project)
    nc_base, wk_base = args.nextcloud_origin, args.weknora_origin
    nc_api = nc_base + "/index.php/apps/integration_weknora/api/v1"
    admin, csrf = login(nc_base, owner, password)
    wk_token, tenant_id = wk_login(wk_base)
    suffix = secrets.token_hex(8)
    binding = "pilot-load-" + suffix
    pair_op = str(uuid.uuid4())
    folder = nc_base + "/remote.php/dav/files/" + urllib.parse.quote(owner, safe="") + "/" + binding
    basic = base64.b64encode(f"{owner}:{password}".encode()).decode()
    dav_headers = {"Authorization": "Basic " + basic}
    binding_url = nc_api + "/admin/bindings/" + binding
    source_path = None
    key_id = connection_id = None
    cloud_paired = source_paired = False
    stage = "create fixture"
    source_id = kb_id = None
    try:
        require_status(dav_request(folder, "MKCOL", dav_headers)[0], 201, "create pilot folder")
        root_id = file_id(folder, dav_headers)
        # Upload before publishing the new binding. Those files will appear
        # in its first complete manifest without creating a historical event
        # backlog that could distort event latency sampling.
        for index in range(args.file_count):
            target = folder + f"/pilot-{index:06d}.txt"
            status = upload_synthetic_file(target, dav_headers,
                                           payload(index, args.file_bytes), args.upload_timeout)
            require_status(status, 201, f"upload synthetic file {index}")
        require_status(nc_request(admin, csrf, nc_api + "/admin/bindings", "POST", {
            "id": binding, "name": binding, "owner_uid": owner, "root_file_id": root_id,
        })[0], 201, "create pilot binding")

        stage = "pair source"
        status, body = wk_request(wk_base, wk_token, "POST", "/api/v1/knowledge-bases", {
            "name": binding, "type": "document", "embedding_model_id": args.embedding_model_id,
        })
        require_status(status, 201, "create dedicated pilot KB")
        kb = body.get("data") if isinstance(body, dict) else None
        kb_id = kb.get("id") if isinstance(kb, dict) else None
        if not isinstance(kb_id, str) or str(kb.get("tenant_id")) != tenant_id:
            raise RuntimeError("pilot KB tenant mismatch")
        pair_url = binding_url + "/source-pairing"
        status, body = nc_request(admin, csrf, pair_url, "POST", {
            "operation_id": pair_op, "tenant_id": tenant_id, "knowledge_base_id": kb_id,
        })
        require_status(status, 201, "prepare pilot source")
        prepared = body.get("pairing") if isinstance(body, dict) else None
        machine_token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(prepared, dict) or prepared.get("operation_id") != pair_op or not machine_token:
            raise RuntimeError("pilot pair preparation mismatch")
        status, _ = wk_request(wk_base, wk_token, "POST", "/api/v1/datasource/nextcloud-source-pairings", {
            "knowledge_base_id": kb_id, "base_url": "http://nextcloud",
            "binding_id": binding, "operation_id": pair_op,
            "instance_id": prepared["instance_id"], "publication_epoch": prepared["publication_epoch"],
            "key_id": prepared["key_id"], "token": machine_token,
        })
        if status not in (200, 201, 202):
            raise RuntimeError(f"pilot pair HTTP {status}")
        del machine_token
        for _ in range(10):
            nc_status, nc_body = nc_request(admin, csrf, pair_url, "GET")
            wk_status, wk_body = wk_request(wk_base, wk_token, "GET",
                                           "/api/v1/datasource/nextcloud-source-pairings/" + pair_op)
            if (nc_status == wk_status == 200 and
                    nc_body.get("pairing", {}).get("state") == wk_body.get("pairing", {}).get("state") == "active"):
                break
            status, _ = wk_request(wk_base, wk_token, "POST",
                                   "/api/v1/datasource/nextcloud-source-pairings/" + pair_op + "/retry")
            if status not in (200, 202):
                raise RuntimeError(f"pilot pair retry HTTP {status}")
            time.sleep(0.5)
        else:
            raise RuntimeError("pilot source pair did not become active")
        source_id = str(uuid.UUID(wk_body["pairing"]["data_source_id"]))

        stage = "measure bulk sync"
        before_rss = process_rss_bytes(wk_container)
        with RssSampler(wk_container, args.rss_interval) as sampler:
            wall_start = time.monotonic()
            status, sync = wk_request(wk_base, wk_token, "POST", f"/api/v1/datasource/{source_id}/sync")
            require_status(status, 200, "start pilot bulk sync")
            sync_id = str(uuid.UUID(sync["id"]))
            sync_result = wait_sync(db_container, source_id, sync_id, args.sync_timeout)
            wall_seconds = time.monotonic() - wall_start
        if sync_result["items_created"] < args.file_count or sync_result["items_failed"]:
            raise RuntimeError("pilot bulk sync did not create all synthetic files")
        version_count = docker_sql(db_container,
            "SELECT to_jsonb(COUNT(*)) FROM nextcloud_source_versions "
            f"WHERE datasource_id='{source_id}' AND state IN ('staging','published')")
        if version_count < args.file_count:
            raise RuntimeError("pilot source version inventory is incomplete")
        logical_bytes = args.file_count * args.file_bytes
        bulk = {
            "files": args.file_count, "logical_payload_bytes": logical_bytes,
            "sync_duration_seconds": round(sync_result["duration_seconds"], 3),
            "wall_observation_seconds": round(wall_seconds, 3),
            "logical_payload_bytes_per_second": round(logical_bytes / sync_result["duration_seconds"], 1),
            "files_per_second": round(args.file_count / sync_result["duration_seconds"], 3),
            "items_total": sync_result["items_total"], "items_created": sync_result["items_created"],
            "source_version_count": version_count,
            "weknora_process_rss_before_bytes": before_rss,
            "weknora_process_rss_peak_bytes": max(sampler.samples),
            "weknora_process_rss_samples": len(sampler.samples),
        }
        wait_published_versions(db_container, source_id, args.file_count, args.sync_timeout)

        stage = "pair event connection"
        source_path = f"/api/v1/datasource/{source_id}/nextcloud-event-connection"
        status, credential = wk_request(wk_base, wk_token, "POST", source_path)
        require_status(status, 201, "create pilot event connection")
        source_paired = True
        connection_id, key_id = credential.get("connection_id"), credential.get("key_id")
        if not isinstance(connection_id, str) or not isinstance(key_id, str) or not credential.get("secret"):
            raise RuntimeError("pilot event credential malformed")
        credential["receiver_url"] = "http://app:8080" + RECEIVER
        status, _ = nc_request(admin, csrf, binding_url + "/event-connection", "POST", credential)
        require_status(status, 201, "configure pilot event sender")
        cloud_paired = True
        del credential

        # File creation before pairing persists hints in the source outbox.
        # Drain and apply that baseline before timing a new event, otherwise
        # a historical batch can inflate the first sample or be coalesced.
        baseline_event_id = outbox_watermark(nc_db_container, binding)
        if baseline_event_id:
            wait_checkpoint(wk_base, wk_token, source_path,
                            "dispatched_through_event_id", baseline_event_id,
                            args.event_timeout)
            wait_checkpoint(wk_base, wk_token, source_path,
                            "applied_through_event_id", baseline_event_id,
                            args.event_timeout)

        stage = "measure event queue latency"
        event_path = folder + "/latency-probe.txt"
        times_ms = []
        event_ids = []
        for index in range(args.event_samples):
            start = time.monotonic()
            status, _ = dav_request(event_path, "PUT", dav_headers,
                                    f"synthetic event sample {index:04d}\n".encode())
            require_status(status, 201 if index == 0 else 204, "write latency sample")
            event_id = outbox_event_id(nc_db_container, binding,
                                       file_id(event_path, dav_headers))
            if event_ids and event_id <= event_ids[-1]:
                raise RuntimeError("event IDs did not advance between samples")
            accepted, _ = wait_checkpoint(wk_base, wk_token, source_path,
                                          "dispatched_through_event_id", event_id,
                                          args.event_timeout)
            times_ms.append(round((accepted - start) * 1000, 1))
            event_ids.append(event_id)
            # One applied checkpoint between samples prevents burst coalescing
            # from counting a single durable job for multiple source events.
            wait_checkpoint(wk_base, wk_token, source_path,
                            "applied_through_event_id", event_id, args.event_timeout)
        report = {
            "schema_version": 1, "kind": "synthetic_disposable_pilot",
            "nextcloud_project": args.nextcloud_compose_project,
            "weknora_project": args.weknora_compose_project,
            "nextcloud_image_id": nc_image_id, "weknora_image_id": wk_image_id,
            "fixture": {"binding_id": binding, "knowledge_base_id": kb_id,
                        "data_source_id": source_id, "pair_operation_id": pair_op},
            "bulk_sync": bulk,
            "event_to_durable_job": {
                "samples": len(times_ms), "latency_ms": times_ms,
                "p50_ms": percentile_nearest_rank(times_ms, .50),
                "p95_ms": percentile_nearest_rank(times_ms, .95),
                "max_ms": max(times_ms), "event_ids": event_ids,
                "method": "host monotonic time before WebDAV PUT to first observed WeKnora dispatched watermark; upper bound includes PUT and status polling",
            },
            "limits": [
                "WeKnora process RSS includes unrelated application activity; it is not connector-only heap.",
                "Logical throughput uses fixture payload bytes divided by sync_log duration; it is not measured network bytes or completed indexing throughput.",
                "Event P95 is nearest-rank over synthetic sequential samples; it does not establish sustained production P95.",
            ],
        }
        return report
    finally:
        cleanup_errors = []
        if cloud_paired:
            try:
                status, row = nc_request(admin, csrf, binding_url + "/event-connection", "GET")
                if status != 200 or row.get("connection_id") != connection_id or row.get("key_id") != key_id:
                    raise RuntimeError("pilot Nextcloud event connection identity changed")
                require_status(nc_request(admin, csrf, binding_url + "/event-connection", "DELETE")[0],
                               200, "revoke pilot Nextcloud sender")
            except Exception as error:
                cleanup_errors.append(error)
        if source_paired:
            try:
                status, row = wk_request(wk_base, wk_token, "GET", source_path)
                if status != 200 or row.get("connection_id") != connection_id or row.get("key_id") != key_id:
                    raise RuntimeError("pilot WeKnora event connection identity changed")
                require_status(wk_no_content_request(wk_base, "DELETE", source_path,
                                                     token=wk_token)[0],
                               204, "revoke pilot WeKnora receiver")
            except Exception as error:
                cleanup_errors.append(error)
        if source_id is not None:
            print(f"pilot fixture retained for disposable-stack inspection: binding={binding} source={source_id} stage={stage}",
                  file=sys.stderr)
        if cleanup_errors:
            if sys.exc_info()[0] is None:
                raise RuntimeError("pilot event credential cleanup failed") from cleanup_errors[0]
            print("pilot event credential cleanup incomplete; inspect the disposable stack", file=sys.stderr)


def main():
    args = parse_args()
    if args.report and args.report.resolve().exists():
        raise RuntimeError("report path already exists; refusing to overwrite")
    report = run(args)
    encoded = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.report:
        target = args.report.resolve()
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
