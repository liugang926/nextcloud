#!/usr/bin/env python3
"""Own a loopback-only Nextcloud original-volume pilot; never start WeKnora.

The full preset is exactly 20 local users, two read-only department group
shares and 10,000 synthetic binary files totaling 100,000,000,000 bytes.
The default is a small, 20-file verification. Credentials stay in a new
private state directory. Removal checks every Docker resource's owner label.
"""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import http.client
import http.cookiejar
import json
import math
import os
from pathlib import Path
import random
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/integration_weknora/tests"))
from publication_http_smoke import issue_machine_key, request  # noqa: E402

MARKER = "nextcloud-original-volume-pilot-v1"
OWNER_LABEL = "org.nextcloud.weknora.original-pilot.owner"
PROJECT_RE = re.compile(r"pilot-original-[0-9a-f]{12}\Z")
SERVICES = {"nextcloud", "db", "redis"}
VOLUMES = {"nextcloud-html", "postgres-data", "redis-data"}
GIB = 1024 ** 3
RESERVE_BYTES = 8 * GIB
CHUNK_BYTES = 64 * 1024
IMAGES = {
    "nextcloud": "nextcloud:34.0.4-apache@sha256:a5ace30c695afe48c2c406e940ee7886a81e13fa382e57cd68b2416d1a66914c",
    "db": "postgres:16-alpine@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416",
    "redis": "redis:7-alpine@sha256:8b81dd37ff027bec4e516d41acfbe9fe2460070dc6d4a4570a2ac5b9d59df065",
}
LIMITS = [
    "Synthetic binary originals and their Nextcloud metadata were measured; WeKnora parsing, embedding, retrieval and physical GC were not exercised.",
    "Twenty local accounts and two normal-folder group shares do not prove real AD, Team Folder or complex ACL acceptance.",
    "One publisher owns both department folders, consistent with the app's verified owner model; twenty local staff accounts do not establish twenty concurrent uploads or questions.",
    "WebDAV PUT latency includes synthetic byte generation and HTTP transfer; it is not event-to-job or event-to-ready latency and has no baseline comparison.",
    "Stored logical byte counts are checked independently against filecache and regular files. Allocated filesystem bytes can differ from logical original bytes.",
]


def command(*args, timeout=60, env=None):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, env=env)
    if result.returncode:
        # Docker/HTTP diagnostics may contain environment credentials or bodies.
        raise RuntimeError(f"{args[0]} command failed with exit {result.returncode}")
    return result.stdout.strip()


def private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")


def replace_report(directory, value):
    path = directory / "report.json"
    if path.is_symlink():
        raise RuntimeError("refusing to replace a symlinked pilot report")
    temporary = directory / (".report-" + secrets.token_hex(8) + ".json")
    try:
        private_json(temporary, value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def bind_source_matches(source, expected):
    # Docker Desktop reports its exact host path through this VM prefix.
    approved = {expected}
    if sys.platform == "darwin":
        approved.add("/host_mnt" + expected)
    return source in approved


def payload_chunks(index, size):
    """Deterministic nonzero synthetic data; at most 64 KiB per chunk."""
    generator = random.Random("nextcloud-original-pilot-v1:" + str(index))
    remaining = size
    while remaining:
        length = min(CHUNK_BYTES, remaining)
        yield generator.randbytes(length)
        remaining -= length


def upload(port, path, headers, index, size, timeout=120):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    digest, sent = hashlib.sha256(), 0
    started = time.monotonic()
    try:
        connection.putrequest("PUT", path)
        for key, value in headers.items():
            connection.putheader(key, value)
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(size))
        connection.endheaders()
        for chunk in payload_chunks(index, size):
            connection.send(chunk)
            digest.update(chunk)
            sent += len(chunk)
        response = connection.getresponse()
        # Bound diagnostics; no response body enters the evidence.
        response.read(1024)
        if response.status != 201 or sent != size:
            raise RuntimeError(f"new original PUT returned HTTP {response.status}")
        return {"index": index, "bytes": sent, "sha256": digest.hexdigest(),
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3), "http_status": 201}
    finally:
        connection.close()


def required_free_bytes(remaining):
    # 20% overhead plus an 8-GiB reserve, with no override that bypasses it.
    return (remaining * 6 + 4) // 5 + RESERVE_BYTES


def host_capacity(directory, remaining):
    paths = [directory]
    docker_raw = Path.home() / "Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw"
    if docker_raw.exists() and docker_raw.parent.stat().st_dev != directory.stat().st_dev:
        paths.append(docker_raw.parent)
    free = [shutil.disk_usage(path).free for path in paths]
    required = required_free_bytes(remaining)
    if min(free) < required:
        raise RuntimeError("host capacity is below original bytes plus 20% overhead and 8-GiB reserve")
    return {"free_bytes_min": min(free), "required_free_bytes": required,
            "filesystems_checked": len(paths)}


