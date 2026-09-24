#!/usr/bin/env python3
"""Create and own a disposable, loopback-only AD-shaped LDAP Compose fixture.

The generator writes secrets only under a new 0700 scratch directory. It does
not modify existing Compose projects. `destroy` accepts only its own marker.
The application setup and cross-system permission matrix are described in
docs/synthetic-ldap-compose.md.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[2]
MARKER = "nextcloud-weknora-synthetic-ldap-v1"
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
olcObjectClasses: ( 1.3.6.1.4.1.4203.666.11.9.20 NAME 'adTestUser' SUP top AUXILIARY MUST ( objectGUID $ objectSid $ sAMAccountName $ userPrincipalName $ userAccountControl $ primaryGroupID ) )
olcObjectClasses: ( 1.3.6.1.4.1.4203.666.11.9.21 NAME 'adTestGroup' SUP top AUXILIARY MUST ( objectGUID $ objectSid ) MAY ( sAMAccountName $ displayName $ mail ) )
"""


def write_private(path, content):
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def sid(domain_parts, rid):
    parts = domain_parts + (rid,)
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
        "objectSid:: " + binary(object_sid), "", "",
    ])


def make_ldif(mode, passwords, guids, domain_parts):
    primary_a = 2000 if mode == "primary" else 513
    grant_members = ([A_DN] if mode == "direct" else
                     [CHILD_DN] if mode == "nested" else [DISABLED_DN])
    rows = [
        "dn: " + BASE + "\nobjectClass: top\nobjectClass: domain\ndc: example\n\n",
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


def compose_data(directory, project, ports, passwords, image):
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
    return {
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
        },
        "volumes": {name: {} for name in
                    ("ldap-certs", "ldap-fixture", "nc-postgres", "nc-redis-data", "nc-html",
                     "wk-postgres", "wk-data", "docreader-tmp")},
    }


def prepare(image, mode):
    inspected = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                               check=True, text=True, capture_output=True)
    image_id = inspected.stdout.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise RuntimeError("WeKnora image has no stable local image ID")
    directory = Path(tempfile.mkdtemp(prefix="nc-synthetic-ldap-"))
    directory.chmod(0o700)
    try:
        for name in ("schema", "ldif"):
            (directory / name).mkdir(mode=0o700)
        suffix = secrets.token_hex(4)
        project = "nc-synldap-" + suffix
        passwords = {key: secrets.token_urlsafe(24) for key in
                     ("ldap_admin", "ldap_bind", "alice", "bob", "charlie",
                      "nc_admin", "nc_db", "wk_admin", "wk_db", "jwt")}
        passwords["aes"] = secrets.token_hex(16)
        guids = {key: str(uuid.uuid4()) for key in
                 ("alice", "bob", "charlie", "domain", "grant", "child")}
        domain_parts = (21, secrets.randbelow(1000000) + 1000,
                        secrets.randbelow(1000000) + 1000,
                        secrets.randbelow(1000000) + 1000)
        ports = {key: free_port() for key in ("nextcloud", "weknora", "ldap")}
        if len(set(ports.values())) != len(ports):
            raise RuntimeError("ephemeral port collision; retry prepare")
        write_private(directory / "schema/ad-test.ldif", SCHEMA)
        write_private(directory / "ldif/01-directory.ldif",
                      make_ldif(mode, passwords, guids, domain_parts))
        generate_certs(directory)
        state = {"marker": MARKER, "project": project, "mode": mode,
                 "source_commit": subprocess.check_output(
                     ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
                 "weknora_image": image, "weknora_image_id": image_id,
                 "ports": ports, "guids": guids,
                 "directory_id": "synthetic-ad"}
        write_private(directory / "state.json", json.dumps(state, indent=2) + "\n")
        write_private(directory / "passwords.json", json.dumps(passwords, indent=2) + "\n")
        write_private(directory / "compose.yaml",
                      json.dumps(compose_data(directory, project, ports, passwords, image),
                                 indent=2) + "\n")
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
            not (directory / "compose.yaml").is_file()):
        raise RuntimeError("not an owned synthetic LDAP fixture")
    return directory, state


def compose_command(directory, state, *args):
    return ["docker", "compose", "-p", state["project"],
            "-f", str(directory / "compose.yaml"), *args]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("prepare")
    create.add_argument("--weknora-image", required=True)
    create.add_argument("--mode", choices=("direct", "primary", "nested"), required=True)
    for name in ("up", "status", "destroy"):
        command = commands.add_parser(name)
        command.add_argument("--scratch", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.weknora_image, args.mode)
        return
    directory, state = owned_state(args.scratch)
    if args.action == "up":
        inspected = subprocess.run(["docker", "image", "inspect", state["weknora_image"],
                                    "--format", "{{.Id}}"], check=True, text=True,
                                   capture_output=True)
        if inspected.stdout.strip() != state["weknora_image_id"]:
            raise RuntimeError("WeKnora image tag changed since fixture preparation")
        subprocess.run(compose_command(directory, state, "up", "-d", "--wait",
                                       "--wait-timeout", "600"), check=True)
    elif args.action == "status":
        subprocess.run(compose_command(directory, state, "ps"), check=True)
    else:
        subprocess.run(compose_command(directory, state, "down", "--volumes",
                                       "--remove-orphans"), check=True)
        shutil.rmtree(directory)
        print("owned synthetic LDAP fixture removed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print("Synthetic LDAP fixture failed: " + str(error), file=sys.stderr)
        sys.exit(1)
