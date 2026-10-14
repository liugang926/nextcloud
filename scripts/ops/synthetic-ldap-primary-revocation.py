#!/usr/bin/env python3
"""Withdraw Alice's sole synthetic primary-group grant and verify old JWTs.

This acts only on a marker-verified, bootstrapped, disposable primary fixture.
Run `synthetic-ldap-fixture.py destroy` separately after inspecting the result.
"""

import argparse
import json
import os
from pathlib import Path
import re
import runpy
import selectors
import subprocess
import sys
import time
import uuid


OPS = Path(__file__).resolve().parent
owner = runpy.run_path(str(OPS / "synthetic-ldap-fixture.py"))
e2e = runpy.run_path(str(OPS / "synthetic-ldap-e2e.py"))


def primary_group_id(state):
    data = owner["ldap_search"](state, owner["A_DN"], "base",
                                "(objectClass=*)", "primaryGroupID")
    values = re.findall(r"^primaryGroupID: ([0-9]+)$", data, re.M)
    if len(values) != 1:
        raise RuntimeError("Alice's LDAP primaryGroupID is missing or ambiguous")
    return int(values[0])


def revoke_primary(state):
    if primary_group_id(state) != 2000:
        raise RuntimeError("Alice no longer has the expected sole primary group")
    mutation = ("dn: " + owner["A_DN"] + "\nchangetype: modify\n"
                "replace: primaryGroupID\nprimaryGroupID: 513\n\n")
    command = ["docker", "exec", "-i", state["project"] + "-openldap-1", "sh", "-c",
               '/opt/bitnami/openldap/bin/ldapmodify -x -H ldap://127.0.0.1:1389 '
               '-D "cn=admin,dc=example,dc=test" -w "$LDAP_ADMIN_PASSWORD"']
    subprocess.run(command, input=mutation, text=True, capture_output=True,
                   check=True, timeout=30)
    if primary_group_id(state) != 513:
        raise RuntimeError("synthetic primary-group mutation did not persist")


def weknora_primary_removed(state):
    alice = str(uuid.UUID(state["guids"]["alice"]))
    grant = str(uuid.UUID(state["guids"]["grant"]))
    query = ("SELECT i.primary_group_sid, "
             "(SELECT count(*) FROM directory_group_memberships m "
             "JOIN directory_groups g ON g.id=m.group_id "
             "WHERE m.identity_id=i.id AND g.object_guid='" + grant + "') "
             "FROM directory_identities i WHERE i.object_guid='" + alice + "';")
    command = ["docker", "exec", state["project"] + "-wk-db-1", "psql", "-U",
               "weknora", "-d", "weknora", "-Atc", query]
    result = subprocess.run(command, text=True, capture_output=True, check=True,
                            timeout=15)
    rows = result.stdout.strip().splitlines()
    return len(rows) == 1 and rows[0].endswith("-513|0")


def run_watcher(directory, state, passwords, runtime, timeout_seconds):
    env = os.environ.copy()
    env.update({"AD_ACCEPTANCE_TEST_ENV": "isolated-test-accounts",
                "AD_TEST_A_PASSWORD": passwords["alice"],
                "AD_TEST_B_PASSWORD": passwords["bob"],
                "AD_TEST_BINDING_KEY_ID": runtime["key_id"],
                "AD_TEST_BINDING_TOKEN": runtime["token"]})
    command = [sys.executable, str(OPS / "synthetic-ldap-revocation-watch.py"),
               "--fixture", str(directory / "fixture.json"),
               "--initial-case", "primary_group",
               "--target-case", "primary_group_removed",
               "--nextcloud-origin", "http://127.0.0.1:" +
               str(state["ports"]["nextcloud"]),
               "--weknora-origin", "http://127.0.0.1:" +
               str(state["ports"]["weknora"]),
               "--allow-loopback-http", "--timeout-seconds", str(timeout_seconds)]
    watcher = subprocess.Popen(command, env=env, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, bufsize=1)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(watcher.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=35):
                raise RuntimeError("baseline old-JWT watch did not become ready")
            ready = watcher.stdout.readline()
        if not ready or json.loads(ready).get("phase") != "baseline_ready":
            raise RuntimeError("old-JWT watch rejected the primary-group baseline")
        print(ready.strip(), flush=True)
        revoke_primary(state)
        print(json.dumps({"phase": "primary_group_changed", "new_primary_group_id": 513}),
              flush=True)
        output, error = watcher.communicate(timeout=timeout_seconds + 15)
        if watcher.returncode != 0:
            raise RuntimeError("old-JWT primary-group denial did not converge: " +
                               error.strip()[-400:])
        report = json.loads(output.strip())
        if report.get("phase") != "target_observed":
            raise RuntimeError("old-JWT watch returned no target denial evidence")
        print(json.dumps(report, separators=(",", ":")), flush=True)
    finally:
        if watcher.poll() is None:
            watcher.terminate()
            try:
                watcher.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                watcher.kill()
                watcher.communicate()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args()
    if not 20 <= args.timeout_seconds <= 300:
        raise RuntimeError("revocation timeout is outside the synthetic test bounds")
    directory, state = owner["owned_state"](args.scratch)
    owner["assert_owned_resources"](directory, state)
    if state["mode"] != "primary" or not (directory / "fixture.json").is_file():
        raise RuntimeError("a bootstrapped primary-group fixture is required")
    passwords = json.loads((directory / "passwords.json").read_text())
    runtime = json.loads((directory / "runtime.json").read_text())
    e2e["matrix"](directory, state)
    run_watcher(directory, state, passwords, runtime, args.timeout_seconds)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not weknora_primary_removed(state):
        time.sleep(2)
    if not weknora_primary_removed(state):
        raise RuntimeError("WeKnora directory still lists Alice in Engineering")
    print(json.dumps({"phase": "weknora_membership_removed", "alice_engineering": False}))
    e2e["matrix"](directory, state, "primary_group_removed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError,
            subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print("Synthetic primary-group revocation failed: " + str(error), file=sys.stderr)
        sys.exit(1)