def docker_names(kind, project=None):
    prefixes = {"container": ("docker", "ps", "-a"),
                "volume": ("docker", "volume", "ls"), "network": ("docker", "network", "ls")}
    template = "{{.Names}}" if kind == "container" else "{{.Name}}"
    args = list(prefixes[kind])
    if project:
        args += ["--filter", "label=com.docker.compose.project=" + project]
    return set(command(*args, "--format", template).splitlines())


def inspect(kind, name):
    rows = json.loads(command("docker", kind, "inspect", name))
    if len(rows) != 1:
        raise RuntimeError("Docker resource inspection was ambiguous")
    return rows[0]


def assert_owned_resources(state, *, empty=False):
    """All checks finish before any lifecycle mutation; names alone never own data."""
    project = state["project"]
    resources = {kind: docker_names(kind, project) for kind in ("container", "volume", "network")}
    for kind, delimiter in (("container", "-"), ("volume", "_"), ("network", "_")):
        occupied = {name for name in docker_names(kind) if name.startswith(project + delimiter)}
        if occupied - resources[kind]:
            raise RuntimeError("unlabeled resource occupies the pilot project name")
    if empty:
        if any(resources.values()):
            raise RuntimeError("pilot project already contains resources")
        return resources
    if not resources["volume"] <= {project + "_" + item for item in VOLUMES}:
        raise RuntimeError("unexpected pilot volume")
    if not resources["network"] <= {project + "_default"}:
        raise RuntimeError("unexpected pilot network")
    for kind, names in resources.items():
        for name in names:
            item = inspect(kind, name)
            labels = ((item.get("Config") or {}).get("Labels") if kind == "container"
                      else item.get("Labels")) or {}
            if (labels.get("com.docker.compose.project") != project or
                    labels.get(OWNER_LABEL) != state["owner_token"]):
                raise RuntimeError("Docker resource lacks this pilot's exact ownership label")
            if kind == "container":
                service = labels.get("com.docker.compose.service")
                if (service not in SERVICES or name != project + "-" + service + "-1" or
                        item.get("Image") != state["image_ids"][service]):
                    raise RuntimeError("pilot container service or image pin changed")
                networks = item.get("NetworkSettings", {}).get("Networks") or {}
                if (set(networks) - {project + "_default"} or
                        (item.get("State", {}).get("Running") and set(networks) != {project + "_default"})):
                    raise RuntimeError("pilot container is attached to another network")
                for mappings in (item.get("NetworkSettings", {}).get("Ports") or {}).values():
                    for mapping in mappings or []:
                        if (service != "nextcloud" or mapping.get("HostIp") != "127.0.0.1" or
                                mapping.get("HostPort") != str(state["port"])):
                            raise RuntimeError("pilot listener is not its approved loopback endpoint")
                for mount in item.get("Mounts", []):
                    if mount.get("Type") == "volume":
                        if mount.get("Name") not in {project + "_" + value for value in VOLUMES}:
                            raise RuntimeError("pilot container mounts a foreign volume")
                    elif (mount.get("Type") != "bind" or mount.get("RW") is not False or
                          not bind_source_matches(mount.get("Source"), state["app_path"]) or
                          mount.get("Destination") != "/var/www/html/custom_apps/integration_weknora"):
                        raise RuntimeError("pilot container mounts an unapproved host path")
            elif kind == "volume":
                if labels.get("com.docker.compose.volume") != name[len(project) + 1:]:
                    raise RuntimeError("pilot volume label differs from name")
            elif labels.get("com.docker.compose.network") != "default":
                raise RuntimeError("pilot network label differs from name")
    if resources["network"]:
        peers = inspect("network", project + "_default").get("Containers") or {}
        owned_ids = {inspect("container", name)["Id"] for name in resources["container"]}
        if not set(peers) <= owned_ids:
            raise RuntimeError("foreign container is attached to pilot network")
    return resources


