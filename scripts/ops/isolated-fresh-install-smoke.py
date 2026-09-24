#!/usr/bin/env python3
"""Install the packaged app on a disposable, empty Nextcloud Docker stack.

The fixture has unique volumes and a loopback-only HTTP listener. It does not
reuse the development stack's database, credentials, or app bind mount.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apps/integration_weknora"
APP_INFO = APP / "appinfo/info.xml"
NEXTCLOUD_IMAGE = (
    "nextcloud:34.0.4-apache@sha256:"
    "a5ace30c695afe48c2c406e940ee7886a81e13fa382e57cd68b2416d1a66914c"
)
POSTGRES_IMAGE = (
    "postgres:16-alpine@sha256:"
    "20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416"
)
REDIS_IMAGE = (
    "redis:7-alpine@sha256:"
    "8b81dd37ff027bec4e516d41acfbe9fe2460070dc6d4a4570a2ac5b9d59df065"
)


def run(*args: str, timeout: int = 600) -> str:
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1800:]
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {detail}")
    return result.stdout.strip()


def free_loopback_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def compose_text(port: int, db_password: str, user: str, password: str) -> str:
    # JSON is a valid YAML document and avoids interpolating shell or Compose
    # syntax into fixture credentials.
    return json.dumps({
        "services": {
            "db": {
                "image": POSTGRES_IMAGE,
                "environment": {
                    "POSTGRES_DB": "nextcloud",
                    "POSTGRES_USER": "nextcloud",
                    "POSTGRES_PASSWORD": db_password,
                },
                "volumes": ["db-data:/var/lib/postgresql/data"],
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U nextcloud -d nextcloud"],
                    "interval": "5s", "timeout": "5s", "retries": 30,
                },
            },
            "redis": {
                "image": REDIS_IMAGE,
                "command": ["redis-server", "--appendonly", "yes"],
                "volumes": ["redis-data:/data"],
                "healthcheck": {
                    "test": ["CMD", "redis-cli", "ping"],
                    "interval": "5s", "timeout": "3s", "retries": 20,
                },
            },
            "nextcloud": {
                "image": NEXTCLOUD_IMAGE,
                "ports": [{"target": 80, "published": port, "host_ip": "127.0.0.1"}],
                "environment": {
                    "POSTGRES_HOST": "db", "POSTGRES_DB": "nextcloud",
                    "POSTGRES_USER": "nextcloud", "POSTGRES_PASSWORD": db_password,
                    "NEXTCLOUD_ADMIN_USER": user,
                    "NEXTCLOUD_ADMIN_PASSWORD": password,
                    "NEXTCLOUD_TRUSTED_DOMAINS": "localhost 127.0.0.1",
                    "REDIS_HOST": "redis",
                },
                "volumes": ["nextcloud-html:/var/www/html"],
                "depends_on": {
                    "db": {"condition": "service_healthy"},
                    "redis": {"condition": "service_healthy"},
                },
                "healthcheck": {
                    "test": ["CMD-SHELL", "curl -fsS http://127.0.0.1/status.php >/dev/null"],
                    "interval": "10s", "timeout": "5s", "retries": 40,
                    "start_period": "60s",
                },
            },
        },
        "volumes": {"db-data": {}, "redis-data": {}, "nextcloud-html": {}},
    }, indent=2) + "\n"


def main() -> None:
    version = ET.parse(APP_INFO).getroot().findtext("version")
    if not version or not all(part.isdigit() for part in version.split(".")):
        raise RuntimeError("invalid app version")
    archive = ROOT / "dist" / f"integration_weknora-{version}.tar.gz"
    if not archive.is_file():
        raise RuntimeError(f"build the app package first: {archive}")

    project = "nc-fresh-install-" + secrets.token_hex(4)
    port = free_loopback_port()
    user = "fresh-admin"
    password = secrets.token_urlsafe(30)
    with tempfile.TemporaryDirectory(prefix="nc-fresh-install-") as tmp:
        config = Path(tmp) / "compose.yaml"
        config.write_text(compose_text(port, secrets.token_urlsafe(30), user, password))
        config.chmod(0o600)
        compose = ("docker", "compose", "-p", project, "-f", str(config))
        started = False
        try:
            rendered = json.loads(run(*compose, "config", "--format", "json"))
            ports = rendered["services"]["nextcloud"]["ports"]
            if len(ports) != 1 or ports[0].get("host_ip") != "127.0.0.1":
                raise RuntimeError("fixture HTTP listener is not loopback-only")
            volumes = rendered["services"]["nextcloud"]["volumes"]
            if any(item.get("type") == "bind" for item in volumes):
                raise RuntimeError("fixture unexpectedly bind mounts host files")

            started = True
            run(*compose, "up", "-d", "--wait", "--wait-timeout", "600", timeout=660)
            container = run(*compose, "ps", "-q", "nextcloud")
            if not container:
                raise RuntimeError("Nextcloud container missing")
            run("docker", "cp", str(archive), f"{container}:/tmp/app.tar.gz")
            run(*compose, "exec", "-T", "nextcloud", "tar", "-xzf",
                "/tmp/app.tar.gz", "-C", "/var/www/html/custom_apps")
            run(*compose, "exec", "-T", "nextcloud", "chown", "-R",
                "www-data:www-data", "/var/www/html/custom_apps/integration_weknora")
            run(*compose, "exec", "-T", "-u", "www-data", "nextcloud",
                "php", "occ", "app:enable", "integration_weknora", timeout=180)

            installed = run(*compose, "exec", "-T", "-u", "www-data", "nextcloud",
                            "php", "occ", "config:app:get", "integration_weknora", "installed_version")
            if installed != version:
                raise AssertionError(f"installed version {installed!r} != package {version!r}")
            status = json.loads(run(*compose, "exec", "-T", "-u", "www-data", "nextcloud",
                                    "php", "occ", "status", "--output=json"))
            if not status.get("installed") or status.get("maintenance"):
                raise AssertionError("fresh Nextcloud app is not ready")
            for table in ("oc_weknora_binding_id", "oc_weknora_src_pair",
                          "oc_weknora_event_conn"):
                query = "SELECT COUNT(*) FROM pg_tables WHERE tablename = '" + table + "';"
                count = run(*compose, "exec", "-T", "db", "psql", "-U", "nextcloud",
                            "-d", "nextcloud", "-At", "-c", query)
                if count != "1":
                    raise AssertionError(f"fresh migration did not create {table}")

            auth = base64.b64encode(f"{user}:{password}".encode()).decode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/remote.php/dav/files/{user}/",
                method="PROPFIND", headers={"Authorization": "Basic " + auth, "Depth": "0"})
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status != 207:
                    raise AssertionError(f"fresh WebDAV status {response.status}")
            print(f"fresh install passed: app {version}, migrations, authenticated DAV 207")
        finally:
            if started:
                run(*compose, "down", "--volumes", "--remove-orphans", timeout=180)


if __name__ == "__main__":
    main()
