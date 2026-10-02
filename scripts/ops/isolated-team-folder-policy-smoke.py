#!/usr/bin/env python3
"""Check V1 ACL publication gates in an owned synthetic Team Folder fixture.

Prepare/up an isolated synthetic LDAP fixture and bootstrap it with
`synthetic-ldap-e2e.py bootstrap --team-folder` before running this probe.
The generator owns all volumes, ports, credentials and cleanup.
"""

import argparse
import base64
import json
from pathlib import Path
import runpy
import subprocess
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
owner = runpy.run_path(str(Path(__file__).with_name("synthetic-ldap-fixture.py")))
e2e = runpy.run_path(str(Path(__file__).with_name("synthetic-ldap-e2e.py")))
helper = runpy.run_path(str(ROOT / "apps/integration_weknora/tests/publication_http_smoke.py"))
files = runpy.run_path(str(ROOT / "apps/integration_weknora/tests/changes_http_smoke.py"))


def expect(status, wanted, label):
    if status != wanted:
        raise RuntimeError(f"{label}: HTTP {status}, expected {wanted}")


def run(scratch):
    directory, state = owner["owned_state"](scratch)
    runtime = json.loads((directory / "runtime.json").read_text())
    passwords = json.loads((directory / "passwords.json").read_text())
    if runtime.get("publication_root") != "team_folder" or not isinstance(
            runtime.get("team_folder_id"), int):
        raise RuntimeError("fixture must be bootstrapped with --team-folder")
    base = f"http://127.0.0.1:{state['ports']['nextcloud']}"
    api = base + "/index.php/apps/integration_weknora/api/v1"
    binding = runtime["binding_id"]
    file_id = runtime["file_id"]
    folder_id = runtime["team_folder_id"]
    admin, csrf = helper["login"](base, "devadmin", passwords["nc_admin"])
    reader = urllib.request.build_opener()
    machine = {"Authorization": "Bearer " + runtime["token"],
               "X-WeKnora-Key-Id": runtime["key_id"]}
    target = f"{api}/bindings/{binding}"
    identity = {"directory_id": state["directory_id"],
                "object_guid": state["guids"]["alice"], "file_id": file_id}
    body = json.dumps(identity).encode()

    def source():
        return helper["request"](reader, target + "/authorize", "POST",
                                 {**machine, "Content-Type": "application/json"}, body)

    def transition(action):
        return helper["request"](admin, f"{api}/admin/bindings/{binding}/{action}",
                                 "POST", {"requesttoken": csrf}, b"")

    def save(binding_id, root_id):
        return helper["request"](admin, api + "/admin/bindings", "POST",
                                 {"requesttoken": csrf, "Content-Type": "application/json"},
                                 json.dumps({"id": binding_id, "name": binding_id,
                                             "owner_uid": "devadmin",
                                             "root_file_id": root_id}).encode())

    def listed_binding():
        status, listed = helper["request"](admin, api + "/admin/bindings",
                                           headers={"requesttoken": csrf})
        expect(status, 200, "list binding")
        return next(x for x in json.loads(listed)["bindings"] if x["id"] == binding)

    def diagnostics():
        status, response = helper["request"](admin, api + "/admin/diagnostics",
                                             headers={"requesttoken": csrf})
        expect(status, 200, "administrator diagnostics")
        return json.loads(response)

    def automatic_stop_count():
        audit = subprocess.run([
            "docker", "exec", state["project"] + "-nc-db-1", "psql", "-U", "nextcloud",
            "-d", "nextcloud", "-Atc",
            "SELECT count(*) FROM oc_weknora_bind_pub_audit "
            "WHERE binding_id='synthetic-published' AND action='stop' "
            "AND actor_uid='acl-policy'"],
            check=True, text=True, capture_output=True, timeout=30)
        return int(audit.stdout.strip())

    expect(helper["request"](reader, target + "/manifest", headers=machine)[0],
           200, "uniform Team Folder manifest")
    expect(helper["request"](reader, f"{target}/files/{file_id}/content",
                             headers=machine)[0], 200, "uniform Team Folder content")
    status, allowed = source()
    expect(status, 200, "uniform Team Folder source authorization")
    if json.loads(allowed)["allow"] is not True:
        raise RuntimeError("uniform Team Folder denied its authorized reader")
    starting_epoch = listed_binding()["publication_epoch"]
    starting_stops = automatic_stop_count()
    if diagnostics()["bindings_with_unsupported_acl"]:
        raise RuntimeError("uniform Team Folder was reported as unsupported")

    second_folder = None
    advanced = False
    stopped = False
    try:
        # This folder is intentionally not bound. Its advanced ACL must make
        # the initial binding operation fail before a machine key can exist.
        second_folder = int(e2e["occ"](state, "groupfolders:create", "--output=json",
                                       "PolicyReject").strip())
        e2e["occ"](state, "groupfolders:group", str(second_folder),
                   "SyntheticPublisher", "read", "write", "share", "delete")
        second_dav = base + "/remote.php/dav/files/devadmin/PolicyReject"
        auth = base64.b64encode(("devadmin:" + passwords["nc_admin"]).encode()).decode()
        second_id = files["file_id"](second_dav, {"Authorization": "Basic " + auth})
        e2e["occ"](state, "groupfolders:permissions", str(second_folder), "--enable")
        status, error = save("policy-reject", second_id)
        expect(status, 409, "reject advanced ACL at binding creation")
        if json.loads(error).get("error") != "unsupported_source_acl":
            raise RuntimeError("binding rejection did not identify unsupported ACL")

        e2e["occ"](state, "groupfolders:permissions", str(folder_id), "--enable")
        advanced = True
        e2e["occ"](state, "groupfolders:permissions", str(folder_id),
                   "--user=alice", "acl-note.txt", "--", "-read")
        status, error = save(binding, runtime["root_file_id"])
        expect(status, 409, "reject advanced ACL on binding update")
        if json.loads(error).get("error") != "unsupported_source_acl":
            raise RuntimeError("binding update did not identify unsupported ACL")

        expect(helper["request"](reader, target + "/manifest", headers=machine)[0],
               503, "manifest discovers and stops advanced ACL")
        item = listed_binding()
        if item["publication_state"] != "stopped" or item["publication_epoch"] != starting_epoch + 1:
            raise RuntimeError("advanced ACL did not durably stop publication")
        health = diagnostics()
        if health["binding_roots_available"] is not False or \
                binding not in health["bindings_with_unsupported_acl"]:
            raise RuntimeError("administrator diagnostics did not identify the unsafe binding")
        if automatic_stop_count() != starting_stops + 1:
            raise RuntimeError("automatic ACL stop has no unique audit record")
        expect(helper["request"](reader, f"{target}/files/{file_id}/content",
                                 headers=machine)[0], 423,
               "stopped advanced ACL binding blocks machine content")
        status, denied = source()
        expect(status, 200, "stopped advanced ACL source authorization")
        if json.loads(denied).get("allow") is not False:
            raise RuntimeError("stopped advanced ACL still authorized source")
        stopped = True
        status, error = transition("resume")
        expect(status, 409, "reject resume with advanced ACL")
        if json.loads(error).get("error") != "unsupported_source_acl":
            raise RuntimeError("resume rejection did not identify unsupported ACL")
        item = listed_binding()
        if item["publication_state"] != "stopped":
            raise RuntimeError("failed resume reopened publication")

        e2e["occ"](state, "groupfolders:permissions", str(folder_id), "--disable")
        advanced = False
        expect(transition("resume")[0], 200, "resume uniform Team Folder")
        stopped = False
        expect(helper["request"](reader, target + "/manifest", headers=machine)[0],
               200, "manifest after ACL mode disabled")
        status, allowed = source()
        expect(status, 200, "authorization after ACL mode disabled")
        if json.loads(allowed)["allow"] is not True:
            raise RuntimeError("uniform Team Folder did not recover")
        if diagnostics()["bindings_with_unsupported_acl"]:
            raise RuntimeError("disabled ACL still appears in admin diagnostics")
        print("isolated Team Folder publication ACL policy passed")
    finally:
        if advanced:
            e2e["occ"](state, "groupfolders:permissions", str(folder_id), "--disable")
        if stopped or listed_binding()["publication_state"] == "stopped":
            transition("resume")
        if second_folder is not None:
            e2e["occ"](state, "groupfolders:delete", str(second_folder), "--force")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=True, type=Path)
    args = parser.parse_args()
    run(args.scratch)