def compose_data(state, passwords):
    labels = {OWNER_LABEL: state["owner_token"]}
    return {
        "services": {
            "db": {"image": IMAGES["db"], "labels": labels, "mem_limit": "768m",
                   "environment": {"POSTGRES_DB": "nextcloud", "POSTGRES_USER": "nextcloud",
                                   "POSTGRES_PASSWORD": passwords["database"]},
                   "volumes": ["postgres-data:/var/lib/postgresql/data"],
                   "healthcheck": {"test": ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U nextcloud -d nextcloud"],
                                   "interval": "5s", "timeout": "5s", "retries": 40}},
            "redis": {"image": IMAGES["redis"], "labels": labels, "mem_limit": "256m",
                      "command": ["redis-server", "--appendonly", "yes"],
                      "volumes": ["redis-data:/data"]},
            "nextcloud": {"image": IMAGES["nextcloud"], "labels": labels, "mem_limit": "2g",
                          "ports": [{"target": 80, "published": state["port"], "host_ip": "127.0.0.1"}],
                          "environment": {"POSTGRES_HOST": "db", "POSTGRES_DB": "nextcloud",
                              "POSTGRES_USER": "nextcloud", "POSTGRES_PASSWORD": passwords["database"],
                              "NEXTCLOUD_ADMIN_USER": "pilotadmin", "NEXTCLOUD_ADMIN_PASSWORD": passwords["admin"],
                              "NEXTCLOUD_TRUSTED_DOMAINS": "localhost 127.0.0.1", "REDIS_HOST": "redis",
                              "WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY": "1", "WEKNORA_LOCAL_COMPOSE": "1",
                              "WEKNORA_EVENT_DEV_HTTP": "1",
                              "PHP_MEMORY_LIMIT": "512M",
                              "PHP_UPLOAD_LIMIT": "64M"},
                          "volumes": ["nextcloud-html:/var/www/html",
                              state["app_path"] + ":/var/www/html/custom_apps/integration_weknora:ro"],
                          "depends_on": {"db": {"condition": "service_healthy"},
                                         "redis": {"condition": "service_started"}}},
        },
        "volumes": {name: {"labels": labels} for name in sorted(VOLUMES)},
        "networks": {"default": {"labels": labels}},
    }


def load_state(directory):
    directory = directory.resolve()
    path = directory / "state.json"
    if path.is_symlink() or not path.is_file() or directory.stat().st_mode & 0o077:
        raise ValueError("private pilot state directory is required")
    state = json.loads(path.read_text())
    if (state.get("marker") != MARKER or state.get("directory") != str(directory) or
            not PROJECT_RE.fullmatch(state.get("project", "")) or
            not re.fullmatch(r"[0-9a-f]{32}", state.get("owner_token", "")) or
            not isinstance(state.get("port"), int) or not 1024 <= state["port"] <= 65535 or
            set(state.get("image_ids", {})) != SERVICES or
            any(not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in state["image_ids"].values())):
        raise ValueError("pilot state identity is invalid")
    compose = directory / "compose.json"
    if compose.is_symlink() or fingerprint(json.loads(compose.read_text())) != state.get("compose_sha256"):
        raise ValueError("pilot compose file changed")
    return state


def prepare(directory, count, size, workers, full, upload_impact_samples=0):
    if directory.exists() or directory.is_symlink():
        raise ValueError("state directory must not exist")
    directory = directory.resolve()
    before = host_capacity(directory.parent, count * size)
    image_ids = {service: inspect("image", image)["Id"] for service, image in IMAGES.items()}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    state = {"marker": MARKER, "project": "pilot-original-" + secrets.token_hex(6),
             "owner_token": secrets.token_hex(16), "directory": str(directory), "port": port,
             "app_path": str((ROOT / "apps/integration_weknora").resolve()), "image_ids": image_ids,
             "file_count": count, "file_bytes": size, "workers": workers, "full_preset": full,
             "upload_impact_samples": upload_impact_samples,
             "host_capacity_before": before, "source_revision": command("git", "-C", str(ROOT), "rev-parse", "HEAD")}
    assert_owned_resources(state, empty=True)
    # The image passes the administrator password to Symfony Console as an
    # option value. A leading '-' can be parsed as another option there.
    passwords = {"admin": "x" + secrets.token_urlsafe(30), "database": "x" + secrets.token_urlsafe(30),
                 **{f"pilot-user-{index:02d}": "x" + secrets.token_urlsafe(30) for index in range(1, 21)}}
    passwords["pilotadmin"] = passwords["admin"]
    config = compose_data(state, passwords)
    state["compose_sha256"] = fingerprint(config)
    directory.mkdir(mode=0o700)
    private_json(directory / "compose.json", config)
    private_json(directory / "credentials.json", passwords)
    private_json(directory / "state.json", state)
    return state


def compose(state, *args, timeout=900):
    return command("docker", "compose", "-p", state["project"], "-f",
                   str(Path(state["directory"]) / "compose.json"), *args, timeout=timeout)


def occ(state, *args, password=None):
    environment = None
    if password:
        environment = dict(os.environ, NC_PASS=password)
    return command("docker", "exec", "--user", "www-data",
                   *( ["-e", "NC_PASS"] if password else []),
                   state["project"] + "-nextcloud-1", "php", "/var/www/html/occ", *args,
                   timeout=120, env=environment)


