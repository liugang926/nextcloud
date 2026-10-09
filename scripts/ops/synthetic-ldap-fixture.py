#!/usr/bin/env python3
"""Create and own a disposable, loopback-only AD-shaped LDAP Compose fixture.

The generator writes secrets only under a new 0700 scratch directory. It does
not modify existing Compose projects. `destroy` accepts only its own marker.
The application setup and cross-system permission matrix are described in
docs/synthetic-ldap-compose.md.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[2]
MARKER = "nextcloud-weknora-synthetic-ldap-v1"
OWNER_LABEL = "org.nextcloud.weknora.synthetic-fixture.owner"
SERVICES = frozenset(("openldap", "cert-init", "nc-db", "nc-redis", "nextcloud",
                      "wk-db", "wk-redis", "docreader", "mock-embedding", "wk-app"))
VOLUMES = frozenset(("ldap-certs", "ldap-fixture", "nc-postgres", "nc-redis-data",
                     "nc-html", "wk-postgres", "wk-data", "docreader-tmp"))
BODY_VOLUMES = frozenset(("wk-body-journal", "wk-body-key", "wk-body-pin"))
NORMAL_TRIAL_MEMORY_MIB = {"wk-app":4096,"nextcloud":768,"wk-db":512,"nc-db":256,
                         "docreader":256,"openldap":128,"cert-init":64,"mock-embedding":128,
                         "nc-redis":64,"wk-redis":64,"wk-ui":64}
SHORT_PRIMARY_ENV = {"DOCREADER_PDF_FORCE_SCANNED": "1"}
LDAP_IMAGE = ("bitnamilegacy/openldap:2.6.10-debian-12-r4@"
              "sha256:966fd39ed25813890e9bd57dac56def163bbcfe64967e0bae59ab018d505bd93")
NC_IMAGE = ("nextcloud:34.0.4-apache@"
            "sha256:a5ace30c695afe48c2c406e940ee7886a81e13fa382e57cd68b2416d1a66914c")
POSTGRES_IMAGE = ("postgres:16-alpine@"
                  "sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416")
WK_POSTGRES_IMAGE = ("paradedb/paradedb:v0.22.6-pg17@"
                     "sha256:58bf87d2a6f1e72f56590b0f0ae1467e82c3f99ca203dde646672c8ac13148b2")
REDIS_IMAGE = ("redis:7-alpine@"
               "sha256:8b81dd37ff027bec4e516d41acfbe9fe2460070dc6d4a4570a2ac5b9d59df065")
DOCREADER_IMAGE = ("wechatopenai/weknora-docreader:latest@"
                   "sha256:59e26f98f17c296c5f4bd216404558f51d31991b580cfef1d204903a01e4b46b")
PYTHON_IMAGE = ("python:3.12-alpine@"
                "sha256:4c47124a8391cb7a9f571164147d154777cf012a4ece5f86097130d7a4478111")
BASE = "dc=example,dc=test"
PEOPLE = "ou=people," + BASE
GROUPS = "ou=groups," + BASE
SERVICE = "ou=service," + BASE
READER_DN = "cn=directory-reader," + SERVICE
A_DN = "cn=Alice Synthetic," + PEOPLE
B_DN = "cn=Bob Synthetic," + PEOPLE
DISABLED_DN = "cn=Charlie Disabled," + PEOPLE
DOMAIN_DN = "cn=Domain Users," + GROUPS
GRANT_DN = "cn=Engineering," + GROUPS
CHILD_DN = "cn=Platform," + GROUPS


SCHEMA = """dn: cn=adtest,cn=schema,cn=config
objectClass: olcSchemaConfig
cn: adtest
olcAttributeTypes: ( 1.3.6.1.4.1.4203.666.11.9.1 NAME 'objectGUID' EQUALITY octetStringMatch SYNTAX 1.3.6.1.4.1.1466.115.121.1.40 SINGLE-VALUE )
olcAttributeTypes: ( 1.3.6.1.4.1.4203.666.11.9.2 NAME 'objectSid' EQUALITY octetStringMatch SYNTAX 1.3.6.1.4.1.1466.115.121.1.40 SINGLE-VALUE )
olcAttributeTypes: ( 1.3.6.1.4.1.4203.666.11.9.3 NAME 'sAMAccountName' EQUALITY caseIgnoreMatch SUBSTR caseIgnoreSubstringsMatch SYNTAX 1.3.6.1.4.1.1466.115.121.1.15 SINGLE-VALUE )
olcAttributeTypes: ( 1.3.6.1.4.1.4203.666.11.9.4 NAME 'userPrincipalName' EQUALITY caseIgnoreMatch SUBSTR caseIgnoreSubstringsMatch SYNTAX 1.3.6.1.4.1.1466.115.121.1.15 SINGLE-VALUE )
olcAttributeTypes: ( 1.3.6.1.4.1.4203.666.11.9.5 NAME 'userAccountControl' EQUALITY integerMatch ORDERING integerOrderingMatch SYNTAX 1.3.6.1.4.1.1466.115.121.1.27 SINGLE-VALUE )
olcAttributeTypes: ( 1.3.6.1.4.1.4203.666.11.9.6 NAME 'primaryGroupID' EQUALITY integerMatch ORDERING integerOrderingMatch SYNTAX 1.3.6.1.4.1.1466.115.121.1.27 SINGLE-VALUE )
olcAttributeTypes: ( 1.3.6.1.4.1.4203.666.11.9.7 NAME 'primaryGroupToken' EQUALITY integerMatch ORDERING integerOrderingMatch SYNTAX 1.3.6.1.4.1.1466.115.121.1.27 SINGLE-VALUE )
olcObjectClasses: ( 1.3.6.1.4.1.4203.666.11.9.20 NAME 'adTestUser' SUP top AUXILIARY MUST ( objectGUID $ objectSid $ sAMAccountName $ userPrincipalName $ userAccountControl $ primaryGroupID ) )
olcObjectClasses: ( 1.3.6.1.4.1.4203.666.11.9.21 NAME 'adTestGroup' SUP top AUXILIARY MUST ( objectGUID $ objectSid $ primaryGroupToken ) MAY ( sAMAccountName $ displayName $ mail ) )
olcObjectClasses: ( 1.3.6.1.4.1.4203.666.11.9.22 NAME 'adTestDomain' SUP top AUXILIARY MUST objectSid )
"""


def write_private(path, content):
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def sid(domain_parts, rid=None):
    parts = domain_parts if rid is None else domain_parts + (rid,)
    return bytes((1, len(parts))) + (5).to_bytes(6, "big") + b"".join(
        part.to_bytes(4, "little") for part in parts)


def binary(value):
    return base64.b64encode(value).decode("ascii")


def person(dn, name, username, password, guid, object_sid, primary_rid, enabled):
    return "\n".join([
        "dn: " + dn, "objectClass: top", "objectClass: person",
        "objectClass: organizationalPerson", "objectClass: inetOrgPerson",
        "objectClass: adTestUser", "cn: " + name, "sn: Synthetic",
        "displayName: " + name, "uid: " + username,
        "mail: " + username + "@example.test",
        "sAMAccountName: " + username,
        "userPrincipalName: " + username + "@example.test",
        "userPassword: " + password,
        "objectGUID:: " + binary(uuid.UUID(guid).bytes_le),
        "objectSid:: " + binary(object_sid),
        "userAccountControl: " + ("512" if enabled else "514"),
        "primaryGroupID: " + str(primary_rid), "", "",
    ])


def group(dn, name, guid, object_sid, members, *, unique=False):
    kind = "groupOfUniqueNames" if unique else "groupOfNames"
    member_attr = "uniqueMember" if unique else "member"
    return "\n".join([
        "dn: " + dn, "objectClass: top", "objectClass: " + kind,
        "objectClass: adTestGroup", "cn: " + name,
        "displayName: " + name, "sAMAccountName: " + name,
        *(member_attr + ": " + member for member in members),
        "objectGUID:: " + binary(uuid.UUID(guid).bytes_le),
        "objectSid:: " + binary(object_sid),
        "primaryGroupToken: " + str(int.from_bytes(object_sid[-4:], "little")), "", "",
    ])


def make_ldif(mode, passwords, guids, domain_parts):
    primary_a = 2000 if mode == "primary" else 513
    grant_members = ([A_DN] if mode == "direct" else
                     [CHILD_DN] if mode == "nested" else [DISABLED_DN])
    rows = [
        "dn: " + BASE + "\nobjectClass: top\nobjectClass: domain\n"
        "objectClass: adTestDomain\ndc: example\nobjectSid:: " +
        binary(sid(domain_parts)) + "\n\n",
        *("dn: " + dn + "\nobjectClass: top\nobjectClass: organizationalUnit\nou: " +
          dn.split(",", 1)[0].split("=", 1)[1] + "\n\n"
          for dn in (SERVICE, PEOPLE, GROUPS)),
        "dn: " + READER_DN + "\nobjectClass: top\nobjectClass: person\n"
        "objectClass: organizationalPerson\nobjectClass: inetOrgPerson\n"
        "cn: directory-reader\nsn: reader\nuid: directory-reader\n"
        "userPassword: " + passwords["ldap_bind"] + "\n\n",
        person(A_DN, "Alice Synthetic", "alice", passwords["alice"], guids["alice"],
               sid(domain_parts, 1100), primary_a, True),
        person(B_DN, "Bob Synthetic", "bob", passwords["bob"], guids["bob"],
               sid(domain_parts, 1101), 513, True),
        person(DISABLED_DN, "Charlie Disabled", "charlie", passwords["charlie"],
               guids["charlie"], sid(domain_parts, 1102), 513, False),
        group(DOMAIN_DN, "Domain Users", guids["domain"], sid(domain_parts, 513),
              [READER_DN], unique=True),
        group(GRANT_DN, "Engineering", guids["grant"], sid(domain_parts, 2000),
              grant_members),
    ]
    if mode == "nested":
        rows.append(group(CHILD_DN, "Platform", guids["child"],
                          sid(domain_parts, 2001), [A_DN]))
    return "".join(rows)


def generate_certs(directory):
    certs = directory / "certs"
    certs.mkdir(mode=0o700)
    ext = certs / "server-ext.cnf"
    write_private(ext, "subjectAltName = DNS:openldap,IP:127.0.0.1\nextendedKeyUsage = serverAuth\n")
    commands = [
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "2", "-nodes",
         "-subj", "/CN=Synthetic LDAP Test CA", "-keyout", str(certs / "ca.key"),
         "-out", str(certs / "ca.crt")],
        ["openssl", "req", "-newkey", "rsa:2048", "-sha256", "-nodes", "-subj", "/CN=openldap",
         "-keyout", str(certs / "server.key"), "-out", str(certs / "server.csr")],
        ["openssl", "x509", "-req", "-sha256", "-days", "2", "-in", str(certs / "server.csr"),
         "-CA", str(certs / "ca.crt"), "-CAkey", str(certs / "ca.key"),
         "-CAserial", str(certs / "ca.srl"), "-CAcreateserial", "-extfile", str(ext),
         "-out", str(certs / "server.crt")],
    ]
    for command in commands:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    for path in certs.iterdir():
        path.chmod(0o600)
    # The CA certificate is public material mounted into app containers.
    (certs / "ca.crt").chmod(0o644)


def compose_data(directory, project, ports, passwords, image, ui_image=None, *, owner_token, body_journal=False):
    certs, schema, ldifs = directory / "certs", directory / "schema", directory / "ldif"
    base_env = {"POSTGRES_DB": "nextcloud", "POSTGRES_USER": "nextcloud",
                "POSTGRES_PASSWORD": passwords["nc_db"]}
    wk_db_env = {"POSTGRES_DB": "weknora", "POSTGRES_USER": "weknora",
                 "POSTGRES_PASSWORD": passwords["wk_db"]}
    ldap_env = {
        "LDAP_ROOT": BASE, "LDAP_ADMIN_USERNAME": "admin",
        "LDAP_ADMIN_PASSWORD": passwords["ldap_admin"],
        "LDAP_CONFIG_ADMIN_ENABLED": "no", "LDAP_SKIP_DEFAULT_TREE": "yes",
        "LDAP_ALLOW_ANON_BINDING": "no", "LDAP_CUSTOM_SCHEMA_FILE": "/fixture/schema/ad-test.ldif",
        "LDAP_CUSTOM_LDIF_DIR": "/fixture/ldif", "LDAP_ENABLE_TLS": "yes",
        "LDAP_REQUIRE_TLS": "no", "LDAP_PORT_NUMBER": "1389",
        "LDAP_LDAPS_PORT_NUMBER": "1636", "LDAP_TLS_CERT_FILE": "/certs/server.crt",
        "LDAP_TLS_KEY_FILE": "/certs/server.key", "LDAP_TLS_CA_FILE": "/certs/ca.crt",
    }
    nc_env = {**base_env, "POSTGRES_HOST": "nc-db", "REDIS_HOST": "nc-redis",
              "NEXTCLOUD_ADMIN_USER": "devadmin", "NEXTCLOUD_ADMIN_PASSWORD": passwords["nc_admin"],
              "NEXTCLOUD_TRUSTED_DOMAINS": "127.0.0.1 localhost nextcloud",
              "WEKNORA_LOCAL_COMPOSE": "1", "WEKNORA_EVENT_DEV_HTTP": "1",
              "WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY": "0",
              "WEKNORA_EVENT_ALLOWED_ORIGINS": "http://wk-app:8080",
              "LDAPTLS_CACERT": "/run/secrets/ldap-ca.pem", "PHP_MEMORY_LIMIT": "512M"}
    wk_env = {
        "DB_DRIVER": "postgres", "DB_HOST": "wk-db", "DB_PORT": "5432",
        "DB_USER": "weknora", "DB_PASSWORD": passwords["wk_db"], "DB_NAME": "weknora",
        "RETRIEVE_DRIVER": "postgres", "REDIS_ADDR": "wk-redis:6379",
        "JWT_SECRET": passwords["jwt"], "SYSTEM_AES_KEY": passwords["aes"],
        "DOCREADER_ADDR": "docreader:50051", "STORAGE_TYPE": "local",
        "LOCAL_STORAGE_BASE_DIR": "/data/files", "AUTO_MIGRATE": "true",
        "WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL": "synthetic-admin@example.test",
        "WEKNORA_NEXTCLOUD_DEV_HTTP": "1",
        "WEKNORA_NEXTCLOUD_ALLOWED_ORIGINS": "http://nextcloud",
        "SSRF_WHITELIST_EXTRA": "nextcloud,mock-embedding",
        "LDAP_ENABLED": "true", "LDAP_CONFIG_SOURCE": "file",
        "LDAP_DIRECTORY_ID": "synthetic-ad", "LDAP_URLS": "ldaps://openldap:1636",
        "LDAP_TLS_MODE": "ldaps", "LDAP_SERVER_NAMES": "openldap",
        "LDAP_CA_FILE": "/run/secrets/ldap-ca.pem",
        "LDAP_BIND_DN": READER_DN, "LDAP_BIND_PASSWORD": passwords["ldap_bind"],
        "LDAP_BASE_DN": BASE, "LDAP_USER_BASE_DN": PEOPLE,
        "LDAP_GROUP_BASE_DN": GROUPS,
        "LDAP_USER_FILTER": "(&(objectClass=adTestUser)(objectClass=person))",
        "LDAP_GROUP_FILTER": "(objectClass=adTestGroup)",
        "LDAP_LOGIN_FILTER": "(&(objectClass=adTestUser)(|(sAMAccountName={login})(userPrincipalName={login})))",
        "LDAP_SYNC_INTERVAL": "15s", "LDAP_STALE_AFTER": "2m",
    }
    result = {
        "name": project,
        "services": {
            "openldap": {"image": LDAP_IMAGE, "hostname": "openldap", "environment": ldap_env,
                         "ports": [f"127.0.0.1:{ports['ldap']}:1636"],
                         "volumes": ["ldap-fixture:/fixture:ro", "ldap-certs:/certs:ro"],
                         "depends_on": {"cert-init": {"condition": "service_completed_successfully"}},
                         "healthcheck": {"test": ["CMD-SHELL",
                             "/opt/bitnami/openldap/bin/ldapsearch -x -H ldap://127.0.0.1:1389 "
                             "-D '" + READER_DN + "' -w '" + passwords["ldap_bind"] +
                             "' -b '" + BASE + "' -s base '(objectClass=*)' dn >/dev/null"],
                             "interval": "3s", "timeout": "5s", "retries": 35,
                             "start_period": "5s"}},
            "cert-init": {"image": PYTHON_IMAGE, "user": "0:0",
                          "command": ["sh", "-c",
                              "cp /input-certs/ca.crt /input-certs/server.crt /input-certs/server.key /output-certs/ && "
                              "mkdir -p /output-fixture/schema /output-fixture/ldif && "
                              "cp /input-schema/ad-test.ldif /output-fixture/schema/ && "
                              "cp /input-ldif/01-directory.ldif /output-fixture/ldif/ && "
                              "chown -R 1001:1001 /output-certs /output-fixture && "
                              "chmod 700 /output-certs /output-fixture /output-fixture/schema /output-fixture/ldif && "
                              "chmod 600 /output-certs/* /output-fixture/schema/* /output-fixture/ldif/*"],
                          "volumes": [f"{certs}:/input-certs:ro", f"{schema}:/input-schema:ro",
                                      f"{ldifs}:/input-ldif:ro", "ldap-certs:/output-certs",
                                      "ldap-fixture:/output-fixture"]},
            "nc-db": {"image": POSTGRES_IMAGE, "environment": base_env,
                      "volumes": ["nc-postgres:/var/lib/postgresql/data"],
                      "healthcheck": {"test": ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U nextcloud -d nextcloud"],
                                      "interval": "5s", "timeout": "5s", "retries": 30}},
            "nc-redis": {"image": REDIS_IMAGE, "command": ["redis-server", "--appendonly", "yes"],
                         "volumes": ["nc-redis-data:/data"],
                         "healthcheck": {"test": ["CMD", "redis-cli", "ping"],
                                         "interval": "5s", "timeout": "3s", "retries": 20}},
            "nextcloud": {"image": NC_IMAGE,
                          "ports": [f"127.0.0.1:{ports['nextcloud']}:80"],
                          "environment": nc_env,
                          "volumes": ["nc-html:/var/www/html",
                                      f"{ROOT / 'apps/integration_weknora'}:/var/www/html/custom_apps/integration_weknora:ro",
                                      f"{certs / 'ca.crt'}:/run/secrets/ldap-ca.pem:ro"],
                          "depends_on": {"nc-db": {"condition": "service_healthy"},
                                         "nc-redis": {"condition": "service_healthy"},
                                         "openldap": {"condition": "service_healthy"}},
                          "healthcheck": {"test": ["CMD-SHELL", "curl -fsS http://127.0.0.1/status.php >/dev/null"],
                                          "interval": "10s", "timeout": "5s", "retries": 40,
                                          "start_period": "60s"}},
            "wk-db": {"image": WK_POSTGRES_IMAGE, "environment": wk_db_env,
                      "volumes": ["wk-postgres:/var/lib/postgresql/data"],
                      "healthcheck": {"test": ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U weknora -d weknora"],
                                      "interval": "5s", "timeout": "5s", "retries": 30}},
            "wk-redis": {"image": REDIS_IMAGE,
                         "healthcheck": {"test": ["CMD", "redis-cli", "ping"],
                                         "interval": "5s", "timeout": "3s", "retries": 20}},
            "docreader": {"image": DOCREADER_IMAGE,
                          "volumes": ["docreader-tmp:/tmp/docreader"]},
            "mock-embedding": {"image": PYTHON_IMAGE,
                               "command": ["python", "/srv/mock_embedding.py"],
                               "volumes": [f"{ROOT / 'integration/mock_embedding.py'}:/srv/mock_embedding.py:ro"],
                               "healthcheck": {"test": ["CMD", "python", "-c",
                                   "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"],
                                   "interval": "5s", "timeout": "3s", "retries": 20}},
            "wk-app": {"image": image, "ports": [f"127.0.0.1:{ports['weknora']}:8080"],
                       "environment": wk_env,
                       "volumes": ["wk-data:/data/files", "docreader-tmp:/tmp/docreader:ro",
                                   f"{certs / 'ca.crt'}:/run/secrets/ldap-ca.pem:ro"],
                       "depends_on": {"wk-db": {"condition": "service_healthy"},
                                      "wk-redis": {"condition": "service_healthy"},
                                      "openldap": {"condition": "service_healthy"}},
                       "healthcheck": {"test": ["CMD", "curl", "-fsS", "http://127.0.0.1:8080/health"],
                                       "interval": "10s", "timeout": "5s", "retries": 40,
                                       "start_period": "60s"}},
            **({"wk-ui": {
                "image": ui_image,
                "ports": [f"127.0.0.1:{ports['weknora_ui']}:80"],
                "environment": {"APP_HOST": "wk-app", "APP_PORT": "8080",
                                "APP_SCHEME": "http", "MAX_FILE_SIZE": "50M",
                                "MAX_SKILL_BUNDLE_SIZE": "100M"},
                "depends_on": {"wk-app": {"condition": "service_healthy"}},
            }} if ui_image else {}),
        },
        "volumes": {name: {} for name in VOLUMES},
    }
    if body_journal:
        app = result["services"]["wk-app"]
        app["environment"].update({
            "WEKNORA_ORIGINAL_BODY_JOURNAL_DIRECTORY": "/var/lib/weknora-body-ledger",
            "WEKNORA_ORIGINAL_BODY_JOURNAL_KEY": "/var/lib/weknora-body-key/hmac.key",
            "WEKNORA_ORIGINAL_BODY_JOURNAL_PIN": "/var/lib/weknora-body-pin/pin.json",
        })
        app["volumes"].extend([
            "wk-body-journal:/var/lib/weknora-body-ledger",
            "wk-body-key:/var/lib/weknora-body-key",
            "wk-body-pin:/var/lib/weknora-body-pin",
        ])
        result["volumes"].update({name: {} for name in BODY_VOLUMES})
    for service in result["services"].values():
        service["labels"] = {OWNER_LABEL: owner_token}
    for volume in result["volumes"].values():
        volume["labels"] = {OWNER_LABEL: owner_token}
    result["networks"] = {"default": {"labels": {OWNER_LABEL: owner_token}}}
    return result


def compose_fingerprint(config):
    """Allow the PDF drill's one controlled private DocReader override."""
    normalized = json.loads(json.dumps(config))
    docreader = normalized.get("services", {}).get("docreader", {})
    if docreader.get("environment") == SHORT_PRIMARY_ENV:
        del docreader["environment"]
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def docker_names(kind, project=None):
    if kind == "container":
        command = ["docker", "ps", "-a"]
        template = "{{.Names}}"
    elif kind == "volume":
        command = ["docker", "volume", "ls"]
        template = "{{.Name}}"
    elif kind == "network":
        command = ["docker", "network", "ls"]
        template = "{{.Name}}"
    else:
        raise ValueError("unknown Docker resource type")
    if project:
        command.extend(["--filter", "label=com.docker.compose.project=" + project])
    command.extend(["--format", template])
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    return set(result.stdout.splitlines())


