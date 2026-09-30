#!/usr/bin/env python3
"""Bootstrap and probe an owned, disposable synthetic LDAP Compose fixture.

Run synthetic-ldap-fixture.py prepare/up first. All credentials and generated
fixtures remain inside that script's private scratch directory. This script
never accepts an arbitrary Compose project or remote HTTP origin.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/integration_weknora/tests"))
from changes_http_smoke import file_id  # noqa: E402
from publication_http_smoke import login, request  # noqa: E402

owner = runpy.run_path(str(Path(__file__).with_name("synthetic-ldap-fixture.py")))
pair_helpers = runpy.run_path(str(Path(__file__).with_name("local-source-pairing.py")))
index_helpers = runpy.run_path(str(Path(__file__).with_name("local-indexed-withdrawal-smoke.py")))
owned_state = owner["owned_state"]
nc_request = pair_helpers["nc_request"]
wk_request = pair_helpers["wk_request"]
index_proof = index_helpers["index_proof"]
sql_json = index_helpers["sql_json"]
GROUPFOLDERS_VERSION = "22.0.6"
GROUPFOLDERS_SHA256 = "bfff357b12bbd24257d8d127cf30e83a72256e7659f1cc3e32bd09fe647d2f9b"


def private_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def need(status, expected, step):
    if status not in expected:
        raise RuntimeError(f"{step}: HTTP {status}, expected {sorted(expected)}")


def occ(state, *arguments, password_stdin=None):
    container = state["project"] + "-nextcloud-1"
    if password_stdin is None:
        command = ["docker", "exec", "-u", "www-data", container,
                   "php", "occ", *arguments]
        result = subprocess.run(command, check=True, text=True, capture_output=True,
                                timeout=90)
    else:
        # Keep the bind password out of the host's docker-exec argument list.
        script = 'IFS= read -r value; php occ ldap:set-config "$1" ldapAgentPassword "$value"'
        command = ["docker", "exec", "-i", "-u", "www-data", container,
                   "sh", "-c", script, "sh", arguments[0]]
        result = subprocess.run(command, input=password_stdin + "\n", check=True,
                                text=True, capture_output=True, timeout=90)
    return result.stdout


def http_json(base, method, path, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    outgoing = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(outgoing, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        # Server errors can contain tokens or credentials; never log the body.
        error.read()
        return error.code, {}


def install_groupfolders(directory, state):
    package = directory / f"groupfolders-v{GROUPFOLDERS_VERSION}.tar.gz"
    if not package.is_file() or hashlib.sha256(package.read_bytes()).hexdigest() != GROUPFOLDERS_SHA256:
        raise RuntimeError("pinned groupfolders 22.0.6 package is absent or has a wrong SHA-256")
    with tarfile.open(package, "r:gz") as archive:
        members = archive.getmembers()
        if not members or any(
                item.issym() or item.islnk() or
                not (item.name == "groupfolders" and item.isdir() or
                     item.name.startswith("groupfolders/")) or
                ".." in Path(item.name).parts for item in members):
            raise RuntimeError("groupfolders package has an unsafe file layout")
        archive.extractall(directory)
    container = state["project"] + "-nextcloud-1"
    subprocess.run(["docker", "cp", str(directory / "groupfolders"),
                    container + ":/var/www/html/custom_apps/"], check=True,
                   capture_output=True, timeout=60)
    subprocess.run(["docker", "exec", container, "chown", "-R", "www-data:www-data",
                    "/var/www/html/custom_apps/groupfolders"], check=True,
                   capture_output=True, timeout=30)
    occ(state, "app:enable", "groupfolders")
    enabled = json.loads(occ(state, "app:list", "--output=json"))["enabled"]
    if enabled.get("groupfolders") != GROUPFOLDERS_VERSION:
        raise RuntimeError("disposable Team Folders app did not enable at the pinned version")


def nextcloud_setup(directory, state, passwords, nc_base, team_folder=False):
    for app in ("user_ldap", "integration_weknora"):
        occ(state, "app:enable", app)
    if team_folder:
        install_groupfolders(directory, state)
    # The connector reaches Nextcloud over the private Compose network, while
    # employee citations must open the host's browser origin. Set the public
    # route before the first source sync so indexed metadata records it.
    occ(state, "config:system:set", "overwrite.cli.url", "--value=" + nc_base)
    browser_port = state["ports"].get("weknora_ui", state["ports"]["weknora"])
    occ(state, "config:app:set", "integration_weknora", "weknora_web_url",
        "--value=http://127.0.0.1:" + str(browser_port))
    config = occ(state, "ldap:create-empty-config")
    match = re.search(r"configID ['\"]?(s[0-9]+)", config)
    if not match:
        raise RuntimeError("Nextcloud did not create an LDAP configuration")
    config_id = match.group(1)
    settings = {
        "ldapHost": "ldaps://openldap", "ldapPort": "1636",
        "ldapAgentName": owner["READER_DN"], "ldapBase": owner["BASE"],
        "ldapBaseUsers": owner["PEOPLE"], "ldapBaseGroups": owner["GROUPS"],
        "ldapUserFilter": "(&(objectClass=adTestUser)(objectClass=person))",
        "ldapLoginFilter": "(&(objectClass=adTestUser)(|(sAMAccountName=%uid)(userPrincipalName=%uid)))",
        "ldapGroupFilter": "(objectClass=adTestGroup)",
        "ldapGroupMemberAssocAttr": "member", "ldapNestedGroups": "1",
        "ldapExpertUUIDUserAttr": "objectGUID", "ldapExpertUUIDGroupAttr": "objectGUID",
        "ldapExpertUsernameAttr": "sAMAccountName", "ldapCacheTTL": "30",
    }
    for key, value in settings.items():
        occ(state, "ldap:set-config", config_id, key, value)
    occ(state, config_id, password_stdin=passwords["ldap_bind"])
    occ(state, "ldap:set-config", config_id, "ldapConfigurationActive", "1")
    occ(state, "ldap:test-config", config_id)
    users = json.loads(occ(state, "user:list", "--output=json"))
    if not {"alice", "bob"}.issubset(users):
        raise RuntimeError("Nextcloud did not discover both synthetic LDAP accounts")
    groups = json.loads(occ(state, "group:list", "--output=json"))
    if state["mode"] != "primary" and "alice" not in groups.get("Engineering", []):
        raise RuntimeError("Nextcloud did not resolve the synthetic group membership")
    if "Engineering" not in groups:
        raise RuntimeError("Nextcloud did not discover the synthetic group")
    occ(state, "config:app:set", "integration_weknora", "ad_directory_id",
        "--value=" + state["directory_id"])

    admin, csrf = login(nc_base, "devadmin", passwords["nc_admin"])
    api = nc_base + "/index.php/apps/integration_weknora/api/v1"
    folder = nc_base + "/remote.php/dav/files/devadmin/Published"
    document = folder + "/acl-note.txt"
    auth = base64.b64encode(("devadmin:" + passwords["nc_admin"]).encode()).decode()
    dav_headers = {"Authorization": "Basic " + auth}
    team_folder_id = None
    if team_folder:
        occ(state, "group:add", "SyntheticPublisher")
        occ(state, "group:adduser", "SyntheticPublisher", "devadmin")
        team_folder_id = int(occ(state, "groupfolders:create", "--output=json",
                                 "Published").strip())
        if team_folder_id < 1:
            raise RuntimeError("synthetic Team Folder ID is invalid")
        for group in ("SyntheticPublisher", "Engineering"):
            occ(state, "groupfolders:group", str(team_folder_id), group,
                "read", "write", "share", "delete")
    else:
        status, _ = request(urllib.request.build_opener(), folder, "MKCOL", dav_headers)
        need(status, {201}, "create synthetic folder")
    content = ("Which synthetic approval code is in this document? "
               "The synthetic approval code is ORCHID-QUARTZ-2749.\n").encode()
    status, _ = request(urllib.request.build_opener(), document, "PUT", dav_headers, content)
    need(status, {201}, "create synthetic document")
    root_id = file_id(folder, dav_headers)
    document_id = file_id(document, dav_headers)
    share_id = None
    if not team_folder:
        share_body = urllib.parse.urlencode({"path": "/Published", "shareType": 1,
                                             "shareWith": "Engineering", "permissions": 1}).encode()
        for attempt in range(30):
            status, raw = request(admin, nc_base + "/ocs/v2.php/apps/files_sharing/api/v1/shares",
                                  "POST", {"requesttoken": csrf, "OCS-APIRequest": "true",
                                           "Accept": "application/json",
                                           "Content-Type": "application/x-www-form-urlencoded"}, share_body)
            if status == 200 and json.loads(raw)["ocs"]["meta"]["statuscode"] == 200:
                break
            # A fresh LDAP group can appear in occ before the sharing provider's
            # own lookup catches up. Retry only the bounded, missing-group result.
            if status != 404 or attempt == 29:
                raise RuntimeError(f"share synthetic folder: HTTP {status}")
            time.sleep(2)
        share_id = json.loads(raw)["ocs"]["data"].get("id")
        if (isinstance(share_id, bool) or not isinstance(share_id, (int, str)) or
                not re.fullmatch(r"[1-9][0-9]*", str(share_id))):
            raise RuntimeError("synthetic group share response omitted its ID")
    # An LDAP topology path alone does not prove that Nextcloud grants the
    # mounted group share. Check the employee's actual DAV access before a
    # source pair can be created, including in primary-group mode.
    alice_auth = base64.b64encode(("alice:" + passwords["alice"]).encode()).decode()
    alice_document = nc_base + "/remote.php/dav/files/alice/Published/acl-note.txt"
    for attempt in range(15):
        status, _ = request(urllib.request.build_opener(), alice_document, "PROPFIND",
                            {"Authorization": "Basic " + alice_auth, "Depth": "0"})
        if status == 207:
            break
        if status not in {403, 404} or attempt == 14:
            raise RuntimeError(
                f"Nextcloud LDAP grant preflight failed for {state['mode']}: DAV HTTP {status}")
        time.sleep(2)
    binding = "synthetic-published"
    status, body = nc_request(admin, csrf, api + "/admin/bindings", "POST", {
        "id": binding, "name": binding, "owner_uid": "devadmin", "root_file_id": root_id})
    need(status, {201}, "create synthetic binding")
    if body.get("binding", {}).get("id") != binding:
        raise RuntimeError("synthetic binding identity mismatch")
    for user in ("alice", "bob"):
        status, _ = nc_request(admin, csrf, api + "/admin/identities", "POST", {
            "directory_id": state["directory_id"], "object_guid": state["guids"][user],
            "nextcloud_uid": user})
        need(status, {201}, "map synthetic identity " + user)
    key_id = "synthetic-key"
    status, body = nc_request(admin, csrf, api + "/admin/bindings/" + binding + "/keys",
                              "POST", {"key_id": key_id})
    need(status, {201}, "create synthetic source key")
    token = body.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("synthetic source key was not returned")
    return {"root_file_id": root_id, "file_id": document_id,
            "publication_root": "team_folder" if team_folder else "group_share",
            "team_folder_id": team_folder_id,
            "share_id": int(share_id) if share_id is not None else None,
            "binding_id": binding, "key_id": key_id, "token": token}


def weknora_setup(directory, state, passwords, nc_base, wk_base, runtime):
    status, _ = http_json(wk_base, "POST", "/api/v1/auth/register", {
        "username": "synthetic-admin", "email": "synthetic-admin@example.test",
        "password": passwords["wk_admin"]})
    need(status, {201}, "register isolated WeKnora administrator")
    subprocess.run(["docker", "compose", "-p", state["project"], "-f",
                    str(directory / "compose.yaml"), "restart", "wk-app"], check=True,
                   stdout=subprocess.DEVNULL, timeout=90)
    for _ in range(60):
        try:
            with urllib.request.urlopen(wk_base + "/health", timeout=2) as response:
                if response.status == 200:
                    break
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    else:
        raise RuntimeError("isolated WeKnora app did not restart healthy")
    status, auth = http_json(wk_base, "POST", "/api/v1/auth/login", {
        "email": "synthetic-admin@example.test", "password": passwords["wk_admin"]})
    need(status, {200}, "WeKnora administrator login")
    token = auth["token"]
    tenant = auth["active_tenant"]["id"]
    status, _ = http_json(wk_base, "GET", "/api/v1/system/admin/directory/status", token=token)
    need(status, {200}, "WeKnora directory administration")
    status, sync = http_json(wk_base, "POST", "/api/v1/system/admin/directory/sync", token=token)
    need(status, {200}, "WeKnora directory sync")
    if sync.get("status") != "success":
        raise RuntimeError("WeKnora directory sync did not succeed")
    status, catalog = http_json(wk_base, "GET",
                                f"/api/v1/tenants/{tenant}/directory-groups?available=true",
                                token=token)
    need(status, {200}, "WeKnora directory group catalog")
    groups = {item["display_name"]: item["directory_group_id"]
              for item in catalog["data"]["groups"]}
    if not {"Engineering", "Domain Users"}.issubset(groups):
        raise RuntimeError("synthetic LDAP groups are missing from WeKnora")
    status, _ = http_json(wk_base, "POST", f"/api/v1/tenants/{tenant}/directory-groups",
                          {"directory_id": state["directory_id"],
                           "directory_group_id": groups["Domain Users"],
                           "role": "viewer"}, token)
    need(status, {201}, "grant both users workspace viewer")
    status, model = http_json(wk_base, "POST", "/api/v1/models", {
        "name": "mock-embed-synthetic", "type": "embedding", "source": "remote",
        "parameters": {"base_url": "http://mock-embedding:8000/v1", "provider": "generic",
                       "embedding_parameters": {"dimension": 3}}}, token)
    need(status, {201}, "register isolated mock embedding model")
    model_id = model["data"]["id"]
    status, chat_model = http_json(wk_base, "POST", "/api/v1/models", {
        "name": "mock-chat-synthetic", "type": "KnowledgeQA", "source": "remote",
        "parameters": {"base_url": "http://mock-embedding:8000/v1", "provider": "generic",
                       "api_key": "synthetic-only"}}, token)
    need(status, {201}, "register isolated mock chat model")
    chat_model_id = chat_model["data"]["id"]
    status, kb = http_json(wk_base, "POST", "/api/v1/knowledge-bases", {
        "name": "Synthetic LDAP ACL", "type": "document", "embedding_model_id": model_id,
        "summary_model_id": chat_model_id}, token)
    need(status, {201}, "create synthetic knowledge base")
    kb_id = kb["data"]["id"]
    status, _ = http_json(wk_base, "PUT", "/api/v1/group-access/knowledge_base/" + kb_id, {
        "mode": "restricted", "grants": [{"directory_id": state["directory_id"],
                                         "directory_group_id": groups["Engineering"],
                                         "permission": "read"}]}, token)
    need(status, {200}, "restrict synthetic knowledge base")

    admin, csrf = login(nc_base, "devadmin", passwords["nc_admin"])
    pair_url = (nc_base + "/index.php/apps/integration_weknora/api/v1/admin/bindings/" +
                runtime["binding_id"] + "/source-pairing")
    operation = str(uuid.uuid4())
    status, prepared = nc_request(admin, csrf, pair_url, "POST", {
        "operation_id": operation, "tenant_id": str(tenant), "knowledge_base_id": kb_id})
    need(status, {201}, "prepare synthetic source pair")
    pair = prepared["pairing"]
    one_time = prepared["token"]
    status, _ = wk_request(wk_base, token, "POST",
                           "/api/v1/datasource/nextcloud-source-pairings", {
        "knowledge_base_id": kb_id, "base_url": "http://nextcloud",
        "binding_id": runtime["binding_id"], "operation_id": operation,
        "instance_id": pair["instance_id"], "publication_epoch": pair["publication_epoch"],
        "key_id": pair["key_id"], "token": one_time})
    del one_time
    need(status, {200, 201, 202}, "create synthetic source pair")
    for _ in range(8):
        n_status, n_body = nc_request(admin, csrf, pair_url, "GET")
        w_status, w_body = wk_request(wk_base, token, "GET",
                                      "/api/v1/datasource/nextcloud-source-pairings/" + operation)
        if n_status == w_status == 200 and (n_body.get("pairing", {}).get("state") ==
                                             w_body.get("pairing", {}).get("state") == "active"):
            source_id = w_body["pairing"]["data_source_id"]
            break
        status, _ = wk_request(wk_base, token, "POST",
                               "/api/v1/datasource/nextcloud-source-pairings/" + operation + "/retry")
        need(status, {200, 202}, "retry synthetic source pair")
        time.sleep(.5)
    else:
        raise RuntimeError("synthetic source pair did not become active")
    event_connection_setup(state, passwords, nc_base, wk_base, runtime, source_id, token)
    status, _ = wk_request(wk_base, token, "POST", f"/api/v1/datasource/{source_id}/sync")
    need(status, {200, 409}, "sync synthetic source")
    database = state["project"] + "-wk-db-1"
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        proof = index_proof(database, source_id, runtime["file_id"])
        if proof:
            break
        time.sleep(2)
    else:
        raise RuntimeError("synthetic document did not become indexed")
    query = ("SELECT to_jsonb(k.id) FROM nextcloud_source_versions v "
             "JOIN knowledges k ON k.id=v.candidate_knowledge_id "
             f"WHERE v.datasource_id='{source_id}' "
             f"AND v.external_id LIKE '%:{runtime['file_id']}' AND v.state='published'")
    knowledge_id = sql_json(database, query)
    return {"tenant_id": tenant, "model_id": model_id, "chat_model_id": chat_model_id,
            "knowledge_base_id": kb_id,
            "knowledge_id": knowledge_id, "source_id": source_id,
            "operation_id": operation, "indexed_chunks": proof["chunks"],
            "indexed_embeddings": proof["embeddings"]}


def event_connection_setup(state, passwords, nc_base, wk_base, runtime, source_id,
                           weknora_admin_token):
    """Pair the signed status/event channel inside this owned Compose stack."""
    path = f"/api/v1/datasource/{source_id}/nextcloud-event-connection"
    status, credential = wk_request(wk_base, weknora_admin_token, "POST", path)
    need(status, {201}, "create synthetic signed event/status connection")
    if not all(isinstance(credential.get(name), str) and credential[name]
               for name in ("connection_id", "key_id", "secret", "receiver_url")):
        raise RuntimeError("synthetic event credential was incomplete")
    credential["receiver_url"] = (
        "http://wk-app:8080/api/v1/integrations/nextcloud/events")
    admin, csrf = login(nc_base, "devadmin", passwords["nc_admin"])
    endpoint = (nc_base + "/index.php/apps/integration_weknora/api/v1/admin/bindings/" +
                runtime["binding_id"] + "/event-connection")
    status, _ = nc_request(admin, csrf, endpoint, "POST", credential)
    need(status, {201}, "configure synthetic signed status sender")


def make_fixture(state, runtime):
    allowed = dict(nextcloud_login=True, ldap_login=True, dav=True, source=True,
                   knowledge=True, search=True)
    denied = dict(nextcloud_login=True, ldap_login=True, dav=False, source=False,
                  knowledge=False, search=False)
    return {"schema_version": 1, "synthetic_fixture": True,
            "binding_id": runtime["binding_id"], "directory_id": state["directory_id"],
            "file_id": runtime["file_id"],
            "knowledge_base_id": runtime["knowledge_base_id"],
            "knowledge_id": runtime["knowledge_id"],
            "synthetic_query": "synthetic approval code",
            "accounts": {
                label: {"nextcloud_uid": user, "weknora_identifier": user,
                        "dav_path": "Published/acl-note.txt",
                        "object_guid": state["guids"][user]}
                for label, user in (("a", "alice"), ("b", "bob"))},
            "cases": {state["mode"] + "_group" if state["mode"] != "direct" else "baseline":
                      {"a": allowed, "b": denied}}}


def bootstrap(directory, state, team_folder=False):
    if (directory / "fixture.json").exists():
        raise RuntimeError("fixture already bootstrapped; use matrix or create a fresh stack")
    passwords = json.loads((directory / "passwords.json").read_text())
    nc_base = f"http://127.0.0.1:{state['ports']['nextcloud']}"
    wk_base = f"http://127.0.0.1:{state['ports']['weknora']}"
    runtime = nextcloud_setup(directory, state, passwords, nc_base, team_folder)
    private_json(directory / "runtime.json", runtime)
    runtime.update(weknora_setup(directory, state, passwords, nc_base, wk_base, runtime))
    private_json(directory / "runtime.json", runtime)
    private_json(directory / "fixture.json", make_fixture(state, runtime))
    print(json.dumps({"project": state["project"], "case": state["mode"],
                      "indexed_chunks": runtime["indexed_chunks"],
                      "indexed_embeddings": runtime["indexed_embeddings"]}))


def matrix(directory, state):
    if not (directory / "fixture.json").is_file():
        raise RuntimeError("bootstrap the owned fixture before running the matrix")
    passwords = json.loads((directory / "passwords.json").read_text())
    runtime = json.loads((directory / "runtime.json").read_text())
    case = state["mode"] if state["mode"] != "direct" else "baseline"
    command = [sys.executable, str(Path(__file__).with_name("ad-permission-acceptance.py")),
               "--fixture", str(directory / "fixture.json"), "--case", case,
               "--nextcloud-origin", f"http://127.0.0.1:{state['ports']['nextcloud']}",
               "--weknora-origin", f"http://127.0.0.1:{state['ports']['weknora']}",
               "--allow-loopback-http"]
    if case in ("primary", "nested"):
        export = directory / "topology.ldif"
        with export.open("wb") as output:
            result = subprocess.run([
                "docker", "exec", state["project"] + "-openldap-1", "sh", "-c",
                '/opt/bitnami/openldap/bin/ldapsearch -x -LLL -o ldif-wrap=no '
                '-H ldap://127.0.0.1:1389 -D cn=admin,dc=example,dc=test '
                '-w "$LDAP_ADMIN_PASSWORD" -b dc=example,dc=test '
                '"(|(objectClass=adTestUser)(objectClass=adTestGroup))" '
                'dn objectClass objectGUID objectSid primaryGroupID member uniqueMember',
            ], stdout=output, stderr=subprocess.PIPE, timeout=30)
        export.chmod(0o600)
        if result.returncode:
            raise RuntimeError("owned LDAP topology export failed")
        command += ["--topology-ldif", str(export), "--grant-group-guid",
                    state["guids"]["grant"]]
        if case == "nested":
            command += ["--child-group-guid", state["guids"]["child"]]
        command[command.index("--case") + 1] = case + "_group"
    env = os.environ.copy()
    env.update({"AD_ACCEPTANCE_TEST_ENV": "isolated-test-accounts",
                "AD_TEST_A_PASSWORD": passwords["alice"],
                "AD_TEST_B_PASSWORD": passwords["bob"],
                "AD_TEST_BINDING_KEY_ID": runtime["key_id"],
                "AD_TEST_BINDING_TOKEN": runtime["token"]})
    result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=180)
    if result.stdout:
        print(result.stdout.strip())
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
        raise RuntimeError("synthetic LDAP HTTP matrix failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bootstrap", "matrix"))
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--team-folder", action="store_true",
                        help="bootstrap a real groupfolders 22.0.6 publication root")
    args = parser.parse_args()
    directory, state = owned_state(args.scratch)
    if args.action == "bootstrap":
        bootstrap(directory, state, args.team_folder)
    else:
        if args.team_folder:
            raise RuntimeError("--team-folder is only valid during bootstrap")
        matrix(directory, state)


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        # Credential-bearing HTTP bodies and subprocess arguments are omitted.
        print("Synthetic LDAP e2e failed: " + str(error), file=sys.stderr)
        sys.exit(1)