def vm_capacity(state, remaining):
    free = int(command("docker", "exec", state["project"] + "-nextcloud-1", "php", "-r",
                       'echo (int)disk_free_space("/var/www/html/data");'))
    required = required_free_bytes(remaining)
    if free < required:
        raise RuntimeError("Docker data filesystem has insufficient pilot headroom")
    return {"free_bytes": free, "required_free_bytes": required}


def api_json(opener, url, method="GET", headers=None, value=None):
    outgoing = None if value is None else json.dumps(value).encode()
    status, body = request(opener, url, method,
                           {**(headers or {}), "Content-Type": "application/json"}, outgoing)
    try:
        return status, json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("HTTP API returned a non-JSON response") from None


def local_login(base, user, password):
    # Existing smoke helpers inherit a system proxy. This isolated fixture
    # must use only its inspected loopback mapping, even on proxied hosts.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    status, body = request(opener, base + "/index.php/login")
    expect(status == 200, f"local login page returned HTTP {status}")
    token = re.search(rb'data-requesttoken="([^"]+)"', body)
    expect(token is not None, "local login page lacks a CSRF token")
    form = urllib.parse.urlencode({"requesttoken": token.group(1).decode(), "user": user, "password": password}).encode()
    status, _ = request(opener, base + "/index.php/login", "POST", {"Origin": base}, form)
    expect(status == 200, f"local login submitted HTTP {status}")
    status, body = request(opener, base + "/index.php/apps/dashboard/")
    expect(status == 200, f"local authenticated dashboard returned HTTP {status}")
    token = re.search(rb'data-requesttoken="([^"]+)"', body)
    expect(token is not None, "local authenticated dashboard lacks a CSRF token")
    return opener, token.group(1).decode()


def local_file_id(url, headers):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    body = (b'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
            b'<d:prop><oc:fileid/></d:prop></d:propfind>')
    status, result = request(opener, url, "PROPFIND", {**headers, "Depth": "0", "Content-Type": "application/xml"}, body)
    expect(status == 207, f"local folder file-ID lookup returned HTTP {status}")
    value = ET.fromstring(result).find(".//{http://owncloud.org/ns}fileid")
    expect(value is not None and value.text and value.text.isdigit(), "local folder file-ID is missing")
    return int(value.text)


def expect(condition, message):
    if not condition:
        raise RuntimeError(message)


def dav_headers(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def percentile(values, fraction):
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def measure_upload_impact(state, passwords):
    """Matched small PUT samples in an unbound folder; remove them before corpus."""
    samples = state.get("upload_impact_samples", 0)
    if not samples:
        return {"result": "not_requested"}
    # Comparison objects coexist until the two phases finish; include them
    # as well as the forthcoming corpus in both storage preflights.
    bytes_needed = (state["file_count"] + 2 * samples) * state["file_bytes"]
    host_capacity(Path(state["directory"]), bytes_needed)
    vm_capacity(state, bytes_needed)
    base = f"http://127.0.0.1:{state['port']}"
    path = "/remote.php/dav/files/pilotadmin/upload-impact"
    auth = dav_headers("pilotadmin", passwords["admin"])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    status, _ = request(opener, base + path, "MKCOL", auth)
    expect(status == 201, "upload comparison folder failed")
    results = {}
    try:
        for phase in ("disabled", "enabled"):
            occ(state, "app:disable" if phase == "disabled" else "app:enable", "integration_weknora")
            def put(index):
                return upload(state["port"], path + f"/{phase}-{index:04d}.bin", auth,
                              index, state["file_bytes"])
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=state["workers"]) as pool:
                rows = list(pool.map(put, range(samples)))
            times = [row["elapsed_ms"] for row in rows]
            results[phase] = {"successful_new_puts": len(rows), "put_p50_ms": percentile(times, .5),
                              "put_p95_ms": percentile(times, .95), "put_max_ms": max(times),
                              "wall_seconds": round(time.monotonic() - started, 3)}
    finally:
        occ(state, "app:enable", "integration_weknora")
        status, _ = request(opener, base + path, "DELETE", auth)
        expect(status == 204, "upload comparison folder removal failed")
        occ(state, "trashbin:cleanup", "pilotadmin")
    results.update({"samples_per_phase": samples, "file_bytes": state["file_bytes"],
                    "workers": state["workers"], "order": ["disabled", "enabled"],
                    "temporary_comparison_folder_deleted_before_corpus": True,
                    "p95_increase_percent": round((results["enabled"]["put_p95_ms"] /
                        results["disabled"]["put_p95_ms"] - 1) * 100, 3),
                    "limits": ["Two matched synthetic phases, disabled before enabled; cache and time-order effects remain.",
                               "This unbound folder samples portal PUT overhead, without a signed event sender or WeKnora parser load.",
                               "A pilot observation does not prove sustained upload P95 or every content type."]})
    return results


