#!/usr/bin/env python3
"""Smoke pending initial-pair abort on two disposable local Compose stacks.

Creates one unique Nextcloud binding and one empty WeKnora KB. A loopback-only
relay in the isolated WeKnora app's network namespace rejects exactly one
synthetic commit; all other machine requests reach the isolated Nextcloud
service. The relay is removed in finally. The probe never prints credentials.

Required WeKnora app environment before starting the isolated stack:
  WEKNORA_NEXTCLOUD_DEV_HTTP=1
  WEKNORA_NEXTCLOUD_ALLOWED_ORIGINS=http://127.0.0.1:<relay-port>
The Python relay image must already exist locally; this script never pulls it.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "apps/integration_weknora/tests"))
from changes_http_smoke import file_id  # noqa: E402
from publication_http_smoke import login, remove_binding, request  # noqa: E402

_pairing = runpy.run_path(str(Path(__file__).with_name("local-source-pairing.py")))
nc_request = _pairing["nc_request"]
wk_login = _pairing["wk_login"]
wk_request = _pairing["wk_request"]

RELAY_SCRIPT = Path(__file__).with_name("local-source-pairing-relay.py")
SAFE_PROJECT = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,80}\Z")
SHARED_PROJECTS = {"nextcloud-weknora-dev", "weknora-ldap-local", "weknora"}


def origin(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or
            parsed.port is None or parsed.username is not None or parsed.password is not None or
            parsed.path or parsed.query or parsed.fragment or
            value != f"http://{parsed.hostname}:{parsed.port}"):
        raise ValueError("origins must be canonical loopback HTTP URLs with a port")
    return parsed


def env_file_values(path):
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"').strip("'")
    return values


def docker(*args):
    result = subprocess.run(["docker", *args], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=25, check=False)
    if result.returncode:
        raise RuntimeError("isolated Docker operation failed")
    return result.stdout.strip()


def compose_container(project, service):
    ids = docker("ps", "-q", "--filter", f"label=com.docker.compose.project={project}",
                 "--filter", f"label=com.docker.compose.service={service}").splitlines()
    if len(ids) != 1:
        raise RuntimeError("expected one running isolated Compose service")
    return ids[0]


def inspect(container):
    return json.loads(docker("inspect", container))[0]


def require_isolated_containers(nc_info, wk_info, nc_port, wk_port, relay_port):
    nc_ports = nc_info["NetworkSettings"]["Ports"].get("80/tcp") or []
    if not any(int(item["HostPort"]) == nc_port for item in nc_ports):
        raise RuntimeError("Nextcloud origin does not map to the isolated Compose container")
    wk_ports = wk_info["NetworkSettings"]["Ports"]
    if not any(int(item["HostPort"]) == wk_port for mappings in wk_ports.values()
               for item in (mappings or [])):
        raise RuntimeError("WeKnora origin does not map to the isolated Compose container")
    nc_networks = nc_info["NetworkSettings"]["Networks"]
    wk_networks = wk_info["NetworkSettings"]["Networks"]
    shared = set(nc_networks) & set(wk_networks)
    if not any("nextcloud" in (nc_networks[name].get("Aliases") or []) for name in shared):
        raise RuntimeError("isolated WeKnora cannot resolve the Nextcloud service alias")
    env = dict(item.split("=", 1) for item in wk_info["Config"]["Env"] if "=" in item)
    relay_origin = f"http://127.0.0.1:{relay_port}"
    allowed = {item.strip() for item in env.get("WEKNORA_NEXTCLOUD_ALLOWED_ORIGINS", "").split(",")}
    if env.get("WEKNORA_NEXTCLOUD_DEV_HTTP") != "1" or relay_origin not in allowed:
        raise RuntimeError("isolated WeKnora app has not approved the loopback relay origin")


def relay_health(name, port):
    code = ("import json,urllib.request; "
            f"print(urllib.request.urlopen('http://127.0.0.1:{port}/__relay_health',"
            "timeout=2).read().decode())")
    return json.loads(docker("exec", name, "python3", "-c", code))


def start_relay(wk_container, binding, operation_id, port, image):
    name = "nc-pair-abort-" + secrets.token_hex(6)
    source = base64.b64encode(RELAY_SCRIPT.read_bytes()).decode("ascii")
    code = f"import base64;exec(compile(base64.b64decode('{source}'),'/relay.py','exec'))"
    docker("run", "--rm", "-d", "--pull=never", "--name", name,
           "--network", f"container:{wk_container}", image, "python3", "-c", code,
           "--binding", binding, "--operation-id", operation_id, "--port", str(port))
    try:
        for _ in range(25):
            try:
                if relay_health(name, port) == {"ready": True, "blocked": False}:
                    return name
            except (RuntimeError, ValueError, KeyError):
                pass
            time.sleep(0.2)
        raise RuntimeError("isolated loopback relay did not become ready")
    except Exception:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=20, check=False)
        raise


def pair_row(status, body, operation_id, binding, kb_id, state, service):
    row = body.get("pairing") if isinstance(body, dict) else None
    if status != 200 or not isinstance(row, dict):
        raise RuntimeError(f"{service} status HTTP {status}")
    if (row.get("operation_id") != operation_id or row.get("binding_id") != binding or
            row.get("knowledge_base_id") != kb_id or row.get("state") != state):
        raise RuntimeError(f"{service} returned a different pairing intent")
    return row


def require_status(actual, expected, stage):
    if actual != expected:
        raise RuntimeError(f"{stage}: HTTP {actual}, expected {expected}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nextcloud-origin", required=True,
                        help="explicit loopback origin of an isolated Nextcloud Compose stack")
    parser.add_argument("--weknora-origin", required=True,
                        help="explicit loopback origin of an isolated WeKnora Compose stack")
    parser.add_argument("--nextcloud-env-file", required=True,
                        help=".env for that isolated Nextcloud stack; never printed")
    parser.add_argument("--nextcloud-compose-project", required=True)
    parser.add_argument("--weknora-compose-project", required=True)
    parser.add_argument("--relay-port", required=True, type=int,
                        help="port approved in the isolated WeKnora app environment")
    parser.add_argument("--relay-image", default="python:3.12-alpine")
    args = parser.parse_args()
    try:
        nc_origin = origin(args.nextcloud_origin)
        wk_origin = origin(args.weknora_origin)
    except ValueError as error:
        parser.error(str(error))
    for value in (args.nextcloud_compose_project, args.weknora_compose_project):
        if not SAFE_PROJECT.fullmatch(value) or value in SHARED_PROJECTS:
            parser.error("use explicit disposable Compose project names, not the shared projects")
    if args.nextcloud_compose_project == args.weknora_compose_project:
        parser.error("provide separate isolated Compose project names")
    if not 1024 <= args.relay_port <= 65535:
        parser.error("--relay-port must be an unprivileged TCP port")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:-]{0,127}", args.relay_image):
        parser.error("invalid relay image name")
    values = env_file_values(Path(args.nextcloud_env_file))
    owner = values.get("NEXTCLOUD_ADMIN_USER")
    password = values.get("NEXTCLOUD_ADMIN_PASSWORD")
    if not owner or not password:
        parser.error("isolated Nextcloud env file needs admin credentials")
    wk_container = compose_container(args.weknora_compose_project, "app")
    nc_container = compose_container(args.nextcloud_compose_project, "nextcloud")
    require_isolated_containers(inspect(nc_container), inspect(wk_container),
                                nc_origin.port, wk_origin.port, args.relay_port)

    nc_base = args.nextcloud_origin
    wk_base = args.weknora_origin
    api = nc_base + "/index.php/apps/integration_weknora/api/v1"
    nc_admin, csrf = login(nc_base, owner, password)
    wk_token, tenant_id = wk_login(wk_base)
    suffix = secrets.token_hex(8)
    binding = "pair-abort-smoke-" + suffix
    folder_url = (nc_base + "/remote.php/dav/files/" +
                  urllib.parse.quote(owner, safe="") + "/" + binding)
    dav_headers = {"Authorization": "Basic " + base64.b64encode(
        f"{owner}:{password}".encode()).decode()}
    binding_url = f"{api}/admin/bindings/{binding}"
    pair_url = binding_url + "/source-pairing"
    wk_pair_path = "/api/v1/datasource/nextcloud-source-pairings"
    kb_path = "/api/v1/knowledge-bases"
    machine = urllib.request.build_opener()
    owned_folder = owned_binding = owned_kb = False
    kb_id = None
    relay_name = None
    operations = []
    stage = "create fixture"
    passed = False
    try:
        status, _ = request(machine, folder_url, "MKCOL", dav_headers)
        require_status(status, 201, "create owned WebDAV folder")
        owned_folder = True
        root_id = file_id(folder_url, dav_headers)
        status, body = nc_request(nc_admin, csrf, f"{api}/admin/bindings", "POST", {
            "id": binding, "name": binding, "owner_uid": owner, "root_file_id": root_id})
        require_status(status, 201, "create owned binding")
        if body.get("binding", {}).get("id") != binding:
            raise RuntimeError("created binding ACK differs from fixture")
        owned_binding = True
        status, body = wk_request(wk_base, wk_token, "POST", kb_path,
                                  {"name": binding, "type": "document"})
        require_status(status, 201, "create owned knowledge base")
        created_kb = body.get("data") if isinstance(body, dict) else None
        kb_id = created_kb.get("id") if isinstance(created_kb, dict) else None
        if (not isinstance(kb_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", kb_id) or
                created_kb.get("name") != binding or str(created_kb.get("tenant_id")) != tenant_id):
            raise RuntimeError("created knowledge base ACK differs from fixture")
        owned_kb = True

        stage = "Nextcloud-only lost-key abort"
        lost_op = str(uuid.uuid4())
        status, body = nc_request(nc_admin, csrf, pair_url, "POST", {
            "operation_id": lost_op, "tenant_id": tenant_id, "knowledge_base_id": kb_id})
        require_status(status, 201, "prepare lost-key operation")
        operations.append(lost_op)
        lost = pair_row(200, body, lost_op, binding, kb_id, "pending", "Nextcloud")
        if not isinstance(body.get("token"), str) or not body["token"]:
            raise RuntimeError("lost-key preparation returned no one-time key")
        del body  # Do not store or expose the deliberately lost token.
        status, _ = wk_request(wk_base, wk_token, "GET", f"{wk_pair_path}/{lost_op}")
        require_status(status, 404, "lost-key operation absent from WeKnora")
        status, _ = wk_request(wk_base, wk_token, "POST", f"{wk_pair_path}/{lost_op}/abort")
        require_status(status, 404, "lost-key WeKnora abort")
        status, body = nc_request(nc_admin, csrf, pair_url, "DELETE", {"operation_id": lost_op})
        pair_row(status, body, lost_op, binding, kb_id, "aborted", "Nextcloud")

        stage = "WeKnora pending-pair abort"
        pending_op = str(uuid.uuid4())
        relay_name = start_relay(wk_container, binding, pending_op, args.relay_port, args.relay_image)
        status, body = nc_request(nc_admin, csrf, pair_url, "POST", {
            "operation_id": pending_op, "tenant_id": tenant_id, "knowledge_base_id": kb_id})
        require_status(status, 201, "prepare pending operation")
        operations.append(pending_op)
        pending_nc = pair_row(200, body, pending_op, binding, kb_id, "pending", "Nextcloud")
        one_time_token = body.get("token")
        if not isinstance(one_time_token, str) or not one_time_token:
            raise RuntimeError("pending preparation returned no one-time key")
        status, body = wk_request(wk_base, wk_token, "POST", wk_pair_path, {
            "knowledge_base_id": kb_id, "base_url": f"http://127.0.0.1:{args.relay_port}",
            "binding_id": binding, "operation_id": pending_op,
            "instance_id": pending_nc.get("instance_id"),
            "publication_epoch": pending_nc.get("publication_epoch"),
            "key_id": pending_nc.get("key_id"), "token": one_time_token})
        del one_time_token
        require_status(status, 202, "controlled commit fault leaves WeKnora pending")
        if relay_health(relay_name, args.relay_port) != {"ready": True, "blocked": True}:
            raise RuntimeError("relay did not block the exact synthetic commit")
        status, body = wk_request(wk_base, wk_token, "GET", f"{wk_pair_path}/{pending_op}")
        wk_pending = pair_row(status, body, pending_op, binding, kb_id, "pending", "WeKnora")
        if (wk_pending.get("instance_id") != pending_nc.get("instance_id") or
                wk_pending.get("key_id") != pending_nc.get("key_id") or
                not isinstance(wk_pending.get("data_source_id"), str)):
            raise RuntimeError("pending source tuple differs across services")
        status, body = nc_request(nc_admin, csrf, pair_url, "GET")
        pair_row(status, body, pending_op, binding, kb_id, "pending", "Nextcloud")

        wrong_op = str(uuid.uuid4())
        status, _ = wk_request(wk_base, wk_token, "POST", f"{wk_pair_path}/{wrong_op}/abort")
        require_status(status, 404, "wrong WeKnora operation cannot abort")
        status, _ = nc_request(nc_admin, csrf, pair_url, "DELETE", {"operation_id": wrong_op})
        require_status(status, 404, "wrong Nextcloud operation cannot abort")
        status, body = wk_request(wk_base, wk_token, "GET", f"{wk_pair_path}/{pending_op}")
        pair_row(status, body, pending_op, binding, kb_id, "pending", "WeKnora")

        status, body = wk_request(wk_base, wk_token, "POST", f"{wk_pair_path}/{pending_op}/abort")
        pair_row(status, body, pending_op, binding, kb_id, "aborted", "WeKnora")
        status, body = nc_request(nc_admin, csrf, pair_url, "GET")
        pair_row(status, body, pending_op, binding, kb_id, "aborted", "Nextcloud")
        status, body = wk_request(wk_base, wk_token, "POST", f"{wk_pair_path}/{pending_op}/abort")
        pair_row(status, body, pending_op, binding, kb_id, "aborted", "WeKnora retry")
        source_id = wk_pending["data_source_id"]
        status, _ = wk_request(wk_base, wk_token, "GET", f"/api/v1/datasource/{source_id}")
        require_status(status, 404, "aborted paused source removed")

        stage = "active pair abort rejection"
        active_op = str(uuid.uuid4())
        status, body = nc_request(nc_admin, csrf, pair_url, "POST", {
            "operation_id": active_op, "tenant_id": tenant_id, "knowledge_base_id": kb_id})
        require_status(status, 201, "prepare synthetic active operation")
        operations.append(active_op)
        active = pair_row(200, body, active_op, binding, kb_id, "pending", "Nextcloud")
        active_token = body.get("token")
        if not isinstance(active_token, str) or not active_token:
            raise RuntimeError("active preparation returned no one-time key")
        source_id = str(uuid.uuid4())
        status, body_bytes = request(machine,
            f"{api}/bindings/{binding}/source-pairing/commit", "POST",
            {"Authorization": "Bearer " + active_token, "X-WeKnora-Key-Id": active["key_id"],
             "Content-Type": "application/json"}, json.dumps({
                "operation_id": active_op, "instance_id": active["instance_id"],
                "tenant_id": tenant_id, "knowledge_base_id": kb_id,
                "data_source_id": source_id}, separators=(",", ":")).encode())
        require_status(status, 200, "commit synthetic Nextcloud-only active pair")
        pair_row(status, json.loads(body_bytes), active_op, binding, kb_id, "active", "Nextcloud")
        status, _ = nc_request(nc_admin, csrf, pair_url, "DELETE", {"operation_id": active_op})
        require_status(status, 409, "admin cannot abort active pair")
        status, _ = request(machine, f"{api}/bindings/{binding}/source-pairing/abort", "POST",
                            {"Authorization": "Bearer " + active_token,
                             "X-WeKnora-Key-Id": active["key_id"],
                             "Content-Type": "application/json"}, json.dumps({
                                "operation_id": active_op, "instance_id": active["instance_id"],
                                "tenant_id": tenant_id, "knowledge_base_id": kb_id},
                                separators=(",", ":")).encode())
        del active_token
        require_status(status, 409, "machine cannot abort active pair")
        status, body = nc_request(nc_admin, csrf, pair_url, "GET")
        pair_row(status, body, active_op, binding, kb_id, "active", "Nextcloud")
        passed = True
    finally:
        # If an assertion fails, close only operations created above. Never
        # remove the fixture while WeKnora still needs its pending key to
        # recover a remote abort, or while its state is uncertain.
        safe_to_remove = True
        cleanup_failed = False
        try:
            if owned_binding:
                for op in reversed(operations):
                    status, body = wk_request(wk_base, wk_token, "GET", f"{wk_pair_path}/{op}")
                    if status == 200:
                        row = body.get("pairing") if isinstance(body, dict) else None
                        if not isinstance(row, dict) or row.get("binding_id") != binding or \
                                row.get("operation_id") != op or row.get("knowledge_base_id") != kb_id:
                            safe_to_remove = False
                            break
                        if row.get("state") == "pending":
                            for _ in range(3):
                                abort_status, abort_body = wk_request(
                                    wk_base, wk_token, "POST", f"{wk_pair_path}/{op}/abort")
                                ack = abort_body.get("pairing") if isinstance(abort_body, dict) else None
                                if abort_status == 200 and isinstance(ack, dict) and \
                                        ack.get("operation_id") == op and \
                                        ack.get("binding_id") == binding and \
                                        ack.get("knowledge_base_id") == kb_id and \
                                        ack.get("state") == "aborted":
                                    break
                            else:
                                safe_to_remove = False
                                break
                        elif row.get("state") != "aborted":
                            safe_to_remove = False
                            break
                    elif status != 404:
                        safe_to_remove = False
                        break
                    nc_status, nc_body = nc_request(nc_admin, csrf, pair_url, "GET")
                    nc_row = nc_body.get("pairing") if isinstance(nc_body, dict) else None
                    if nc_status == 200 and isinstance(nc_row, dict) and nc_row.get("operation_id") == op:
                        if nc_row.get("state") == "pending":
                            abort_status, abort_body = nc_request(
                                nc_admin, csrf, pair_url, "DELETE", {"operation_id": op})
                            ack = abort_body.get("pairing") if isinstance(abort_body, dict) else None
                            if abort_status != 200 or not isinstance(ack, dict) or \
                                    ack.get("operation_id") != op or ack.get("binding_id") != binding or \
                                    ack.get("knowledge_base_id") != kb_id or \
                                    ack.get("state") != "aborted":
                                safe_to_remove = False
                                break
                        elif nc_row.get("state") not in ("aborted", "active"):
                            safe_to_remove = False
                            break
                # Another operator could have created a newer intent on this
                # binding. Do not retire anything we did not create.
                nc_status, nc_body = nc_request(nc_admin, csrf, pair_url, "GET")
                nc_row = nc_body.get("pairing") if isinstance(nc_body, dict) else None
                if nc_status == 200 and isinstance(nc_row, dict) and \
                        nc_row.get("operation_id") not in operations:
                    safe_to_remove = False
                elif nc_status not in (200, 404):
                    safe_to_remove = False
        except Exception:
            safe_to_remove = False
            cleanup_failed = True
        finally:
            if relay_name is not None:
                try:
                    result = subprocess.run(["docker", "rm", "-f", relay_name],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            timeout=20, check=False)
                    if result.returncode:
                        cleanup_failed = True
                except Exception:
                    cleanup_failed = True
        if owned_binding and safe_to_remove:
            try:
                remove_binding(nc_admin, api, binding, csrf)
                owned_binding = False
            except Exception:
                cleanup_failed = True
        if owned_folder and not owned_binding:
            try:
                status, _ = request(machine, folder_url, "DELETE", dav_headers)
                if status == 204:
                    owned_folder = False
                else:
                    cleanup_failed = True
            except Exception:
                cleanup_failed = True
        if owned_kb and not owned_binding:
            try:
                status, _ = wk_request(wk_base, wk_token, "DELETE", f"{kb_path}/{kb_id}")
                if status == 200:
                    owned_kb = False
                else:
                    cleanup_failed = True
            except Exception:
                cleanup_failed = True
        if owned_binding or owned_folder or owned_kb:
            print(f"retained synthetic fixture for recovery: binding={binding} kb={kb_id} "
                  f"operations={','.join(operations)}", file=sys.stderr)
        if cleanup_failed:
            raise RuntimeError("synthetic fixture cleanup was incomplete")
    if passed:
        print("pending source pairing abort smoke passed: lost key, exact cross-system abort, active rejection")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Imported HTTP helpers can include response bodies in exceptions.
        # Suppress them so no one-time token or administrator credential leaks.
        print(f"source pairing abort smoke failed ({type(error).__name__}); inspect isolated stack",
              file=sys.stderr)
        sys.exit(1)
