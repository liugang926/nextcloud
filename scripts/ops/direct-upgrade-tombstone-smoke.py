#!/usr/bin/env python3
"""Upgrade isolated Nextcloud 0.4.6 data and reject reused historical bindings.

Run from any directory after local Docker is available. This creates a new
Compose project with its own volumes, then removes that project in finally.
The fixed Git commit is the last 0.4.6 app; CI must check out full history.
"""

import base64
import hashlib
from http.cookiejar import CookieJar
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
OLD_APP_COMMIT = "5b97db539155be61541836ba3d67074e78705340"
OLD_IDS = (
    "retired-outbox", "retired-state", "retired-audit",
    "retired-floor", "retired-snapshot",
)


def command(args, *, env=None, input_text=None, label="command", timeout=660):
    result = subprocess.run(args, cwd=ROOT, env=env, input=input_text,
                            text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}")
    return result.stdout.strip()


def local_env():
    values = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"').strip("'")
    for key in ("NEXTCLOUD_ADMIN_USER", "NEXTCLOUD_ADMIN_PASSWORD",
                "NEXTCLOUD_DB_PASSWORD", "WEKNORA_SERVICE_TOKEN"):
        if not values.get(key):
            raise RuntimeError(f"missing {key} in local .env")
    return values


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http(opener, url, *, method="GET", headers=None, body=None):
    request = urllib.request.Request(url, data=body, method=method,
                                     headers=headers or {})
    try:
        with opener.open(request, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def login(base, user, password):
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()))
    status, body = http(opener, base + "/login")
    if status != 200:
        raise AssertionError(f"isolated login page returned HTTP {status}")
    match = re.search(rb'data-requesttoken="([^"]+)"', body)
    if match is None:
        raise AssertionError("isolated login page has no CSRF token")
    encoded = urllib.parse.urlencode({
        "requesttoken": match.group(1).decode(),
        "user": user,
        "password": password,
    }).encode()
    status, _ = http(opener, base + "/login", method="POST",
                     headers={"Origin": base}, body=encoded)
    if status != 200:
        raise AssertionError(f"isolated admin login returned HTTP {status}")
    status, body = http(opener, base + "/apps/dashboard/")
    match = re.search(rb'data-requesttoken="([^"]+)"', body)
    if status != 200 or match is None:
        raise AssertionError("isolated admin session has no CSRF token")
    return opener, match.group(1).decode()


def folder_id(base, user, password, name):
    auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    url = f"{base}/remote.php/dav/files/{urllib.parse.quote(user)}/{name}"
    opener = urllib.request.build_opener()
    status, _ = http(opener, url, method="MKCOL", headers={"Authorization": auth})
    if status != 201:
        raise AssertionError(f"isolated folder creation returned HTTP {status}")
    body = (b'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
            b'<d:prop><oc:fileid/></d:prop></d:propfind>')
    status, response = http(opener, url, method="PROPFIND", body=body,
                            headers={"Authorization": auth, "Depth": "0",
                                     "Content-Type": "application/xml"})
    if status != 207:
        raise AssertionError(f"isolated folder lookup returned HTTP {status}")
    node = ET.fromstring(response).find(".//{http://owncloud.org/ns}fileid")
    if node is None or node.text is None:
        raise AssertionError("isolated folder has no file ID")
    return int(node.text)


def verify_mount(compose, env, app_dir, port):
    config = json.loads(command(compose + ["config", "--format", "json"],
                                env=env, label="isolated Compose config"))
    service = config["services"]["nextcloud"]
    if not any(volume.get("source") == str(app_dir) and
               volume.get("target") == "/var/www/html/custom_apps/integration_weknora"
               for volume in service["volumes"]):
        raise AssertionError("isolated app mount did not override live source")
    if not any(str(item.get("published")) == str(port) for item in service["ports"]):
        raise AssertionError("isolated HTTP port did not override live port")