def create_departments(state, passwords, base, admin, csrf):
    # Never change another stack's users: this whole stack was just created.
    occ(state, "config:system:set", "skeletondirectory", "--value=/var/www/html/empty-pilot-skeleton")
    occ(state, "app:enable", "integration_weknora")
    departments = []
    api = base + "/index.php/apps/integration_weknora/api/v1"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for number in range(2):
        group = f"pilot-department-{number + 1}"
        members = [f"pilot-user-{index:02d}" for index in range(number * 10 + 1, number * 10 + 11)]
        # The current application deliberately rejects multiple binding
        # owners until its cross-owner mount model is verified. Both scopes
        # use the isolated publisher, while group access remains disjoint.
        owner, folder = "pilotadmin", group
        occ(state, "group:add", group)
        for user in members:
            occ(state, "user:add", "--password-from-env", "--no-interaction", user, password=passwords[user])
            occ(state, "group:adduser", group, user)
        owner_headers = dav_headers(owner, passwords[owner])
        folder_url = base + "/remote.php/dav/files/" + owner + "/" + folder
        status, _ = request(opener, folder_url, "MKCOL", owner_headers)
        expect(status == 201, "department folder creation failed")
        root = local_file_id(folder_url, owner_headers)
        owner_session, token = local_login(base, owner, passwords[owner])
        form = urllib.parse.urlencode({"path": folder, "shareType": 1, "shareWith": group, "permissions": 1}).encode()
        status, body = request(owner_session, base + "/ocs/v2.php/apps/files_sharing/api/v1/shares", "POST",
                               {"requesttoken": token, "OCS-APIREQUEST": "true", "Accept": "application/json",
                                "Content-Type": "application/x-www-form-urlencoded"}, form)
        expect(status == 200 and json.loads(body)["ocs"]["meta"]["statuscode"] == 200,
               "department read-only group share failed")
        binding = group
        status, _ = api_json(admin, api + "/admin/bindings", "POST", {"requesttoken": csrf},
                             {"id": binding, "name": binding, "owner_uid": owner, "root_file_id": root})
        expect(status == 201, "department publication binding failed")
        key, _ = issue_machine_key(admin, api, binding, csrf)
        identities = {}
        for user in members:
            identity = {"directory_id": "original-pilot", "object_guid": secrets_uuid(), "nextcloud_uid": user}
            status, _ = api_json(admin, api + "/admin/identities", "POST", {"requesttoken": csrf}, identity)
            expect(status == 201, "local synthetic identity mapping failed")
            identities[user] = identity
        departments.append({"group": group, "members": members, "owner": owner, "folder": folder,
                            "root_id": root, "binding": binding, "key": key, "identities": identities})
    return departments


def secrets_uuid():
    import uuid
    return str(uuid.uuid4())


def upload_corpus(state, passwords, departments):
    count, size = state["file_count"], state["file_bytes"]
    rows, capacity = [], []
    started = time.monotonic()
    batch_size = 100
    def put(index):
        department = departments[index % 2]
        owner = department["owner"]
        path = f"/remote.php/dav/files/{owner}/{department['folder']}/original-{index:06d}.bin"
        return upload(state["port"], path, dav_headers(owner, passwords[owner]), index, size)
    with ThreadPoolExecutor(max_workers=state["workers"]) as pool:
        for start in range(0, count, batch_size):
            remaining = (count - start) * size
            capacity.append({"completed_files": start,
                             "host": host_capacity(Path(state["directory"]), remaining),
                             "docker_data": vm_capacity(state, remaining)})
            rows.extend(pool.map(put, range(start, min(start + batch_size, count))))
            print(f"original pilot: {len(rows)}/{count} new files uploaded", flush=True)
    wall = time.monotonic() - started
    rows.sort(key=lambda row: row["index"])
    digest = hashlib.sha256()
    for row in rows:
        digest.update(f"{row['index']}:{row['bytes']}:{row['sha256']}\n".encode())
    sample_indices = {0, 1, count - 2, count - 1}
    samples = {row["index"]: row["sha256"] for row in rows if row["index"] in sample_indices}
    times = [row["elapsed_ms"] for row in rows]
    return {"successful_new_puts": len(rows), "put_status_counts": {"201": len(rows)},
            "logical_payload_bytes": sum(row["bytes"] for row in rows), "workers": state["workers"],
            "wall_seconds": round(wall, 3), "files_per_second": round(count / wall, 3),
            "logical_bytes_per_second": round(count * size / wall, 3),
            "put_p50_ms": percentile(times, .5), "put_p95_ms": percentile(times, .95),
            "put_max_ms": max(times), "generation_receipts_sha256": digest.hexdigest(),
            "capacity_samples": capacity}, samples


