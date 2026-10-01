#!/usr/bin/env python3
"""Prove a failed 0.4.6 app upgrade can recover from a matched cold checkpoint.

Creates its own loopback-only Compose project, random credentials and volumes.
It never accepts a project, volume, credential file or remote origin as input.
The deliberately broken migration exists only in a temporary app copy.
"""

from __future__ import annotations

import base64
import hashlib
from http.cookiejar import CookieJar
import io
import json
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apps/integration_weknora"
OLD_APP_COMMIT = "5b97db539155be61541836ba3d67074e78705340"
OLD_VERSION = "0.4.6"
FAULT_MARKER = "WEKNORA_UPGRADE_FAILURE_PROBE"
IMAGES = {
    "db": "postgres:16-alpine@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416",
    "redis": "redis:7-alpine@sha256:8b81dd37ff027bec4e516d41acfbe9fe2460070dc6d4a4570a2ac5b9d59df065",
    "nextcloud": "nextcloud:34.0.4-apache@sha256:a5ace30c695afe48c2c406e940ee7886a81e13fa382e57cd68b2416d1a66914c",
    "archive": "python:3.12-alpine@sha256:4c47124a8391cb7a9f571164147d154777cf012a4ece5f86097130d7a4478111",
}
SERVICES = frozenset(("db", "redis", "nextcloud"))
VOLUMES = frozenset(("db-data", "redis-data", "nextcloud-html"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def command(args: list[str], *, cwd: Path, input_bytes: bytes | None = None,
            timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(args, cwd=cwd, input=input_bytes, capture_output=True,
                            timeout=timeout, check=False)
    if check and result.returncode:
        # Docker and application output may include generated fixture secrets.
        raise RuntimeError(f"{args[0]} command failed (exit {result.returncode})")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_compose(project: str, port: int, app_dir: Path, db_password: str,
                 admin_password: str) -> dict:
    return {
        "name": project,
        "services": {
            "db": {
                "image": IMAGES["db"],
                "environment": {"POSTGRES_DB": "nextcloud", "POSTGRES_USER": "nextcloud",
                                "POSTGRES_PASSWORD": db_password},
                "volumes": ["db-data:/var/lib/postgresql/data"],
                "healthcheck": {"test": ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U nextcloud -d nextcloud"],
                                "interval": "5s", "timeout": "5s", "retries": 30},
            },
            "redis": {
                "image": IMAGES["redis"], "command": ["redis-server", "--appendonly", "yes"],
                "volumes": ["redis-data:/data"],
                "healthcheck": {"test": ["CMD", "redis-cli", "ping"],
                                "interval": "5s", "timeout": "3s", "retries": 20},
            },
            "nextcloud": {
                "image": IMAGES["nextcloud"],
                "ports": [{"target": 80, "published": port, "host_ip": "127.0.0.1"}],
                "environment": {
                    "POSTGRES_HOST": "db", "POSTGRES_DB": "nextcloud",
                    "POSTGRES_USER": "nextcloud", "POSTGRES_PASSWORD": db_password,
                    "NEXTCLOUD_ADMIN_USER": "upgrade-admin",
                    "NEXTCLOUD_ADMIN_PASSWORD": admin_password,
                    "NEXTCLOUD_TRUSTED_DOMAINS": "localhost 127.0.0.1",
                    "REDIS_HOST": "redis",
                },
                "volumes": ["nextcloud-html:/var/www/html", {
                    "type": "bind", "source": str(app_dir),
                    "target": "/var/www/html/custom_apps/integration_weknora",
                    "read_only": True,
                }],
                "depends_on": {"db": {"condition": "service_healthy"},
                               "redis": {"condition": "service_healthy"}},
                "healthcheck": {"test": ["CMD-SHELL", "curl -fsS http://127.0.0.1/status.php >/dev/null"],
                                "interval": "10s", "timeout": "5s", "retries": 40,
                                "start_period": "60s"},
            },
        },
        "volumes": {name: {} for name in sorted(VOLUMES)},
    }


def inspect(kind: str, name: str, cwd: Path) -> dict | None:
    result = command(["docker", kind, "inspect", name], cwd=cwd, timeout=30,
                     check=False)
    if result.returncode:
        return None
    return json.loads(result.stdout)[0]


def listed(kind: str, project: str, cwd: Path) -> list[str]:
    args = (["docker", "ps", "-aq"] if kind == "container" else
            ["docker", kind, "ls", "-q"])
    output = command(args + ["--filter", f"label=com.docker.compose.project={project}"],
                     cwd=cwd, timeout=30).stdout.decode()
    return output.split()


def assert_unoccupied(project: str, cwd: Path) -> None:
    for kind in ("container", "volume", "network"):
        require(not listed(kind, project, cwd), "generated Compose project already exists")
    for kind, names in (("volume", [f"{project}_{role}" for role in VOLUMES]),
                        ("network", [f"{project}_default"])):
        for name in names:
            require(inspect(kind, name, cwd) is None,
                    "generated Compose resource name already exists")


def assert_owned(project: str, directory: Path, config_hash: str,
                 *, complete: bool = False) -> None:
    require(re.fullmatch(r"nc-upgrade-recovery-[0-9a-f]{8}", project) is not None,
            "refusing a non-disposable project name")
    require(sha256(directory / "compose.json") == config_hash,
            "generated Compose file changed")
    marker = json.loads((directory / "owner.json").read_text())
    require(marker == {"project": project, "compose_sha256": config_hash},
            "fixture ownership marker changed")
    for container_id in listed("container", project, directory):
        item = inspect("container", container_id, directory)
        labels = item["Config"].get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        require(labels.get("com.docker.compose.project") == project and
                service in SERVICES and item["Name"] == f"/{project}-{service}-1",
                "unexpected container in disposable project")
        for bindings in (item.get("HostConfig", {}).get("PortBindings") or {}).values():
            for binding in bindings or []:
                require(binding.get("HostIp") == "127.0.0.1",
                        "disposable container published a non-loopback port")
    found = set()
    for name in listed("volume", project, directory):
        item = inspect("volume", name, directory)
        labels = item.get("Labels") or {}
        role = name[len(project) + 1:]
        require(name == f"{project}_{role}" and role in VOLUMES and
                labels.get("com.docker.compose.project") == project and
                labels.get("com.docker.compose.volume") == role,
                "unexpected volume in disposable project")
        found.add(role)
    if complete:
        require(found == VOLUMES, "disposable project has missing volumes")
    for name in listed("network", project, directory):
        item = inspect("network", name, directory)
        require(item["Name"] == f"{project}_default" and
                (item.get("Labels") or {}).get("com.docker.compose.project") == project,
                "unexpected network in disposable project")


def load_old_app(destination: Path) -> None:
    archive = command(["git", "archive", OLD_APP_COMMIT,
                       "apps/integration_weknora"], cwd=ROOT, timeout=30).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            require((member.name in ("apps", "apps/integration_weknora") or
                     member.name.startswith("apps/integration_weknora/")) and
                    not member.issym() and not member.islnk() and
                    ".." not in Path(member.name).parts,
                    "fixed app archive contains an unexpected path")
        tar.extractall(destination)
    info = destination / "apps/integration_weknora/appinfo/info.xml"
    require(ET.parse(info).getroot().findtext("version") == OLD_VERSION,
            "fixed Git baseline is not 0.4.6")


def replace_app(source: Path, target: Path) -> None:
    # Keep the bind-mounted top-level directory inode; only its children change.
    for child in target.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(source, target, dirs_exist_ok=True)


def inject_failure(app_dir: Path) -> None:
    migration = app_dir / "lib/Migration/Version0020Date20261001000000.php"
    latest = sorted((app_dir / "lib/Migration").glob("Version*.php"))[-1]
    require(latest == migration,
            "the final app migration changed; update the failure injection")
    content = migration.read_text()
    require(content.endswith("}\n") and "postSchemaChange" not in content,
            "expected final upgrade migration changed; update the probe")
    method = ("    public function postSchemaChange(\\OCP\\Migration\\IOutput $output, "
              "\\Closure $schemaClosure, array $options): void {\n"
              f"        throw new \\RuntimeException('{FAULT_MARKER}');\n"
              "    }\n")
    migration.write_text(content[:-2] + method + "}\n")


def archive_volume(project: str, role: str, destination: Path, cwd: Path) -> None:
    with destination.open("wb") as stream:
        destination.chmod(0o600)
        result = subprocess.run([
            "docker", "run", "--rm", "--pull", "never", "--network", "none",
            "--mount", f"type=volume,source={project}_{role},target=/payload,readonly",
            "--entrypoint", "tar", IMAGES["archive"], "-C", "/payload", "-cf", "-", ".",
        ], cwd=cwd, stdout=stream, stderr=subprocess.PIPE, timeout=300)
    require(result.returncode == 0, "cold volume archive failed")


def restore_volume(project: str, role: str, source: Path, cwd: Path) -> None:
    with source.open("rb") as stream:
        result = subprocess.run([
            "docker", "run", "--rm", "-i", "--pull", "never", "--network", "none",
            "--mount", f"type=volume,source={project}_{role},target=/payload,volume-nocopy",
            "--entrypoint", "tar", IMAGES["archive"], "-C", "/payload", "-xf", "-",
        ], cwd=cwd, stdin=stream, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=300)
    require(result.returncode == 0, "cold volume restore failed")


def volume_owner(project: str, role: str, cwd: Path) -> str:
    output = command([
        "docker", "run", "--rm", "--pull", "never", "--network", "none",
        "--mount", f"type=volume,source={project}_{role},target=/payload,readonly",
        "--entrypoint", "stat", IMAGES["archive"], "-c", "%u:%g:%a", "/payload",
    ], cwd=cwd, timeout=30).stdout.decode().strip()
    require(re.fullmatch(r"[0-9]+:[0-9]+:[0-7]+", output) is not None,
            "PostgreSQL volume owner is invalid")
    return output


def volume_entries(project: str, role: str, cwd: Path) -> int:
    require(role in {"db-data", "nextcloud-html"}, "unknown restore target role")
    output = command([
        "docker", "run", "--rm", "--pull", "never", "--network", "none",
        "--mount", f"type=volume,source={project}_{role},target=/payload,volume-nocopy",
        "--entrypoint", "python", IMAGES["archive"], "-c",
        "from pathlib import Path; print(len(list(Path('/payload').iterdir())))",
    ], cwd=cwd, timeout=30).stdout.decode().strip()
    require(output.isdigit(), "restore volume entry count is invalid")
    return int(output)


def clear_owned_volume(project: str, role: str, cwd: Path) -> None:
    require(role in {"db-data", "nextcloud-html"}, "unknown restore target role")
    command([
        "docker", "run", "--rm", "--pull", "never", "--network", "none",
        "--mount", f"type=volume,source={project}_{role},target=/payload,volume-nocopy",
        "--entrypoint", "python", IMAGES["archive"], "-c",
        "from pathlib import Path; import shutil; root=Path('/payload'); "
        "[(shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()) "
        "for p in root.iterdir()]",
    ], cwd=cwd, timeout=60)
    require(volume_entries(project, role, cwd) == 0,
            "restore target volume is not empty")


def ensure_archive_image(cwd: Path) -> None:
    image = IMAGES["archive"]
    result = command(["docker", "image", "inspect", image], cwd=cwd,
                     timeout=30, check=False)
    if result.returncode:
        command(["docker", "pull", image], cwd=cwd, timeout=300)
    require(command(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                    cwd=cwd, timeout=30).stdout.decode().strip().startswith("sha256:"),
            "pinned archive image is unavailable")


def dav(port: int, password: str, path: str, *, method: str = "GET",
        body: bytes | None = None, depth: str | None = None) -> tuple[int, bytes]:
    auth = base64.b64encode(("upgrade-admin:" + password).encode()).decode()
    headers = {"Authorization": "Basic " + auth}
    if depth is not None:
        headers["Depth"] = depth
    url = f"http://127.0.0.1:{port}/remote.php/dav/files/upgrade-admin/{path}"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, response.read()


def dav_id(port: int, password: str, path: str) -> int:
    body = (b'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
            b'<d:prop><oc:fileid/></d:prop></d:propfind>')
    status, response = dav(port, password, path, method="PROPFIND", body=body,
                           depth="0")
    require(status == 207, "source DAV PROPFIND did not return 207")
    item = ET.fromstring(response).find(".//{http://owncloud.org/ns}fileid")
    require(item is not None and item.text and item.text.isdigit(),
            "source DAV file ID is missing")
    return int(item.text)


def assert_admin_binding_route(port: int, password: str, root_id: int) -> None:
    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()))
    with opener.open(base + "/login", timeout=30) as response:
        require(response.status == 200, "restored admin login page failed")
        page = response.read()
    match = re.search(rb'data-requesttoken="([^"]+)"', page)
    require(match is not None, "restored admin login CSRF token is missing")
    body = urllib.parse.urlencode({
        "requesttoken": match.group(1).decode(),
        "user": "upgrade-admin", "password": password,
    }).encode()
    request = urllib.request.Request(base + "/login", data=body,
                                     headers={"Origin": base})
    with opener.open(request, timeout=30) as response:
        require(response.status == 200, "restored admin session failed")
    with opener.open(base + "/apps/dashboard/", timeout=30) as response:
        require(response.status == 200, "restored admin dashboard failed")
        page = response.read()
    match = re.search(rb'data-requesttoken="([^"]+)"', page)
    require(match is not None, "restored admin session CSRF token is missing")
    endpoint = base + "/index.php/apps/integration_weknora/api/v1/admin/bindings"
    request = urllib.request.Request(endpoint, headers={
        "requesttoken": match.group(1).decode(),
    })
    with opener.open(request, timeout=30) as response:
        require(response.status == 200, "restored integration admin route failed")
        payload = json.load(response)
    bindings = payload.get("bindings")
    require(isinstance(bindings, list) and any(
        item.get("id") == "recovery-probe" and item.get("root_file_id") == root_id
        for item in bindings if isinstance(item, dict)),
        "restored integration did not load its binding")


def main() -> None:
    project = "nc-upgrade-recovery-" + secrets.token_hex(4)
    port = free_port()
    password = "x" + secrets.token_urlsafe(30)
    db_password = "x" + secrets.token_urlsafe(30)
    content = b"upgrade-recovery-file-" + secrets.token_bytes(32)
    app_probe = secrets.token_hex(16)
    current_version = ET.parse(APP / "appinfo/info.xml").getroot().findtext("version")
    require(current_version != OLD_VERSION and current_version,
            "current app version must follow 0.4.6")
    with tempfile.TemporaryDirectory(prefix="nc-upgrade-recovery-") as temp_name:
        directory = Path(temp_name)
        directory.chmod(0o700)
        load_old_app(directory / "baseline")
        baseline = directory / "baseline/apps/integration_weknora"
        app_dir = directory / "app"
        shutil.copytree(baseline, app_dir)
        config = directory / "compose.json"
        config.write_text(json.dumps(make_compose(project, port, app_dir, db_password,
                                                  password), indent=2) + "\n")
        config.chmod(0o600)
        empty_env = directory / "empty.env"
        empty_env.write_text("")
        empty_env.chmod(0o600)
        config_hash = sha256(config)
        marker = directory / "owner.json"
        marker.write_text(json.dumps({"project": project,
                                      "compose_sha256": config_hash}) + "\n")
        marker.chmod(0o600)
        compose = ["docker", "compose", "--env-file", str(empty_env),
                   "-p", project, "-f", str(config)]

        def compose_run(*args: str, timeout: int = 600) -> str:
            return command(compose + list(args), cwd=directory, timeout=timeout).stdout.decode().strip()

        rendered = json.loads(compose_run("config", "--format", "json"))
        require(rendered.get("name") == project and
                set(rendered.get("services", {})) == SERVICES and
                set(rendered.get("volumes", {})) == VOLUMES,
                "rendered Compose topology is not the private fixture")
        for service in SERVICES:
            require(rendered["services"][service]["image"] == IMAGES[service],
                    "rendered Compose image differs from its pin")
        service = rendered["services"]["nextcloud"]
        require(service["ports"] == [{"mode": "ingress", "target": 80,
                                      "published": str(port), "protocol": "tcp",
                                      "host_ip": "127.0.0.1"}] or
                (len(service["ports"]) == 1 and
                 service["ports"][0].get("host_ip") == "127.0.0.1" and
                 str(service["ports"][0].get("published")) == str(port)),
                "fixture HTTP listener is not loopback-only")
        mounts = service["volumes"]
        require(len(mounts) == 2 and any(
            mount.get("type") == "bind" and mount.get("source") == str(app_dir) and
            mount.get("target") == "/var/www/html/custom_apps/integration_weknora" and
            mount.get("read_only") is True for mount in mounts),
            "fixture does not bind only its temporary app copy")
        require(rendered["services"]["nextcloud"]["environment"][
                    "NEXTCLOUD_ADMIN_PASSWORD"] == password and
                rendered["services"]["db"]["environment"]["POSTGRES_PASSWORD"] == db_password,
                "fixture credentials do not match generated private values")
        assert_unoccupied(project, directory)
        ensure_archive_image(directory)

        def occ(*args: str) -> str:
            return compose_run("exec", "-T", "-u", "www-data", "nextcloud",
                               "php", "occ", *args, timeout=180)

        def sql(statement: str) -> str:
            return command(compose + ["exec", "-T", "db", "psql", "-U", "nextcloud",
                                      "-d", "nextcloud", "-v", "ON_ERROR_STOP=1",
                                      "-At"], cwd=directory,
                           input_bytes=statement.encode(), timeout=60).stdout.decode().strip()

        started = False
        report = None
        try:
            started = True
            compose_run("up", "-d", "--wait", "--wait-timeout", "600", timeout=660)
            assert_owned(project, directory, config_hash, complete=True)
            occ("app:enable", "integration_weknora")
            require(occ("config:app:get", "integration_weknora", "installed_version") ==
                    OLD_VERSION, "baseline app was not installed as 0.4.6")
            status, _ = dav(port, password, "UpgradeRoot", method="MKCOL")
            require(status == 201, "baseline folder creation failed")
            root_id = dav_id(port, password, "UpgradeRoot")
            file_path = "UpgradeRoot/recovery-note.txt"
            status, _ = dav(port, password, file_path, method="PUT", body=content)
            require(status in (201, 204), "baseline DAV PUT failed")
            file_id = dav_id(port, password, file_path)
            status, source = dav(port, password, file_path)
            require(status == 200 and source == content, "baseline DAV bytes differ")
            status, _ = dav(port, password, "", method="PROPFIND", depth="0")
            require(status == 207, "baseline core DAV listing failed")
            bindings = json.dumps([{"id": "recovery-probe", "name": "Recovery probe",
                                    "owner_uid": "upgrade-admin", "root_file_id": root_id}],
                                  separators=(",", ":"))
            occ("config:app:set", "integration_weknora", "bindings", "--value=" + bindings)
            occ("config:app:set", "integration_weknora", "recovery_probe",
                "--value=" + app_probe)
            sql("INSERT INTO oc_weknora_pub_state "
                "(binding_id, file_id, state, actor_uid, updated_at) "
                f"VALUES ('recovery-probe', {file_id}, 'withdrawn', 'upgrade-admin', 1);")
            require(sql("SELECT state FROM oc_weknora_pub_state WHERE "
                        f"binding_id='recovery-probe' AND file_id={file_id};") == "withdrawn",
                    "baseline plugin publication state missing")
            instance_id = occ("config:system:get", "instanceid")
            require(instance_id, "baseline Nextcloud instance ID missing")

            assert_owned(project, directory, config_hash, complete=True)
            compose_run("stop", "nextcloud", "db", "redis", timeout=120)
            for service_name in SERVICES:
                state = inspect("container", f"{project}-{service_name}-1", directory)
                require(state is not None and not state["State"]["Running"],
                        "checkpoint was not cold")
            db_owner = volume_owner(project, "db-data", directory)
            checksums = {}
            for role in ("db-data", "nextcloud-html"):
                path = directory / (role + ".tar")
                archive_volume(project, role, path, directory)
                checksums[role] = sha256(path)
            require(len(checksums) == 2, "matched checkpoint is incomplete")

            compose_run("up", "-d", "--wait", "--wait-timeout", "300", timeout=360)
            occ("maintenance:mode", "--on")
            require(json.loads(occ("status", "--output=json")).get("maintenance") is True,
                    "isolated ingress did not enter maintenance mode")
            replace_app(APP, app_dir)
            require(ET.parse(app_dir / "appinfo/info.xml").getroot().findtext("version") ==
                    current_version, "temporary upgrade copy has wrong version")
            inject_failure(app_dir)
            failed = command(compose + ["exec", "-T", "-u", "www-data", "nextcloud",
                                        "php", "occ", "upgrade"], cwd=directory,
                             timeout=300, check=False)
            require(failed.returncode != 0 and
                    FAULT_MARKER.encode() in failed.stdout + failed.stderr,
                    "upgrade did not fail at the injected final migration")
            require(json.loads(occ("status", "--output=json")).get("maintenance") is True,
                    "failed upgrade reopened isolated application ingress")
            require(sql("SELECT COUNT(*) FROM pg_tables WHERE "
                        "tablename='oc_weknora_src_decom';") == "1",
                    "injected failure did not reach the late app migration")
            require(sql("SELECT state FROM oc_weknora_pub_state WHERE "
                        f"binding_id='recovery-probe' AND file_id={file_id};") == "withdrawn",
                    "prior plugin row disappeared during failed upgrade")
            require(sql("SELECT configvalue FROM oc_appconfig WHERE appid='integration_weknora' "
                        "AND configkey='recovery_probe';") == app_probe,
                    "prior plugin configuration disappeared during failed upgrade")
            # Both post-checkpoint mutations prove the restore replaces the
            # failed database and file volume, not just the application code.
            sql("UPDATE oc_appconfig SET configvalue='after-failed-upgrade' "
                "WHERE appid='integration_weknora' AND configkey='recovery_probe';")
            require(sql("SELECT configvalue FROM oc_appconfig WHERE appid='integration_weknora' "
                        "AND configkey='recovery_probe';") == "after-failed-upgrade",
                    "post-checkpoint DB mutation failed")
            compose_run("stop", "nextcloud", "db", "redis", timeout=120)
            file_on_volume = "/payload/data/upgrade-admin/files/UpgradeRoot/recovery-note.txt"
            mutated = command([
                "docker", "run", "--rm", "--pull", "never", "--network", "none",
                "--mount", f"type=volume,source={project}_nextcloud-html,target=/payload",
                "--entrypoint", "python", IMAGES["archive"], "-c",
                "from pathlib import Path; import sys; "
                "p=Path(sys.argv[1]); assert p.is_file(); p.write_bytes(b'after-failed-upgrade')",
                file_on_volume,
            ], cwd=directory, timeout=30)
            require(mutated.returncode == 0, "post-checkpoint file mutation failed")

            # Never resume ingress on the half-migrated database. Destroy only
            # this marker-owned project's volumes, then create clean ones.
            assert_owned(project, directory, config_hash, complete=True)
            compose_run("down", "--volumes", "--remove-orphans", timeout=180)
            require(all(not listed(kind, project, directory)
                        for kind in ("container", "volume", "network")),
                    "failed fixture resources remain after teardown")
            replace_app(baseline, app_dir)
            require(ET.parse(app_dir / "appinfo/info.xml").getroot().findtext("version") ==
                    OLD_VERSION, "baseline application code was not restored")
            compose_run("create", "--no-build", timeout=180)
            assert_owned(project, directory, config_hash, complete=True)
            prefilled = {role: volume_entries(project, role, directory) > 0
                         for role in checksums}
            for role, digest in checksums.items():
                path = directory / (role + ".tar")
                require(sha256(path) == digest, "checkpoint archive checksum changed")
                clear_owned_volume(project, role, directory)
                restore_volume(project, role, path, directory)
            require(volume_owner(project, "db-data", directory) == db_owner,
                    "restored PostgreSQL volume ownership changed")
            for service_name in SERVICES:
                state = inspect("container", f"{project}-{service_name}-1", directory)
                require(state is not None and not state["State"]["Running"],
                        "application ingress opened before restore completed")
            compose_run("up", "-d", "--wait", "--wait-timeout", "300", timeout=360)
            require(occ("config:system:get", "instanceid") == instance_id,
                    "restored Nextcloud instance identity differs")
            require(occ("config:app:get", "integration_weknora", "installed_version") ==
                    OLD_VERSION, "restored app version differs from checkpoint")
            require(occ("config:app:get", "integration_weknora", "recovery_probe") ==
                    app_probe, "restored app configuration differs from checkpoint")
            require(occ("config:app:get", "integration_weknora", "bindings") == bindings,
                    "restored binding config differs from checkpoint")
            require(sql("SELECT state FROM oc_weknora_pub_state WHERE "
                        f"binding_id='recovery-probe' AND file_id={file_id};") == "withdrawn",
                    "restored publication row differs from checkpoint")
            require(occ("config:app:get", "integration_weknora", "enabled") == "yes",
                    "restored integration is not enabled")
            app_status = json.loads(occ("status", "--output=json"))
            require(app_status.get("installed") and not app_status.get("maintenance"),
                    "restored application remains unavailable")
            status, restored = dav(port, password, file_path)
            require(status == 200 and restored == content,
                    "restored core DAV file bytes differ from checkpoint")
            require(dav_id(port, password, file_path) == file_id and
                    dav_id(port, password, "UpgradeRoot") == root_id,
                    "restored source file identity differs from checkpoint")
            status, _ = dav(port, password, "", method="PROPFIND", depth="0")
            require(status == 207, "restored core DAV listing failed")
            assert_admin_binding_route(port, password, root_id)
            report = {
                "result": "passed", "project": project,
                "baseline_app": OLD_VERSION, "attempted_app": current_version,
                "fault": FAULT_MARKER, "failed_upgrade_exit": failed.returncode,
                "restored_instance_id_matches": True,
                "restored_file_id_matches": True,
                "restored_file_sha256": hashlib.sha256(restored).hexdigest(),
                "restored_plugin_config_and_state": True,
                "restored_admin_binding_route": True,
                "new_volume_prefilled_before_clear": prefilled,
                "checkpoint_archive_sha256": checksums,
            }
        finally:
            if started:
                assert_owned(project, directory, config_hash)
                compose_run("down", "--volumes", "--remove-orphans", timeout=180)
                require(all(not listed(kind, project, directory)
                            for kind in ("container", "volume", "network")),
                        "disposable recovery project cleanup incomplete")
        require(report is not None, "recovery drill did not produce a result")
        report["owned_resources_removed"] = True
        print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
