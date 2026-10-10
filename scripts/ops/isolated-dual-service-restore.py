#!/usr/bin/env python3
"""Restore a real, disposable Nextcloud/WeKnora pair from a stale checkpoint.

This script creates its own direct-group synthetic LDAP fixture. It has no
option for selecting a running project, database, volume, or remote origin.
Only the generated nc-synldap-* Compose project can be destroyed and restored.
The backup contains test passwords and application keys; keep its directory
private. The final JSON report contains no credentials or document text.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/integration_weknora/tests"))
from publication_http_smoke import login, request  # noqa: E402

HERE = Path(__file__).resolve().parent
owner = runpy.run_path(str(HERE / "synthetic-ldap-fixture.py"))
e2e = runpy.run_path(str(HERE / "synthetic-ldap-e2e.py"))
matrix = runpy.run_path(str(HERE / "ad-permission-acceptance.py"))
pair = runpy.run_path(str(HERE / "local-source-pairing.py"))
index = runpy.run_path(str(HERE / "local-indexed-withdrawal-smoke.py"))

PYTHON_IMAGE = owner["PYTHON_IMAGE"]
VOLUMES = frozenset(("ldap-certs", "ldap-fixture", "nc-postgres", "nc-redis-data",
                     "nc-html", "wk-postgres", "wk-data", "docreader-tmp"))
SERVICES = frozenset(("openldap", "cert-init", "nc-db", "nc-redis", "nextcloud",
                      "wk-db", "wk-redis", "docreader", "mock-embedding", "wk-app"))
CONTROL_FILES = {"state.json": "fixture-state.json",
                 "compose.yaml": "fixture-compose.yaml",
                 "passwords.json": "fixture-passwords.json"}


def require(value, message):
    if not value:
        raise RuntimeError(message)


def run(command, *, input_file=None, output_file=None, timeout=180):
    with (open(input_file, "rb") if input_file else open(os.devnull, "rb")) as source:
        result = subprocess.run(command, stdin=source,
                                stdout=output_file or subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout,
                                check=False)
    if result.returncode:
        # Docker/HTTP error bodies may contain secrets or source metadata.
        raise RuntimeError(f"command failed at {command[0]} (exit {result.returncode})")
    return result.stdout if output_file is None else b""


def compose(directory, state, *args, timeout=600):
    return run(owner["compose_command"](directory, state, *args), timeout=timeout)


def inspect_json(kind, target):
    return json.loads(run(["docker", kind, "inspect", target], timeout=30))[0]


def docker_object_exists(kind, target):
    result = subprocess.run(["docker", kind, "inspect", target],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=30, check=False)
    return result.returncode == 0


def assert_project_unoccupied(state):
    """A fresh random suffix must not attach to any pre-existing Docker state."""
    project = state["project"]
    for kind, names in (
        ("container", [f"{project}-{service}-1" for service in SERVICES]),
        ("volume", [f"{project}_{role}" for role in VOLUMES]),
        ("network", [f"{project}_default"]),
    ):
        require(not any(docker_object_exists(kind, name) for name in names),
                "fresh synthetic project name is already occupied")
    for kind in ("ps", "volume", "network"):
        command = (["docker", "ps", "-aq"] if kind == "ps" else
                   ["docker", kind, "ls", "-q"])
        existing = run(command + ["--filter",
                                  f"label=com.docker.compose.project={project}"],
                       timeout=30).decode().split()
        require(not existing, "fresh synthetic project label is already occupied")


def owned_resources(directory, state, *, require_containers):
    """Check the marker, labels, service allowlist and every named volume."""
    checked_dir, checked = owner["owned_state"](directory)
    expected_state = {key: value for key, value in state.items()
                      if key != "_compose_sha256"}
    require(checked_dir == directory and checked == expected_state,
            "synthetic project ownership marker changed")
    project = state["project"]
    require(re.fullmatch(r"nc-synldap-[0-9a-f]{8}", project) is not None,
            "refusing non-disposable Compose project")
    require(sha256(directory / "compose.yaml") == state.get("_compose_sha256"),
            "generated Compose configuration changed after fixture creation")
    compose_data = json.loads((directory / "compose.yaml").read_text())
    require(compose_data.get("name") == project and
            set(compose_data.get("services", {})) == SERVICES and
            set(compose_data.get("volumes", {})) == VOLUMES,
            "synthetic Compose topology changed")
    image_id = run(["docker", "image", "inspect", state["weknora_image"],
                    "--format", "{{.Id}}"], timeout=30).decode().strip()
    require(image_id == state["weknora_image_id"],
            "WeKnora image tag changed since fixture creation")
    ids = run(["docker", "ps", "-aq", "--filter",
               f"label=com.docker.compose.project={project}"], timeout=30).decode().split()
    if require_containers:
        require(ids, "owned project has no containers")
    for container_id in ids:
        item = inspect_json("container", container_id)
        labels = item["Config"].get("Labels") or {}
        require(labels.get("com.docker.compose.project") == project and
                labels.get("com.docker.compose.service") in SERVICES,
                "unexpected container in owned project")
        for mapping in (item.get("HostConfig", {}).get("PortBindings") or {}).values():
            for endpoint in mapping or []:
                require(endpoint.get("HostIp") == "127.0.0.1",
                        "synthetic service has a non-loopback published port")
    names = run(["docker", "volume", "ls", "-q", "--filter",
                 f"label=com.docker.compose.project={project}"], timeout=30).decode().split()
    expected_names = {f"{project}_{name}" for name in VOLUMES}
    require(set(names).issubset(expected_names),
            "unexpected named volume in owned project")
    if require_containers:
        require(set(names) == expected_names,
                "synthetic project is missing a required named volume")
    for name in names:
        volume = inspect_json("volume", name)
        labels = volume.get("Labels") or {}
        role = name[len(project) + 1:]
        require(labels.get("com.docker.compose.project") == project and
                labels.get("com.docker.compose.volume") == role,
                "owned volume lost its exact Compose role label")
    return ids, names


def volume_name(state, role):
    require(role in VOLUMES, "unknown synthetic volume role")
    name = state["project"] + "_" + role
    info = inspect_json("volume", name)
    require((info.get("Labels") or {}).get("com.docker.compose.project") ==
            state["project"], "volume does not belong to synthetic project")
    return name


def archive_volume(state, role, destination):
    command = ["docker", "run", "--rm", "--pull", "never", "--network", "none",
               "--mount", f"type=volume,source={volume_name(state, role)},target=/payload,readonly",
               "--entrypoint", "tar", PYTHON_IMAGE, "-C", "/payload", "-cf", "-", "."]
    with destination.open("wb") as output:
        destination.chmod(0o600)
        run(command, output_file=output, timeout=300)


def restore_volume(state, role, source):
    command = ["docker", "run", "--rm", "-i", "--pull", "never", "--network", "none",
               "--mount", f"type=volume,source={volume_name(state, role)},target=/payload,volume-nocopy",
               "--entrypoint", "tar", PYTHON_IMAGE, "-C", "/payload", "-xf", "-"]
    run(command, input_file=source, timeout=300)


def postgres_volume_owner(state, role):
    require(role in {"nc-postgres", "wk-postgres"}, "not a PostgreSQL volume")
    value = run(["docker", "run", "--rm", "--pull", "never", "--network", "none",
                 "--mount", f"type=volume,source={volume_name(state, role)},target=/payload,readonly",
                 "--entrypoint", "stat", PYTHON_IMAGE, "-c", "%u:%g:%a", "/payload"],
                timeout=30).decode().strip()
    require(re.fullmatch(r"[0-9]+:[0-9]+:[0-7]+", value) is not None,
            "PostgreSQL volume owner could not be verified")
    return value


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_archive(path, expected):
    require(path.is_file() and sha256(path) == expected,
            "checkpoint archive failed SHA-256 verification")


def copy_private(source, destination):
    """Create a checkpoint copy with private permissions from the first byte."""
    with source.open("rb") as original:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as saved:
            shutil.copyfileobj(original, saved)


def restore_private(source, destination):
    """Replace one fixture control file atomically with its verified backup."""
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".restore-",
                                     delete=False) as saved:
        temporary = Path(saved.name)
        try:
            with source.open("rb") as original:
                shutil.copyfileobj(original, saved)
            saved.flush()
            os.fsync(saved.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(destination)


def fixture_weknora_keys(directory):
    """Require the restored Compose keys to match the backed-up fixture secrets."""
    passwords = json.loads((directory / "passwords.json").read_text())
    compose_data = json.loads((directory / "compose.yaml").read_text())
    require(isinstance(passwords, dict) and isinstance(compose_data, dict),
            "restored fixture key files have invalid structure")
    services = compose_data.get("services")
    require(isinstance(services, dict) and isinstance(services.get("wk-app"), dict),
            "restored WeKnora Compose service is missing")
    environment = services["wk-app"].get("environment")
    aes_key = passwords.get("aes")
    require(isinstance(environment, dict) and
            isinstance(passwords.get("jwt"), str) and passwords["jwt"] and
            isinstance(aes_key, str) and
            re.fullmatch(r"[0-9a-f]{32}", aes_key) is not None and
            environment.get("JWT_SECRET") == passwords["jwt"] and
            environment.get("SYSTEM_AES_KEY") == aes_key,
            "restored WeKnora JWT or encryption key differs from checkpoint")
    return passwords


def pg_dump(container, user, database, destination):
    with destination.open("wb") as output:
        destination.chmod(0o600)
        run(["docker", "exec", container, "pg_dump", "-U", user, "-Fc", "-d", database],
            output_file=output, timeout=300)
    run(["docker", "exec", "-i", container, "pg_restore", "--list"],
        input_file=destination, timeout=30)


def app_stopped(state, service):
    item = inspect_json("container", state["project"] + "-" + service + "-1")
    require(not item["State"]["Running"], f"{service} must remain stopped")


def wait_health(url, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _ = matrix["http"]("GET", url)
            if status == 200:
                return
        except (OSError, matrix["ProbeError"]):
            pass
        time.sleep(2)
    raise RuntimeError("restored application health check timed out")


def source_decision(nc_base, state, runtime):
    binding = urllib.parse.quote(runtime["binding_id"], safe="")
    url = (nc_base + "/index.php/apps/integration_weknora/api/v1/bindings/" +
           binding + "/authorize")
    body = json.dumps({"directory_id": state["directory_id"],
                       "object_guid": state["guids"]["alice"].lower(),
                       "file_id": runtime["file_id"]}, separators=(",", ":")).encode()
    headers = matrix["signed_headers"]("POST", url, {
        "Authorization": "Bearer " + runtime["token"],
        "X-WeKnora-Key-Id": runtime["key_id"],
        "Content-Type": "application/json",
    }, body)
    status, raw = matrix["http"]("POST", url, headers=headers, payload=body)
    require(status == 200, f"source authorization HTTP {status}")
    result = json.loads(raw)
    require(type(result.get("allow")) is bool,
            "source authorization returned no decision")
    return result


def old_token_still_valid(wk_base, token):
    status, body = e2e["http_json"](wk_base, "GET", "/api/v1/auth/me", token=token)
    user = body.get("data", {}).get("user", {}) if isinstance(body, dict) else {}
    return (status == 200 and body.get("success") is True and
            user.get("username") == "alice" and user.get("is_active") is True)


def version_state(state, runtime):
    source_id = runtime["source_id"]
    file_id = runtime["file_id"]
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", source_id) is not None and
            type(file_id) is int and file_id > 0, "invalid synthetic source identity")
    query = ("SELECT jsonb_build_object('state',v.state,'candidate',v.candidate_knowledge_id,"
             "'desired_etag',v.desired_etag,'visible',(SELECT COUNT(*) FROM knowledges k "
             "WHERE k.metadata->>'datasource_id'=v.datasource_id "
             "AND k.metadata->>'external_id'=v.external_id "
             "AND k.deleted_at IS NULL AND k.metadata->>'nextcloud_etag' <> '')) "
             "FROM nextcloud_source_versions v "
             f"WHERE v.datasource_id='{source_id}' AND v.external_id LIKE '%:{file_id}'")
    return index["sql_json"](state["project"] + "-wk-db-1", query)


def sync_to_terminal(wk_base, wk_token, state, runtime, *, attempts=2):
    source_id = runtime["source_id"]
    database = state["project"] + "-wk-db-1"
    records = []
    for _ in range(attempts):
        status, body = pair["wk_request"](
            wk_base, wk_token, "POST", f"/api/v1/datasource/{source_id}/sync")
        require(status == 200 and isinstance(body.get("id"), str),
                f"manual source sync returned HTTP {status}")
        sync_id = body["id"]
        require(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sync_id) is not None,
                "manual sync returned invalid ID")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                row = index["sql_json"](database,
                    "SELECT jsonb_build_object('status',status,'items_failed',items_failed) "
                    f"FROM sync_logs WHERE id='{sync_id}' AND data_source_id='{source_id}'")
            except RuntimeError:
                row = None
            if isinstance(row, dict) and row.get("status") in {
                    "success", "failed", "partial", "canceled"}:
                require(row["status"] == "success" and row["items_failed"] == 0,
                        "manual source sync did not complete successfully")
                records.append(sync_id)
                break
            time.sleep(1)
        else:
            raise RuntimeError("manual source sync timed out")
    row = version_state(state, runtime)
    require(row["state"] == "tombstone" and row["visible"] == 0,
            "two successful full scans did not tombstone the withdrawn file")
    return records, row


def private_json(path, data):
    with path.open("x", encoding="utf-8") as output:
        json.dump(data, output, indent=2, sort_keys=True)
        output.write("\n")
    path.chmod(0o600)


def drill(image, evidence):
    directory = None
    project = None
    stages = []
    try:
        prep = json.loads(run([sys.executable, str(HERE / "synthetic-ldap-fixture.py"),
                               "prepare", "--weknora-image", image,
                               "--mode", "direct"], timeout=60))
        directory = Path(prep["scratch"])
        directory, state = owner["owned_state"](directory)
        require(state["mode"] == "direct" and state["project"] == prep["project"],
                "fresh synthetic fixture identity changed")
        project = state["project"]
        state["_compose_sha256"] = sha256(directory / "compose.yaml")
        assert_project_unoccupied(state)
        try:
            compose(directory, state, "up", "-d", "--wait", "--wait-timeout", "600",
                    timeout=700)
        except RuntimeError:
            # A first Compose pass can race another local fixture's ephemeral
            # port release. Retry once only after checking our own labels and
            # topology; do not destroy or reinitialize an uncertain project.
            owned_resources(directory, state, require_containers=True)
            compose(directory, state, "up", "-d", "--wait", "--wait-timeout", "600",
                    timeout=700)
        owned_resources(directory, state, require_containers=True)
        run([sys.executable, str(HERE / "synthetic-ldap-e2e.py"), "bootstrap",
             "--scratch", str(directory)], timeout=360)
        run([sys.executable, str(HERE / "synthetic-ldap-e2e.py"), "matrix",
             "--scratch", str(directory)], timeout=240)
        stages.append("live_dual_service_fixture")
        runtime = json.loads((directory / "runtime.json").read_text())
        fixture = json.loads((directory / "fixture.json").read_text())
        passwords = fixture_weknora_keys(directory)
        nc_base = f"http://127.0.0.1:{state['ports']['nextcloud']}"
        wk_base = f"http://127.0.0.1:{state['ports']['weknora']}"
        old_jwt = matrix["weknora_login"](wk_base, "alice", passwords["alice"])
        require(old_jwt and old_token_still_valid(wk_base, old_jwt) and
            matrix["knowledge_probe"](
            wk_base, runtime["knowledge_id"], old_jwt) and
            matrix["search_probe"](wk_base, fixture, old_jwt),
            "baseline old JWT cannot retrieve the indexed file")
        source_before = source_decision(nc_base, state, runtime)
        require(source_before["allow"] and source_before.get("source_etag"),
                "baseline source grant is not live")
        before = version_state(state, runtime)
        require(before["state"] == "published" and before["visible"] == 1,
                "baseline publication is not active")
        admin, csrf = login(nc_base, "devadmin", passwords["nc_admin"])
        pair_url = (nc_base + "/index.php/apps/integration_weknora/api/v1/admin/bindings/" +
                    urllib.parse.quote(runtime["binding_id"], safe="") + "/source-pairing")
        pair_status, pair_body = pair["nc_request"](admin, csrf, pair_url, "GET")
        require(pair_status == 200 and
                pair_body.get("pairing", {}).get("operation_id") == runtime["operation_id"],
                "baseline Nextcloud pair identity is unavailable")
        instance_id = pair_body["pairing"].get("instance_id")
        require(isinstance(instance_id, str) and instance_id,
                "baseline Nextcloud instance identity is unavailable")
        stages.append("published_and_authorized")

        # Stop every service in this fixture that can mutate either database or
        # source file volume before taking the coordinated checkpoint.
        owned_resources(directory, state, require_containers=True)
        compose(directory, state, "stop", "nextcloud", "wk-app", timeout=120)
        app_stopped(state, "nextcloud")
        app_stopped(state, "wk-app")
        checkpoint_start = time.monotonic()
        archives = {}
        for role, container, user, database in (
            ("nc-db", project + "-nc-db-1", "nextcloud", "nextcloud"),
            ("wk-db", project + "-wk-db-1", "weknora", "weknora"),
        ):
            path = evidence / (role + ".dump")
            pg_dump(container, user, database, path)
            archives[path.name] = sha256(path)
        # A database-only pg_dump omits the Nextcloud install's oc_admin role,
        # password and cluster grants. For this pinned-image Docker drill,
        # also take cold physical PostgreSQL volumes as restoration inputs.
        compose(directory, state, "stop", "nc-db", "wk-db", timeout=120)
        app_stopped(state, "nc-db")
        app_stopped(state, "wk-db")
        pg_volume_owners = {role: postgres_volume_owner(state, role)
                            for role in ("nc-postgres", "wk-postgres")}
        for role in ("nc-postgres", "wk-postgres", "nc-html", "wk-data"):
            path = evidence / (role + ".tar")
            archive_volume(state, role, path)
            archives[path.name] = sha256(path)
        for original, backup in CONTROL_FILES.items():
            path = evidence / backup
            copy_private(directory / original, path)
            archives[path.name] = sha256(path)
        checkpoint_seconds = round(time.monotonic() - checkpoint_start, 3)
        private_json(evidence / "checkpoint.json", {
            "project": project, "instance_id": instance_id,
            "binding_id": runtime["binding_id"], "source_id": runtime["source_id"],
            "root_file_id": runtime["root_file_id"], "file_id": runtime["file_id"],
            "source_etag": source_before["source_etag"],
            "knowledge_id": runtime["knowledge_id"], "pair_operation_id": runtime["operation_id"],
            "archive_sha256": archives, "postgres_volume_owners": pg_volume_owners,
            "checkpoint_seconds": checkpoint_seconds,
            "scope": "owned_synthetic_dual_service",
        })
        stages.append("coordinated_real_checkpoint")
        compose(directory, state, "up", "-d", "--wait", "--wait-timeout", "300",
                timeout=400)
        wait_health(nc_base + "/status.php")
        wait_health(wk_base + "/health")

        document = nc_base + "/remote.php/dav/files/devadmin/Published/acl-note.txt"
        basic = base64.b64encode(("devadmin:" + passwords["nc_admin"]).encode()).decode()
        code, _ = request(urllib.request.build_opener(), document, "DELETE",
                          {"Authorization": "Basic " + basic})
        require(code in {200, 204}, f"post-checkpoint DAV delete HTTP {code}")
        status, auth = e2e["http_json"](wk_base, "POST", "/api/v1/auth/login", {
            "email": "synthetic-admin@example.test", "password": passwords["wk_admin"]})
        require(status == 200 and isinstance(auth.get("token"), str),
                "post-checkpoint WeKnora administrator login failed")
        wk_token = auth["token"]
        t1_syncs, t1 = sync_to_terminal(wk_base, wk_token, state, runtime)
        require(not matrix["knowledge_probe"](wk_base, runtime["knowledge_id"], old_jwt),
                "post-checkpoint deletion remained readable")
        private_json(evidence / "replay-journal.json", {
            "source": "post_checkpoint_synthetic_dav_delete",
            "project": project, "pair_operation_id": runtime["operation_id"],
            "source_id": runtime["source_id"],
            "binding_id": runtime["binding_id"], "file_id": runtime["file_id"],
            "source_etag_at_checkpoint": source_before["source_etag"],
            "observed_weknora_state": t1["state"], "successful_syncs": len(t1_syncs),
            "checked_at_unix": int(time.time()),
        })
        stages.append("post_checkpoint_tombstone_journal")

        # The only destructive operation targets the just-created, label-checked
        # Compose project. The private journal and archive stay outside it.
        owned_resources(directory, state, require_containers=True)
        compose(directory, state, "down", "--volumes", "--remove-orphans", timeout=180)
        ids, names = owned_resources(directory, state, require_containers=False)
        require(not ids and not names, "old disposable project resources remain")
        restore_start = time.monotonic()
        saved_checkpoint = json.loads((evidence / "checkpoint.json").read_text())
        require(saved_checkpoint.get("project") == project and
                saved_checkpoint.get("instance_id") == instance_id and
                saved_checkpoint.get("pair_operation_id") == runtime["operation_id"] and
                saved_checkpoint.get("binding_id") == runtime["binding_id"] and
                saved_checkpoint.get("root_file_id") == runtime["root_file_id"] and
                saved_checkpoint.get("file_id") == runtime["file_id"] and
                saved_checkpoint.get("source_etag") == source_before["source_etag"] and
                saved_checkpoint.get("archive_sha256") == archives,
                "saved checkpoint identity or archive inventory changed")
        archives = saved_checkpoint["archive_sha256"]
        for name, expected in archives.items():
            verify_archive(evidence / name, expected)
        # Rehydrate the generated Compose environment, ownership marker and
        # secrets from the checkpoint before recreating any service. The
        # original scratch copies are deliberately replaced, not reused.
        for original, backup in CONTROL_FILES.items():
            restore_private(evidence / backup, directory / original)
            verify_archive(directory / original, archives[backup])
        restored_directory, restored_state = owner["owned_state"](directory)
        require(restored_directory == directory and
                restored_state == {key: value for key, value in state.items()
                                   if key != "_compose_sha256"},
                "restored fixture ownership differs from checkpoint")
        require(sha256(directory / "compose.yaml") == state["_compose_sha256"],
                "restored Compose configuration differs from checkpoint")
        passwords = fixture_weknora_keys(directory)
        stages.append("fixture_keys_restored_from_checkpoint")

        compose(directory, state, "create", "--no-build", timeout=180)
        owned_resources(directory, state, require_containers=True)
        app_stopped(state, "nextcloud")
        app_stopped(state, "wk-app")
        for role in ("nc-postgres", "wk-postgres", "nc-html", "wk-data"):
            restore_volume(state, role, evidence / (role + ".tar"))
        for role, expected_owner in pg_volume_owners.items():
            require(postgres_volume_owner(state, role) == expected_owner,
                    "restored PostgreSQL volume ownership differs from checkpoint")
        # App ingress and background work remain stopped while both real
        # PostgreSQL databases are restored.
        compose(directory, state, "up", "-d", "--wait", "--wait-timeout", "300",
                "nc-db", "wk-db", "nc-redis", "wk-redis", "openldap", timeout=400)
        app_stopped(state, "nextcloud")
        app_stopped(state, "wk-app")
        restored = version_state(state, runtime)
        require(restored["state"] == "published" and restored["visible"] == 1,
                "stale checkpoint did not restore the old publication")
        stages.append("old_checkpoint_restored_without_app_ingress")

        replay = json.loads((evidence / "replay-journal.json").read_text())
        require(replay.get("project") == project and
                replay.get("pair_operation_id") == runtime["operation_id"] and
                replay.get("source_id") == runtime["source_id"] and
                replay.get("binding_id") == runtime["binding_id"] and
                replay.get("file_id") == runtime["file_id"] and
                replay.get("source_etag_at_checkpoint") == source_before["source_etag"] and
                replay.get("observed_weknora_state") == "tombstone" and
                replay.get("successful_syncs") == 2 and
                replay.get("source") == "post_checkpoint_synthetic_dav_delete",
                "post-checkpoint journal cannot authorize replay")

        compose(directory, state, "up", "-d", "--wait", "--wait-timeout", "180",
                "nextcloud", timeout=250)
        app_stopped(state, "wk-app")
        wait_health(nc_base + "/status.php")
        admin, csrf = login(nc_base, "devadmin", passwords["nc_admin"])
        binding = urllib.parse.quote(replay["binding_id"], safe="")
        api = nc_base + "/index.php/apps/integration_weknora/api/v1/admin/bindings/" + binding
        pair_status, pair_body = pair["nc_request"](
            admin, csrf, api + "/source-pairing", "GET")
        require(pair_status == 200 and
                pair_body.get("pairing", {}).get("operation_id") == runtime["operation_id"] and
                pair_body["pairing"].get("instance_id") == instance_id and
                pair_body["pairing"].get("data_source_id") == runtime["source_id"] and
                pair_body["pairing"].get("state") == "active",
                "restored source pair does not match the checkpoint")
        binding_status, binding_body = pair["nc_request"](
            admin, csrf,
            nc_base + "/index.php/apps/integration_weknora/api/v1/admin/bindings", "GET")
        require(binding_status == 200 and any(
            item.get("id") == runtime["binding_id"] and
            item.get("root_file_id") == runtime["root_file_id"]
            for item in binding_body.get("bindings", [])),
            "restored binding root does not match the checkpoint")
        require(e2e["file_id"](document, {"Authorization": "Basic " + basic}) ==
                runtime["file_id"],
                "restored T0 DAV file identity does not match the checkpoint")
        restored_grant = source_decision(nc_base, state, runtime)
        require(restored_grant["allow"] and
                restored_grant.get("source_etag") == source_before["source_etag"],
                "restored source content does not match the checkpoint")
        status, stopped = pair["nc_request"](admin, csrf, api + "/stop", "POST", {})
        require(status == 200 and stopped.get("publication_state") == "stopped",
                "restored binding could not be fenced before WeKnora startup")
        require(not source_decision(nc_base, state, runtime)["allow"],
                "restored source remained authorized after publication stop")
        status, withdrawn = pair["nc_request"](
            admin, csrf, api + f"/files/{replay['file_id']}/withdraw", "POST", {})
        require(status == 200 and withdrawn.get("excluded") is True,
                "post-backup withdrawal journal did not replay")
        code, _ = request(urllib.request.build_opener(), document, "DELETE",
                          {"Authorization": "Basic " + basic})
        require(code in {200, 204}, f"restored stale DAV file deletion HTTP {code}")
        status, resumed = pair["nc_request"](admin, csrf, api + "/resume", "POST", {})
        require(status == 200 and resumed.get("publication_state") == "active",
                "restored binding did not resume after replay")
        require(not source_decision(nc_base, state, runtime)["allow"],
                "replayed withdrawn file became authorized after binding resume")
        stages.append("nextcloud_replayed_and_source_denied")

        # Only now can the restored WeKnora process start. Its stale vector
        # rows must not expose the old file even before full reconciliation.
        compose(directory, state, "up", "-d", "--wait", "--wait-timeout", "180",
                "wk-app", timeout=250)
        wait_health(wk_base + "/health")
        require(old_token_still_valid(wk_base, old_jwt) and
                not matrix["knowledge_probe"](wk_base, runtime["knowledge_id"], old_jwt) and
                not matrix["direct_content_probe"](
                    wk_base, runtime["knowledge_id"], old_jwt) and
                not matrix["search_probe"](wk_base, fixture, old_jwt),
                "old JWT exposed stale restored source content")
        stages.append("old_jwt_denied_before_reconciliation")
        status, auth = e2e["http_json"](wk_base, "POST", "/api/v1/auth/login", {
            "email": "synthetic-admin@example.test", "password": passwords["wk_admin"]})
        require(status == 200 and isinstance(auth.get("token"), str),
                "restored WeKnora administrator login failed")
        restore_syncs, final = sync_to_terminal(wk_base, auth["token"], state, runtime)
        require(old_token_still_valid(wk_base, old_jwt) and
                not matrix["knowledge_probe"](wk_base, runtime["knowledge_id"], old_jwt) and
                not matrix["search_probe"](wk_base, fixture, old_jwt),
                "old JWT exposed file after restored full reconciliation")
        restore_seconds = round(time.monotonic() - restore_start, 3)
        stages.append("reconciled_tombstone_and_zero_visible_candidates")
        report = {"status": "PASS", "scope": "owned_synthetic_actual_dual_service",
                  "project": project, "stages": stages,
                  "checkpoint_seconds": checkpoint_seconds,
                  "restore_seconds": restore_seconds,
                  "post_checkpoint_successful_scans": len(t1_syncs),
                  "restored_successful_scans": len(restore_syncs),
                  "old_jwt_valid_but_source_denied_before_and_after_reconcile": True,
                  "restored_source_state": final["state"],
                  "restored_visible_candidates": final["visible"],
                  "fixture_keys_restored_from_checkpoint": True,
                  "production_rpo_rto_or_external_backend_proven": False}
        owned_resources(directory, state, require_containers=True)
        compose(directory, state, "down", "--volumes", "--remove-orphans", timeout=180)
        ids, names = owned_resources(directory, state, require_containers=False)
        require(not ids and not names and
                not docker_object_exists("network", project + "_default"),
                "owned Compose resources remain after successful drill")
        shutil.rmtree(directory)
        private_json(evidence / "result.json", report)
        return report
    except BaseException:
        # Keep this one synthetic project and private scratch for diagnosis.
        # Never attempt broad Docker cleanup after an uncertain failure.
        private_json(evidence / "failure.json", {
            "status": "FAIL", "project": project, "stages_completed": stages,
            "scratch": str(directory) if directory else None,
            "scope": "owned_synthetic_only"})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weknora-image", default="weknora-ldap-app:nextcloud-rag",
                        help="already-built local patched image for the disposable fixture")
    parser.add_argument("--evidence-dir", type=Path,
                        help="new private directory; defaults to a new /tmp directory")
    args = parser.parse_args()
    if args.evidence_dir:
        evidence = args.evidence_dir.resolve()
        require(not evidence.exists(), "evidence directory already exists")
        evidence.mkdir(mode=0o700, parents=True)
    else:
        evidence = Path(tempfile.mkdtemp(prefix="nc-dual-restore-"))
        evidence.chmod(0o700)
    try:
        result = drill(args.weknora_image, evidence)
        print(json.dumps({"status": result["status"], "evidence": str(evidence),
                          "restore_seconds": result["restore_seconds"]}))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired,
            matrix["ProbeError"]) as error:
        print(f"Isolated dual-service restore failed: {error}; evidence: {evidence}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