PHYSICAL_CHECK = r'''
$rows = []; $count = 0; $bytes = 0; $allocated = 0;
$samples = json_decode($argv[1], true, 512, JSON_THROW_ON_ERROR); $checked = 0;
$expectedCount = (int)$argv[2]; $expectedSize = (int)$argv[3];
foreach ([0, 1] as $department) {
    $owner = 'pilotadmin';
    $directory = '/var/www/html/data/' . $owner . '/files/pilot-department-' . ($department + 1);
    $n = 0; $b = 0;
    foreach (new DirectoryIterator($directory) as $file) {
        if ($file->isDot()) { continue; }
        if (!$file->isFile() || $file->isLink() || !preg_match('/^original-([0-9]{6})\.bin$/', $file->getFilename(), $match)) {
            throw new RuntimeException('unexpected pilot entry');
        }
        $index = (int)$match[1];
        if ($index % 2 !== $department || $index >= $expectedCount || $file->getSize() !== $expectedSize) {
            throw new RuntimeException('wrong original index, size or department');
        }
        $n++; $b += $file->getSize();
        $stat = stat($file->getPathname()); $allocated += $stat['blocks'] * 512;
        if (array_key_exists($index, $samples)) {
            if (!hash_equals($samples[$index], hash_file('sha256', $file->getPathname()))) {
                throw new RuntimeException('sample original digest mismatch');
            }
            $checked++;
        }
    }
    $rows[] = ['department' => $department + 1, 'regular_file_count' => $n, 'logical_bytes' => $b];
    $count += $n; $bytes += $b;
}
if ($checked !== count($samples)) { throw new RuntimeException('missing sample original'); }
echo json_encode(['regular_file_count' => $count, 'logical_bytes' => $bytes,
    'allocated_bytes' => $allocated, 'departments' => $rows, 'sample_sha256_matches' => $checked]);
'''


def verify_inventory(state, departments, samples):
    physical = json.loads(command("docker", "exec", state["project"] + "-nextcloud-1", "php", "-r",
                                  PHYSICAL_CHECK, json.dumps(samples), str(state["file_count"]),
                                  str(state["file_bytes"]), timeout=180))
    count, size = state["file_count"], state["file_bytes"]
    expect(physical["regular_file_count"] == count and physical["logical_bytes"] == count * size,
           "physical original count or bytes differ from corpus")
    query = """SELECT json_build_object('file_count', count(*), 'logical_bytes', COALESCE(sum(size),0),
        'min_file_bytes', min(size), 'max_file_bytes', max(size)) FROM oc_filecache
        WHERE storage IN (SELECT numeric_id FROM oc_storages WHERE id IN
        ('home::pilotadmin'))
        AND path ~ '^files/pilot-department-[12]/original-[0-9]{6}\\.bin$'"""
    cached = json.loads(command("docker", "exec", state["project"] + "-db-1", "psql", "-X", "-U",
                                "nextcloud", "-d", "nextcloud", "-At", "-v", "ON_ERROR_STOP=1", "-c", query))
    expect(cached["file_count"] == count and cached["logical_bytes"] == count * size and
           cached["min_file_bytes"] == cached["max_file_bytes"] == size, "filecache corpus inventory mismatch")
    base = f"http://127.0.0.1:{state['port']}/index.php/apps/integration_weknora/api/v1"
    machine = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    inventories, all_ids = [], set()
    for department in departments:
        ids, total, times, cursor, generation, pages = set(), 0, [], None, None, 0
        while True:
            query = "" if cursor is None else "?" + urllib.parse.urlencode({"cursor": cursor})
            started = time.monotonic()
            status, page = api_json(machine, base + "/bindings/" + department["binding"] + "/manifest" + query,
                                    headers=department["key"])
            times.append(round((time.monotonic() - started) * 1000, 3))
            expect(status == 200 and isinstance(page.get("items"), list), "manifest page failed")
            expect(generation is None or generation == page["generation"], "manifest generation changed")
            generation = page["generation"]
            pages += 1
            expect(pages <= math.ceil(count / 2 / 200) + 1, "manifest pagination exceeded corpus bound")
            expect(0 < len(page["items"]) <= 200, "manifest returned an empty or overlarge page")
            for item in page["items"]:
                identifier = item["file_id"]
                expect(identifier not in ids and item["size"] == size, "manifest duplicate or incorrect bytes")
                ids.add(identifier)
                total += item["size"]
            if page["complete"]:
                expect(page["next_cursor"] is None, "complete manifest retained cursor")
                break
            expect(isinstance(page["next_cursor"], str) and page["next_cursor"] != cursor, "manifest cursor did not advance")
            cursor = page["next_cursor"]
        expect(len(ids) == count // 2 and total == count // 2 * size and not ids & all_ids,
               "department manifest corpus mismatch")
        all_ids.update(ids)
        department["sample_file_id"] = min(ids)
        inventories.append({"department": department["group"], "files": len(ids), "logical_bytes": total,
                            "pages": pages, "page_p95_ms": percentile(times, .95), "page_max_ms": max(times)})
    return {"physical_files": physical, "filecache": cached, "manifest": inventories,
            "manifest_unique_file_ids": len(all_ids)}