def project_resources(project):
    return {kind: docker_names(kind, project) for kind in
            ("container", "volume", "network")}


def assert_project_unoccupied(project):
    if any(project_resources(project).values()):
        return False
    # A manually named or unlabeled resource can occupy a Compose name too.
    prefixes = {"container": project + "-", "volume": project + "_",
                "network": project + "_"}
    return all(not any(name.startswith(prefix) for name in docker_names(kind))
               for kind, prefix in prefixes.items())


def docker_inspect(kind, name):
    command = ["docker", kind, "inspect", name]
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    items = json.loads(result.stdout)
    if len(items) != 1:
        raise RuntimeError("Docker resource inspection was ambiguous")
    return items[0]


class PrefixResourceMismatch(RuntimeError):
    """Two resource inventories disagree; mutation callers still fail closed."""
    def __init__(self, kind, names):
        super().__init__("unlabeled resource occupies synthetic Compose project name")
        self.kind, self.names = kind, tuple(sorted(names))


def assert_owned_resources(directory, state, *, require_empty=False):
    """Fail closed before Compose can modify a project or remove its volumes."""
    project, token = state["project"], state["owner_token"]
    services = SERVICES | ({"wk-ui"} if state.get("weknora_ui_image") else set())
    expected_volumes = {project + "_" + name for name in VOLUMES | (BODY_VOLUMES if state.get("body_journal") else set())}
    expected_network = project + "_default"
    resources = project_resources(project)
    prefixes = {"container": project + "-", "volume": project + "_",
                "network": project + "_"}
    for kind, prefix in prefixes.items():
        foreign = {name for name in docker_names(kind) if name.startswith(prefix)} - resources[kind]
        if foreign:
            raise PrefixResourceMismatch(kind, foreign)
    if require_empty:
        if any(resources.values()):
            raise RuntimeError("synthetic Compose project still has resources")
        return
    for name in resources["container"]:
        item = docker_inspect("container", name)
        labels = item.get("Config", {}).get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        if (labels.get("com.docker.compose.project") != project or
                labels.get(OWNER_LABEL) != token or service not in services or
                not re.fullmatch(re.escape(project + "-" + service) + r"-[1-9][0-9]*", name)):
            raise RuntimeError("container does not belong to this synthetic fixture")
        pinned_image = (state["weknora_image_id"] if service == "wk-app" else
                        state.get("weknora_ui_image_id") if service == "wk-ui" else None)
        if pinned_image and item.get("Image") != pinned_image:
            raise RuntimeError("running WeKnora container image differs from fixture pin")
    if not resources["volume"] <= expected_volumes:
        raise RuntimeError("unexpected named volume in synthetic Compose project")
    for name in resources["volume"]:
        item = docker_inspect("volume", name)
        labels = item.get("Labels") or {}
        if (labels.get("com.docker.compose.project") != project or
                labels.get("com.docker.compose.volume") != name[len(project) + 1:] or
                labels.get(OWNER_LABEL) != token):
            raise RuntimeError("volume does not belong to this synthetic fixture")
    if not resources["network"] <= {expected_network}:
        raise RuntimeError("unexpected network in synthetic Compose project")
    for name in resources["network"]:
        item = docker_inspect("network", name)
        labels = item.get("Labels") or {}
        if (labels.get("com.docker.compose.project") != project or
                labels.get("com.docker.compose.network") != "default" or
                labels.get(OWNER_LABEL) != token):
            raise RuntimeError("network does not belong to this synthetic fixture")