def run_case(case, values):
    project = "nc-upgrade-tombstone-" + secrets.token_hex(4)
    port = free_port()
    env = {**os.environ, "NEXTCLOUD_HTTP_PORT": str(port)}
    with tempfile.TemporaryDirectory(prefix="nc-upgrade-tombstone-") as tmp:
        temp = Path(tmp)
        archive = subprocess.run(
            ["git", "archive", OLD_APP_COMMIT, "apps/integration_weknora"],
            cwd=ROOT, capture_output=True)
        if archive.returncode:
            raise RuntimeError("fixed 0.4.6 commit unavailable; fetch full Git history")
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(temp)
        app_dir = temp / "apps/integration_weknora"
        old_info = (app_dir / "appinfo/info.xml").read_text()
        if "<version>0.4.6</version>" not in old_info:
            raise AssertionError("fixed baseline is not app 0.4.6")
        override = temp / "override.yaml"
        override.write_text(
            "services:\n  nextcloud:\n    volumes:\n"
            f"      - type: bind\n        source: {app_dir}\n"
            "        target: /var/www/html/custom_apps/integration_weknora\n"
            "        read_only: true\n")
        compose = ["docker", "compose", "-p", project,
                   "-f", str(ROOT / "compose.yaml"), "-f", str(override)]
        verify_mount(compose, env, app_dir, port)
        try:
            command(compose + ["up", "-d", "--wait", "--wait-timeout", "600",
                               "db", "redis", "nextcloud"], env=env,
                    label="isolated 0.4.6 stack")

            def occ(*args):
                return command(compose + ["exec", "-T", "-u", "www-data",
                                          "nextcloud", "php", "occ", *args],
                               env=env, label="isolated occ", timeout=180)

            def sql(statement):
                return command(compose + ["exec", "-T", "db", "psql", "-U",
                                          "nextcloud", "-d", "nextcloud", "-v",
                                          "ON_ERROR_STOP=1", "-At"], env=env,
                               input_text=statement, label="isolated SQL", timeout=60)

            occ("app:enable", "integration_weknora")
            base = f"http://127.0.0.1:{port}"
            user = values["NEXTCLOUD_ADMIN_USER"]
            old_root = folder_id(base, user, values["NEXTCLOUD_ADMIN_PASSWORD"],
                                 "UpgradeOld")
            current_root = folder_id(base, user, values["NEXTCLOUD_ADMIN_PASSWORD"],
                                     "UpgradeCurrent")

            def set_binding(binding_id, root_id):
                payload = json.dumps([{
                    "id": binding_id, "name": binding_id,
                    "owner_uid": user, "root_file_id": root_id,
                }], separators=(",", ":"))
                occ("config:app:set", "integration_weknora", "bindings",
                    "--value=" + payload)

            # This binding existed and was removed before the upgrade.
            set_binding("retired-state", old_root)
            token_hash = hashlib.sha256(values["WEKNORA_SERVICE_TOKEN"].encode()).hexdigest()
            occ("config:app:set", "integration_weknora", "service_token_sha256",
                "--value=" + token_hash)
            sql("""
INSERT INTO oc_weknora_outbox (binding_id, file_id, event_type, created_at)
VALUES ('retired-outbox', NULL, 'deleted', 1), ('upgrade-current', NULL, 'created', 1);
INSERT INTO oc_weknora_pub_state (binding_id, file_id, state, actor_uid, updated_at)
VALUES ('retired-state', 901, 'withdrawn', 'migration-test', 1);
INSERT INTO oc_weknora_pub_audit (binding_id, file_id, action, actor_uid, created_at)
VALUES ('retired-audit', 902, 'withdraw', 'migration-test', 1);
INSERT INTO oc_weknora_change_floor (binding_id, floor_id)
VALUES ('retired-floor', 1);
INSERT INTO oc_weknora_manifest_snap
  (snapshot_id, binding_id, generation, root_etag, publication_revision,
   items_json, created_at, format_version)
VALUES ('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        'retired-snapshot', repeat('0', 64), 'fixture', 0, '[]', 1, 1);
""")
            if case == "valid-current":
                set_binding("upgrade-current", current_root)
            else:
                # A malformed current config must still retire history.
                occ("config:app:set", "integration_weknora", "bindings",
                    "--value={invalid-json")

            source = ROOT / "apps/integration_weknora"
            for name in ("appinfo", "lib", "css", "js", "templates"):
                shutil.copytree(source / name, app_dir / name, dirs_exist_ok=True)
            if "<version>0.4.17</version>" not in (app_dir / "appinfo/info.xml").read_text():
                raise AssertionError("upgrade app is not version 0.4.17")
            occ("upgrade")

            if sql("SELECT COUNT(*) FROM pg_tables WHERE tablename = 'oc_weknora_src_pair';") != "1":
                raise AssertionError("source pairing migration was not applied")
            if sql("SELECT COUNT(*) FROM pg_tables WHERE tablename = 'oc_weknora_src_pair_rot';") != "1":
                raise AssertionError("source rotation migration was not applied")
            if sql("SELECT COUNT(*) FROM information_schema.columns "
                   "WHERE table_name = 'oc_weknora_machine_key' AND column_name = 'expires_at';") != "1":
                raise AssertionError("source rotation key expiry migration was not applied")
            if sql("SELECT COUNT(*) FROM pg_tables WHERE tablename = 'oc_weknora_event_conn';") != "1":
                raise AssertionError("direct upgrade did not create the event sender table")
            for column in ("applied_id", "applied_checked_at", "applied_error_code"):
                if sql("SELECT COUNT(*) FROM information_schema.columns "
                       f"WHERE table_name = 'oc_weknora_event_conn' AND column_name = '{column}';") != "1":
                    raise AssertionError(f"direct upgrade did not create event sender {column}")
            if sql("SELECT COUNT(*) FROM pg_tables WHERE tablename = 'oc_weknora_bind_pub_audit';") != "1":
                raise AssertionError("direct upgrade did not create the binding publication audit table")
            for table, column in (("oc_weknora_binding_id", "publication_state"),
                                  ("oc_weknora_binding_id", "publication_epoch"),
                                  ("oc_weknora_manifest_snap", "binding_epoch")):
                count = sql("SELECT COUNT(*) FROM information_schema.columns "
                            f"WHERE table_name = '{table}' AND column_name = '{column}';")
                if count != "1":
                    raise AssertionError(f"direct upgrade did not create {table}.{column}")

            rows = sql("SELECT binding_id, retired_at FROM oc_weknora_binding_id ORDER BY binding_id;")
            states = {name: int(retired) for name, retired in
                      (line.split("|", 1) for line in rows.splitlines() if "|" in line)}
            for binding_id in OLD_IDS:
                if states.get(binding_id, 0) <= 0:
                    raise AssertionError(f"{case}: {binding_id} has no retirement tombstone")
            if case == "valid-current":
                if states.get("upgrade-current") != 0:
                    raise AssertionError("current binding was retired")
                gate = sql("SELECT publication_state || '|' || publication_epoch "
                           "FROM oc_weknora_binding_id WHERE binding_id = 'upgrade-current';")
                if gate != "active|0":
                    raise AssertionError(f"existing binding gate was not backfilled active|0: {gate}")
                match = sql("""SELECT COUNT(*) FROM oc_weknora_machine_key k
JOIN oc_weknora_binding_id b USING (binding_id)
WHERE k.key_id = 'default' AND k.binding_id = 'upgrade-current'
  AND k.source_hash = b.source_hash AND b.retired_at = 0;""")
                if match != "1":
                    raise AssertionError("current legacy machine key was not preserved")
            else:
                # Repair only after migration; old IDs must stay retired.
                occ("config:app:set", "integration_weknora", "bindings", "--value=[]")

            command(compose + ["restart", "nextcloud"], env=env,
                    label="isolated Nextcloud restart", timeout=120)
            command(compose + ["up", "-d", "--wait", "--wait-timeout", "180",
                               "nextcloud"], env=env, label="isolated HTTP readiness")
            admin, csrf = login(base, user, values["NEXTCLOUD_ADMIN_PASSWORD"])
            api = base + "/index.php/apps/integration_weknora/api/v1"

            def save(binding_id):
                body = json.dumps({
                    "id": binding_id, "name": binding_id,
                    "owner_uid": user, "root_file_id": old_root,
                }).encode()
                status, _ = http(admin, api + "/admin/bindings", method="POST",
                                 body=body, headers={"requesttoken": csrf,
                                                     "Content-Type": "application/json"})
                return status

            for binding_id in OLD_IDS:
                status = save(binding_id)
                if status != 409:
                    raise AssertionError(f"{case}: {binding_id} reused with HTTP {status}")
            if save("upgrade-fresh") != 201:
                raise AssertionError(f"{case}: fresh binding could not be created")
            fresh_gate = sql("SELECT publication_state || '|' || publication_epoch "
                             "FROM oc_weknora_binding_id WHERE binding_id = 'upgrade-fresh';")
            if fresh_gate != "active|0":
                raise AssertionError(f"new binding gate was not active|0: {fresh_gate}")
            print(f"{case}: direct 0.4.6→0.4.17 upgrade tombstone smoke passed")
        finally:
            # The project name is random and every volume belongs to this
            # disposable Compose instance; existing development volumes stay.
            command(compose + ["down", "-v", "--remove-orphans"], env=env,
                    label="isolated Compose teardown", timeout=180)


def main():
    values = local_env()
    for case in ("valid-current", "invalid-config"):
        run_case(case, values)


if __name__ == "__main__":
    main()