def verify_permissions(state, departments):
    base = f"http://127.0.0.1:{state['port']}"
    api = base + "/index.php/apps/integration_weknora/api/v1"
    machine = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    allowed = denied = scope_denied = 0
    for number, department in enumerate(departments):
        other = departments[1 - number]
        for user in department["members"]:
            identity = department["identities"][user]
            payload = {key: identity[key] for key in ("directory_id", "object_guid")}
            for target, permitted in ((department, True), (other, False)):
                status, result = api_json(machine, api + "/bindings/" + target["binding"] + "/authorize", "POST",
                                          target["key"], {**payload, "file_id": target["sample_file_id"]})
                expect(status == 200 and result.get("allow") is permitted, "department source permission mismatch")
                if permitted:
                    allowed += 1
                else:
                    denied += 1
        status, _ = api_json(machine, api + "/bindings/" + other["binding"] + "/manifest", headers=department["key"])
        expect(status in (401, 403), "department machine key accessed another binding")
        scope_denied += 1
    return {"local_users": 20, "department_groups": 2, "members_per_group": 10,
            "same_department_allows": allowed, "cross_department_denials": denied,
            "cross_binding_machine_key_denials": scope_denied, "group_share_permissions": 1}


def cleanup(state):
    resources = assert_owned_resources(state)
    # Exact names, validated owner labels and no image/cache pruning.
    for name in sorted(resources["container"]):
        command("docker", "rm", "--force", name)
    for name in sorted(resources["volume"]):
        command("docker", "volume", "rm", name, timeout=300)
    for name in sorted(resources["network"]):
        command("docker", "network", "rm", name)
    assert_owned_resources(state, empty=True)
    directory = Path(state["directory"])
    for name in ("credentials.json", "compose.json"):
        path = directory / name
        if path.is_symlink():
            raise RuntimeError("refusing to remove symlinked private state")
        path.unlink(missing_ok=True)
    return {"owned_containers_remaining": 0, "owned_volumes_remaining": 0,
            "owned_networks_remaining": 0, "private_credentials_removed": True,
            "host_free_after_cleanup_bytes": shutil.disk_usage(directory).free}