def apply_resource_profile(config,profile):
    if profile not in {'default','normal-trial'}:
        raise RuntimeError('invalid fresh fixture resource profile')
    if profile=='normal-trial':
        for service,item in config['services'].items():
            memory=NORMAL_TRIAL_MEMORY_MIB[service]*1024**2
            item.update(mem_limit=memory,memswap_limit=memory,
                        cpus=1.0 if service in {'wk-app','nextcloud','wk-db','nc-db','docreader'} else .5)
        config['services']['wk-app'].setdefault('environment',{})['GOMEMLIMIT']='3GiB'


def prepare(image, mode, ui_image=None, *, body_journal=False, chat_stream_delay_max_seconds=0,
            resource_profile='default', postprocess_control_max_seconds=0):
    if type(chat_stream_delay_max_seconds) is not int or not 0 <= chat_stream_delay_max_seconds <= 20:
        raise RuntimeError("invalid owned chat stream delay maximum")
    if type(postprocess_control_max_seconds) is not int or not 0 <= postprocess_control_max_seconds <= 60:
        raise RuntimeError("invalid owned postprocess delay maximum")
    if resource_profile not in {'default','normal-trial'}:
        raise RuntimeError('invalid fresh fixture resource profile')
    inspected = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                               check=True, text=True, capture_output=True)
    image_id = inspected.stdout.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise RuntimeError("WeKnora image has no stable local image ID")
    ui_image_id = None
    if ui_image:
        ui_inspected = subprocess.run(
            ["docker", "image", "inspect", ui_image, "--format", "{{.Id}}"],
            check=True, text=True, capture_output=True)
        ui_image_id = ui_inspected.stdout.strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", ui_image_id):
            raise RuntimeError("WeKnora UI image has no stable local image ID")
    directory = Path(tempfile.mkdtemp(prefix="nc-synthetic-ldap-"))
    directory.chmod(0o700)
    try:
        for name in ("schema", "ldif"):
            (directory / name).mkdir(mode=0o700)
        for _ in range(16):
            project = "nc-synldap-" + secrets.token_hex(4)
            if assert_project_unoccupied(project):
                break
        else:
            raise RuntimeError("could not allocate an unused synthetic Compose project")
        owner_token = secrets.token_hex(16)
        # The WeKnora registration policy caps passwords at 32 characters
        # and requires letters plus a digit. This 31-character form also
        # avoids a leading '-' being parsed as an occ option by Nextcloud.
        passwords = {key: "xA1_" + secrets.token_urlsafe(20) for key in
                     ("ldap_admin", "ldap_bind", "alice", "bob", "charlie",
                      "nc_admin", "nc_db", "wk_admin", "wk_db", "jwt")}
        passwords["aes"] = secrets.token_hex(16)
        guids = {key: str(uuid.uuid4()) for key in
                 ("alice", "bob", "charlie", "domain", "grant", "child")}
        domain_parts = (21, secrets.randbelow(1000000) + 1000,
                        secrets.randbelow(1000000) + 1000,
                        secrets.randbelow(1000000) + 1000)
        port_names = ["nextcloud", "weknora", "ldap"]
        if ui_image:
            port_names.append("weknora_ui")
        ports = {key: free_port() for key in port_names}
        if len(set(ports.values())) != len(ports):
            raise RuntimeError("ephemeral port collision; retry prepare")
        write_private(directory / "schema/ad-test.ldif", SCHEMA)
        write_private(directory / "ldif/01-directory.ldif",
                      make_ldif(mode, passwords, guids, domain_parts))
        generate_certs(directory)
        config = compose_data(directory, project, ports, passwords, image,
                              ui_image, owner_token=owner_token, body_journal=body_journal)
        # A fresh fixture keeps the exact model implementation through later
        # repository updates. Existing owners and their stored hashes are not
        # adopted or rewritten by this preparation path.
        mock_code = directory.resolve() / "mock_embedding.py"
        mock_bytes = (ROOT / "integration/mock_embedding.py").read_bytes()
        write_private(mock_code, mock_bytes.decode("utf-8"))
        config["services"]["mock-embedding"]["volumes"] = [f"{mock_code}:/srv/mock_embedding.py:ro"]
        mock_env = {}
        if chat_stream_delay_max_seconds:
            mock_env["MOCK_CHAT_STREAM_DELAY_MAX_SECONDS"] = str(chat_stream_delay_max_seconds)
        if postprocess_control_max_seconds:
            mock_env["MOCK_POSTPROCESS_CONTROL_MAX_SECONDS"] = str(postprocess_control_max_seconds)
        if mock_env:
            config["services"]["mock-embedding"]["environment"] = mock_env
        apply_resource_profile(config,resource_profile)
        state = {"marker": MARKER, "project": project, "mode": mode,
                 "scratch_dir": str(directory.resolve()), "owner_token": owner_token,
                 "compose_fingerprint": compose_fingerprint(config),
                 "source_commit": subprocess.check_output(
                     ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
                 "weknora_image": image, "weknora_image_id": image_id,
                 "mock_model_code_sha256": hashlib.sha256(mock_bytes).hexdigest(),
                 "ports": ports, "guids": guids,
                 "directory_id": "synthetic-ad"}
        if body_journal:
            state["body_journal"] = True
        if chat_stream_delay_max_seconds:
            state["mock_chat_stream_delay_max_seconds"] = chat_stream_delay_max_seconds
        if postprocess_control_max_seconds:
            state["mock_postprocess_control_max_seconds"] = postprocess_control_max_seconds
        if resource_profile!='default':
            state['resource_profile']=resource_profile
        if ui_image:
            state.update({"weknora_ui_image": ui_image,
                          "weknora_ui_image_id": ui_image_id})
        write_private(directory / "state.json", json.dumps(state, indent=2) + "\n")
        write_private(directory / "passwords.json", json.dumps(passwords, indent=2) + "\n")
        write_private(directory / "compose.yaml", json.dumps(config, indent=2) + "\n")
        print(json.dumps({"scratch": str(directory), "project": project,
                          "mode": mode, "ports": ports}, separators=(",", ":")))
    except BaseException:
        shutil.rmtree(directory)
        raise


def owned_state(directory):
    directory = directory.resolve(strict=True)
    state = json.loads((directory / "state.json").read_text())
    if (state.get("marker") != MARKER or not
            str(directory).startswith(str(Path(tempfile.gettempdir()).resolve()) + os.sep) or
            not re.fullmatch(r"nc-synldap-[0-9a-f]{8}", state.get("project", "")) or
            state.get("scratch_dir") != str(directory) or
            not re.fullmatch(r"[0-9a-f]{32}", state.get("owner_token", "")) or
            not re.fullmatch(r"[0-9a-f]{64}", state.get("compose_fingerprint", "")) or
            not (directory / "compose.yaml").is_file()):
        raise RuntimeError("not an owned synthetic LDAP fixture")
    config = json.loads((directory / "compose.yaml").read_text())
    if (config.get("name") != state["project"] or
            set(config.get("services", {})) !=
            SERVICES | ({"wk-ui"} if state.get("weknora_ui_image") else set()) or
            set(config.get("volumes", {})) != VOLUMES | (BODY_VOLUMES if state.get("body_journal") else set()) or
            compose_fingerprint(config) != state["compose_fingerprint"]):
        raise RuntimeError("synthetic LDAP Compose configuration changed")
    assert_state_matches_compose(state, config)
    return directory, state


def assert_state_matches_compose(state, config):
    """Keep downstream loopback targets and image pins tied to owned Compose."""
    has_ui = "weknora_ui_image" in state
    ports = state.get("ports")
    expected_ports = {"nextcloud", "weknora", "ldap"}
    if has_ui:
        expected_ports.add("weknora_ui")
    if (not isinstance(ports, dict) or set(ports) != expected_ports or
            any(type(value) is not int or not 1024 < value <= 65535
                for value in ports.values()) or
            len(set(ports.values())) != len(ports)):
        raise RuntimeError("synthetic LDAP port state is invalid")
    image = state.get("weknora_image")
    image_id = state.get("weknora_image_id")
    if (not isinstance(image, str) or not image or
            not isinstance(image_id, str) or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)):
        raise RuntimeError("synthetic WeKnora backend image state is invalid")
    services = config["services"]
    profile=state.get('resource_profile','default')
    if profile not in {'default','normal-trial'}:
        raise RuntimeError('invalid fresh fixture resource profile')
    if profile=='normal-trial':
        expected=json.loads(json.dumps(config));apply_resource_profile(expected,profile)
        for service,item in services.items():
            if (type(item.get('mem_limit')) is not int or type(item.get('memswap_limit')) is not int or
                    type(item.get('cpus')) not in (int,float) or
                    any(item.get(key)!=expected['services'][service][key] for key in ('mem_limit','memswap_limit','cpus'))):
                raise RuntimeError('owned resource budget differs from its frozen profile')
        if services['wk-app'].get('environment',{}).get('GOMEMLIMIT')!='3GiB':
            raise RuntimeError('owned Go memory budget differs from its frozen profile')
    mock_hash = state.get("mock_model_code_sha256")
    if mock_hash is not None:
        if not isinstance(mock_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", mock_hash):
            raise RuntimeError("invalid frozen fixture model hash")
        mock_code = Path(state["scratch_dir"]) / "mock_embedding.py"
        details = mock_code.lstat()
        if (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or
                details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o600 or
                mock_code.resolve() != mock_code or hashlib.sha256(mock_code.read_bytes()).hexdigest() != mock_hash or
                services["mock-embedding"].get("volumes") != [f"{mock_code}:/srv/mock_embedding.py:ro"]):
            raise RuntimeError("frozen fixture model source changed")
    delay = state.get("mock_chat_stream_delay_max_seconds", 0)
    if type(delay) is not int or not 0 <= delay <= 20:
        raise RuntimeError("invalid owned chat stream delay maximum")
    expected_mock_env = {"MOCK_CHAT_STREAM_DELAY_MAX_SECONDS": str(delay)} if delay else None
    postprocess = state.get("mock_postprocess_control_max_seconds", 0)
    if type(postprocess) is not int or not 0 <= postprocess <= 60 or (postprocess and not mock_hash):
        raise RuntimeError("invalid frozen owned postprocess control")
    if postprocess:
        expected_mock_env = {**(expected_mock_env or {}), "MOCK_POSTPROCESS_CONTROL_MAX_SECONDS": str(postprocess)}
    if services["mock-embedding"].get("environment") != expected_mock_env:
        raise RuntimeError("owned chat stream delay differs from its frozen state")
    if state.get("body_journal") not in (None, True):
        raise RuntimeError("invalid body journal fixture mode")
    body_mounts = ["wk-body-journal:/var/lib/weknora-body-ledger",
                   "wk-body-key:/var/lib/weknora-body-key",
                   "wk-body-pin:/var/lib/weknora-body-pin"]
    body_env = {"WEKNORA_ORIGINAL_BODY_JOURNAL_DIRECTORY": "/var/lib/weknora-body-ledger",
                "WEKNORA_ORIGINAL_BODY_JOURNAL_KEY": "/var/lib/weknora-body-key/hmac.key",
                "WEKNORA_ORIGINAL_BODY_JOURNAL_PIN": "/var/lib/weknora-body-pin/pin.json"}
    app = services["wk-app"]
    actual_body_mounts = [m for m in app.get("volumes", []) if isinstance(m, str) and
                          any(m.split(":", 2)[1:2] == [expected.split(":")[1]] for expected in body_mounts)]
    if state.get("body_journal"):
        if sorted(actual_body_mounts) != sorted(body_mounts) or any(m not in app.get("volumes", []) for m in body_mounts) or any(
                app.get("environment", {}).get(k) != v for k, v in body_env.items()):
            raise RuntimeError("body journal must use its exact independent owned mounts")
    elif actual_body_mounts or any(k in app.get("environment", {}) for k in body_env):
        raise RuntimeError("unexpected body journal configuration")
    expected = {
        "openldap": ("ldap", 1636),
        "nextcloud": ("nextcloud", 80),
        "wk-app": ("weknora", 8080),
    }
    if has_ui:
        ui_image = state["weknora_ui_image"]
        ui_image_id = state.get("weknora_ui_image_id")
        if (not isinstance(ui_image, str) or not ui_image or
                not isinstance(ui_image_id, str) or
                not re.fullmatch(r"sha256:[0-9a-f]{64}", ui_image_id)):
            raise RuntimeError("synthetic WeKnora UI image state is invalid")
        expected["wk-ui"] = ("weknora_ui", 80)
    elif "weknora_ui_image_id" in state:
        raise RuntimeError("synthetic WeKnora UI image state is unexpected")
    if (services["wk-app"].get("image") != image or
            (has_ui and services["wk-ui"].get("image") != ui_image)):
        raise RuntimeError("synthetic WeKnora image differs from Compose")
    for service, (port_name, container_port) in expected.items():
        binding = f"127.0.0.1:{ports[port_name]}:{container_port}"
        if services[service].get("ports") != [binding]:
            raise RuntimeError("synthetic loopback port differs from Compose")


def compose_command(directory, state, *args):
    return ["docker", "compose", "-p", state["project"],
            "-f", str(directory / "compose.yaml"), *args]


def ldap_config_command(state, operation, payload=None):
    command = ["docker", "exec", *( ["-i"] if payload is not None else []),
               state["project"] + "-openldap-1",
               "/opt/bitnami/openldap/bin/" + operation,
               "-Q", "-Y", "EXTERNAL", "-H", "ldapi:///"]
    return subprocess.run(command, input=payload, text=True, capture_output=True,
                          check=True, timeout=30).stdout


def ldap_search(state, base, scope, ldap_filter, *attributes):
    command = ["docker", "exec", state["project"] + "-openldap-1",
               "/opt/bitnami/openldap/bin/ldapsearch", "-Q", "-Y", "EXTERNAL",
               "-H", "ldapi:///", "-LLL", "-o", "ldif-wrap=no",
               "-b", base, "-s", scope, ldap_filter, *attributes]
    return subprocess.run(command, text=True, capture_output=True, check=True,
                          timeout=30).stdout


def ldap_binary_sid(state, dn):
    data = ldap_search(state, dn, "base", "(objectClass=*)", "objectSid")
    if not any(line.casefold() == "dn: " + dn.casefold()
               for line in data.splitlines()):
        raise RuntimeError("synthetic SID owner is missing from LDAP")
    values = re.findall(r"^objectSid:: ([A-Za-z0-9+/=]+)$", data, re.M)
    if len(values) != 1:
        raise RuntimeError("synthetic LDAP SID is missing or ambiguous")
    raw = base64.b64decode(values[0], validate=True)
    if (len(raw) < 8 or len(raw) != 8 + 4 * raw[1] or
            raw[0] != 1 or raw[2:8] != (5).to_bytes(6, "big")):
        raise RuntimeError("synthetic LDAP SID is malformed")
    return raw


def ldap_sid_text(raw):
    return ("S-1-5" + "".join("-" + str(int.from_bytes(raw[i:i + 4], "little"))
                             for i in range(8, len(raw), 4)))


def exact_group_sids(state, ldap_filter):
    data = ldap_search(state, GROUPS, "sub", ldap_filter, "dn")
    return {line[4:].casefold() for line in data.splitlines()
            if line.startswith("dn: ")}


def configure_primary_sid_match(state):
    """Make the disposable OpenLDAP accept AD's textual objectSid assertion.

    Nextcloud user_ldap searches for a textual SID while the group and domain
    expose binary objectSid values. AD handles that assertion; plain OpenLDAP
    octetStringMatch does not. This exact rewrite keeps Nextcloud's own primary
    group lookup and both applications' binary directory identity in use.
    """
    domain_sid = ldap_binary_sid(state, BASE)
    group_sid = ldap_binary_sid(state, GRANT_DN)
    if (len(group_sid) != len(domain_sid) + 4 or
            group_sid[:2] != bytes((1, domain_sid[1] + 1)) or
            group_sid[2:-4] != domain_sid[2:] or
            int.from_bytes(group_sid[-4:], "little") != 2000):
        raise RuntimeError("synthetic primary group SID does not belong to the domain")
    sid_label = ldap_sid_text(group_sid)
    binary_filter = "(objectSid=" + "".join("\\%02x" % byte for byte in group_sid) + ")"
    text_filter = "(objectSid=" + sid_label + ")"
    wrong_filter = "(objectSid=" + ldap_sid_text(group_sid[:-4] +
                    (2002).to_bytes(4, "little")) + ")"
    expected = {GRANT_DN.casefold()}
    if exact_group_sids(state, binary_filter) != expected:
        raise RuntimeError("synthetic primary group binary SID lookup failed")
    module_path = "/opt/bitnami/openldap/lib/openldap/rwm.so"
    modules = ldap_search(state, "cn=module{0},cn=config", "base",
                          "(objectClass=*)", "olcModuleLoad")
    if module_path not in modules:
        if re.search(r"^olcModuleLoad: .*rwm", modules, re.M):
            raise RuntimeError("synthetic LDAP has an unexpected rewrite module")
        ldap_config_command(state, "ldapmodify",
                            "dn: cn=module{0},cn=config\nchangetype: modify\n"
                            "add: olcModuleLoad\nolcModuleLoad: " + module_path + "\n\n")
    # The rule matches only this domain/group SID and requires the closing
    # filter parenthesis. A changed RID or a wrong-domain SID cannot match.
    escaped_binary = "".join("\\%02x" % byte for byte in group_sid)
    rule = (f'rwm-rewriteRule "^(.*)objectsid={sid_label}([)].*)$" '
            f'"$1objectsid={escaped_binary}$2" ":@"')
    overlay_base = "olcDatabase={-1}frontend,cn=config"
    overlays = ldap_search(state, overlay_base, "one", "(olcOverlay=rwm)",
                           "olcRwmRewrite")
    if not overlays.strip():
        if exact_group_sids(state, text_filter):
            raise RuntimeError("text SID already resolves without the expected adapter")
        ldap_config_command(state, "ldapadd",
                            "dn: olcOverlay=rwm," + overlay_base + "\n"
                            "objectClass: olcOverlayConfig\n"
                            "objectClass: olcRwmConfig\nolcOverlay: rwm\n"
                            "olcRwmRewrite: rwm-rewriteEngine on\n"
                            "olcRwmRewrite: rwm-rewriteContext searchFilter\n"
                            "olcRwmRewrite: " + rule + "\n\n")
        overlays = ldap_search(state, overlay_base, "one", "(olcOverlay=rwm)",
                               "olcRwmRewrite")
    actual_rules = [re.sub(r"^\{[0-9]+\}", "", line.split(": ", 1)[1])
                    for line in overlays.splitlines()
                    if line.startswith("olcRwmRewrite: ")]
    if actual_rules != ["rwm-rewriteEngine on", "rwm-rewriteContext searchFilter", rule]:
        raise RuntimeError("synthetic LDAP SID rewrite differs from the exact adapter")
    if (exact_group_sids(state, text_filter) != expected or
            exact_group_sids(state, wrong_filter)):
        raise RuntimeError("synthetic AD-compatible textual SID lookup failed closed")


def initialize_body_journal(directory, state):
    # Start only the DB before migration. No app/readers/builders run until init
    # and verify complete with the same appuser UID as the normal entrypoint.
    subprocess.run(compose_command(directory, state, "up", "-d", "--wait",
                                   "--wait-timeout", "180", "wk-db"), check=True,
                   capture_output=True, text=True)
    script = """set -eu
cd /app
export WEKNORA_ORIGINAL_BODY_MAINTENANCE_DSN="postgres://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}?sslmode=disable"
exec gosu appuser original-body-retention -driver postgres -mode "$1"
"""
    def maintenance(mode):
        result = subprocess.run(compose_command(directory, state, "run", "--rm", "--no-deps",
                                "--name", state["project"] + "-wk-app-99", "--entrypoint", "/bin/sh", "wk-app", "-c", script, "sh", mode),
                                check=False, capture_output=True, text=True, timeout=240)
        if result.returncode:
            raise RuntimeError("owned body journal " + mode + " failed; preserve fixture for diagnosis")
    if state.get("body_journal_initialized"):
        maintenance("verify")
        return
    maintenance("migrate")
    setup = """set -eu
for path in /var/lib/weknora-body-ledger /var/lib/weknora-body-key /var/lib/weknora-body-pin; do
  test -d "$path" && test ! -L "$path"
  test -z "$(find "$path" -mindepth 1 -maxdepth 1 -print -quit)"
  chown appuser:appuser "$path"
  chmod 0700 "$path"
done
gosu appuser sh -c 'umask 077; head -c 48 /dev/urandom > /var/lib/weknora-body-key/hmac.key'
"""
    result = subprocess.run(compose_command(directory, state, "run", "--rm", "--no-deps",
                            "--name", state["project"] + "-wk-app-99", "--entrypoint", "/bin/sh", "wk-app", "-c", setup),
                            check=False, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError("body journal provisioning refused; existing anchors are not replaced")
    maintenance("init")
    maintenance("verify")
    assert_owned_resources(directory, state)
    state["body_journal_initialized"] = True
    write_private(directory / "state.json", json.dumps(state, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("prepare")
    create.add_argument("--weknora-image", required=True)
    create.add_argument("--weknora-ui-image",
                        help="optional locally built UI image for browser acceptance")
    create.add_argument("--body-journal", action="store_true", help="initialize candidate152 external anchor before app startup")
    create.add_argument("--mode", choices=("direct", "primary", "nested"), required=True)
    create.add_argument("--chat-stream-delay-max-seconds", type=int, default=0,
                        help="fresh fixture only: opt in to loopback-only bounded real model stream scheduling (0-20)")
    create.add_argument("--postprocess-control-max-seconds", type=int, default=0,
                        help="fresh fixture only: bounded loopback non-stream Summary/Question control and Auto telemetry (0-60)")
    create.add_argument('--resource-profile',choices=('default','normal-trial'),default='default',
                        help='fresh only: normal-trial pins app4GiB/noSwap/CPU1/Go3GiB and bounded companion services')
    for name in ("up", "status", "destroy"):
        command = commands.add_parser(name)
        command.add_argument("--scratch", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.weknora_image, args.mode, args.weknora_ui_image, body_journal=args.body_journal,
                chat_stream_delay_max_seconds=args.chat_stream_delay_max_seconds,resource_profile=args.resource_profile,
                postprocess_control_max_seconds=args.postprocess_control_max_seconds)
        return
    directory, state = owned_state(args.scratch)
    if args.action == "up":
        assert_owned_resources(directory, state)
        inspected = subprocess.run(["docker", "image", "inspect", state["weknora_image"],
                                    "--format", "{{.Id}}"], check=True, text=True,
                                   capture_output=True)
        if inspected.stdout.strip() != state["weknora_image_id"]:
            raise RuntimeError("WeKnora image tag changed since fixture preparation")
        if state.get("weknora_ui_image"):
            ui_inspected = subprocess.run(
                ["docker", "image", "inspect", state["weknora_ui_image"],
                 "--format", "{{.Id}}"], check=True, text=True, capture_output=True)
            if ui_inspected.stdout.strip() != state["weknora_ui_image_id"]:
                raise RuntimeError("WeKnora UI image tag changed since fixture preparation")
        if state.get("body_journal"):
            initialize_body_journal(directory, state)
        subprocess.run(compose_command(directory, state, "up", "-d", "--wait",
                                       "--wait-timeout", "600"), check=True)
        assert_owned_resources(directory, state)
        if state["mode"] == "primary":
            configure_primary_sid_match(state)
    elif args.action == "status":
        subprocess.run(compose_command(directory, state, "ps"), check=True)
    else:
        assert_owned_resources(directory, state)
        subprocess.run(compose_command(directory, state, "down", "--volumes",
                                       "--remove-orphans"), check=True)
        assert_owned_resources(directory, state, require_empty=True)
        shutil.rmtree(directory)
        print("owned synthetic LDAP fixture removed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print("Synthetic LDAP fixture failed: " + str(error), file=sys.stderr)
        sys.exit(1)