def execute(state, retain):
    directory = Path(state["directory"])
    report = {"schema_version": 1, "kind": MARKER, "source_revision": state["source_revision"],
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "project": state["project"],
              "fixture": {"local_staff_users": 20, "publisher_users": 1, "publisher": "pilotadmin",
                          "departments": 2, "original_files": state["file_count"],
                          "original_bytes_each": state["file_bytes"],
                          "original_bytes_total": state["file_count"] * state["file_bytes"],
                          "full_100gb_preset": state["full_preset"]},
              "runtime": {"image_ids": state["image_ids"], "host": "127.0.0.1", "port": state["port"]},
              "host_capacity_before": state["host_capacity_before"], "measurement_limits": LIMITS}
    success = False
    stage = "start isolated stack"
    try:
        assert_owned_resources(state, empty=True)
        compose(state, "up", "--detach", "--pull", "never", "db", "redis", "nextcloud")
        deadline = time.monotonic() + 600
        base = f"http://127.0.0.1:{state['port']}"
        readiness = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            try:
                status = json.loads(occ(state, "status", "--output=json"))
                if status.get("installed") and not status.get("maintenance") and not status.get("needsDbUpgrade"):
                    code, body = request(readiness, base + "/status.php")
                    served = json.loads(body)
                    if code == 200 and served.get("installed") and not served.get("maintenance"):
                        break
            except (RuntimeError, ValueError, OSError, http.client.HTTPException):
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("isolated Nextcloud startup timed out")
            time.sleep(2)
        assert_owned_resources(state)
        stage = "validate runtime capacity"
        report["runtime"]["nextcloud_version"] = status["versionstring"]
        report["docker_capacity_before"] = vm_capacity(state, state["file_count"] * state["file_bytes"])
        passwords = json.loads((directory / "credentials.json").read_text())
        occ(state, "config:system:set", "overwrite.cli.url", "--value=" + base)
        occ(state, "config:system:set", "htaccess.RewriteBase", "--value=/")
        occ(state, "maintenance:update:htaccess")
        stage = "administrator login"
        admin, csrf = local_login(base, "pilotadmin", passwords["admin"])
        stage = "optional upload comparison"
        report["upload_impact"] = measure_upload_impact(state, passwords)
        stage = "create departments and synthetic mappings"
        departments = create_departments(state, passwords, base, admin, csrf)
        report["runtime"]["integration_weknora_version"] = occ(state, "config:app:get", "integration_weknora", "installed_version")
        stage = "stream original corpus"
        report["upload"], samples = upload_corpus(state, passwords, departments)
        stage = "verify physical filecache and manifest inventories"
        report["inventory"] = verify_inventory(state, departments, samples)
        stage = "verify department source permissions"
        report["permissions"] = verify_permissions(state, departments)
        report["result"] = "passed"
        success = True
    except (Exception, KeyboardInterrupt) as error:
        report["result"] = "failed"
        report["failure_category"] = type(error).__name__
        if isinstance(error, AssertionError) and error.args:
            detail = error.args[0]
            if isinstance(detail, tuple) and detail and isinstance(detail[0], int):
                report["failure_http_status"] = detail[0]
        report["stage"] = stage
        location = error.__traceback__
        while location and location.tb_next:
            location = location.tb_next
        if location:
            report["failure_location"] = {"file": Path(location.tb_frame.f_code.co_filename).name,
                                          "line": location.tb_lineno}
        # Own messages have no private credentials or response bodies.
        if type(error) in (RuntimeError, ValueError):
            report["failure_stage"] = str(error)[:200]
        raise
    finally:
        try:
            if not (success and retain):
                report["cleanup"] = cleanup(state)
            else:
                report["cleanup"] = {"retained_for_inspection": True}
        except Exception as error:
            report["cleanup"] = {"result": "refused_or_failed", "category": type(error).__name__}
            report["result"] = "failed"
            raise
        finally:
            private_json(directory / "report.json", report)
            print(f"pilot evidence: {directory / 'report.json'}", flush=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run")
    run.add_argument("--state-dir", required=True, type=Path)
    run.add_argument("--file-count", type=int, default=20)
    run.add_argument("--file-bytes", type=int, default=1024)
    run.add_argument("--workers", type=int, default=2)
    run.add_argument("--pilot-10k-100gb", action="store_true")
    run.add_argument("--allow-large-pilot", action="store_true")
    run.add_argument("--retain-on-success", action="store_true")
    run.add_argument("--upload-impact-samples", type=int, default=0,
                     help="optional 20..100 matched PUTs each with app disabled/enabled, removed before corpus")
    destroy = sub.add_parser("cleanup")
    destroy.add_argument("--state-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.action == "run":
        if args.pilot_10k_100gb:
            if args.file_count != 20 or args.file_bytes != 1024:
                parser.error("full preset cannot be combined with count or size overrides")
            args.file_count, args.file_bytes = 10_000, 10_000_000
        if args.file_count < 2 or args.file_count > 10_000 or args.file_count % 2:
            parser.error("file count must be even, 2..10,000")
        if not 1 <= args.file_bytes <= 64 * 1024 ** 2 or not 1 <= args.workers <= 4:
            parser.error("file bytes must be 1..64 MiB and workers 1..4")
        if args.upload_impact_samples != 0 and not 20 <= args.upload_impact_samples <= 100:
            parser.error("upload comparison requires 0 or 20..100 samples per phase")
        if not args.pilot_10k_100gb and not args.allow_large_pilot and (
                args.file_count > 100 or args.file_count * args.file_bytes > 100 * 1024 ** 2):
            parser.error("large custom corpus requires --allow-large-pilot")
    return args


def main():
    args = parse_args()
    try:
        if args.action == "run":
            state = prepare(args.state_dir, args.file_count, args.file_bytes, args.workers,
                            args.pilot_10k_100gb, args.upload_impact_samples)
            execute(state, args.retain_on_success)
        else:
            state = load_state(args.state_dir)
            result = cleanup(state)
            report_path = args.state_dir / "report.json"
            if report_path.exists() and not report_path.is_symlink():
                report = json.loads(report_path.read_text())
                expect(report.get("kind") == MARKER and report.get("project") == state["project"],
                       "retained report differs from owned pilot")
                report["cleanup"] = result
                replace_report(args.state_dir, report)
            print("exact owned pilot resources removed; report retained")
    except KeyboardInterrupt:
        print("original pilot interrupted; owned cleanup and evidence finalized", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"original pilot failed: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
