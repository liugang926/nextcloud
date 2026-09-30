#!/usr/bin/env python3
"""Exercise one disposable synthetic file handoff, answer and revocation.

The marker-verified fixture is created by synthetic-ldap-fixture.py. This
probe never accepts a remote origin or arbitrary Compose project. It prints
only booleans, IDs and timing, never passwords, tokens, source text or SSE.
"""

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/integration_weknora/tests"))
from publication_http_smoke import login, request  # noqa: E402

owner = runpy.run_path(str(Path(__file__).with_name("synthetic-ldap-fixture.py")))
matrix = runpy.run_path(str(Path(__file__).with_name("ad-permission-acceptance.py")))
e2e = runpy.run_path(str(Path(__file__).with_name("synthetic-ldap-e2e.py")))
index = runpy.run_path(str(Path(__file__).with_name("local-indexed-withdrawal-smoke.py")))

MARKER = "ORCHID-QUARTZ-2749"
DENIED = {401, 403, 404}


def require(condition, step):
    if not condition:
        raise RuntimeError(step)


def status_for(nc_base, state, passwords, uid):
    session, csrf = login(nc_base, uid, passwords[uid])
    url = (nc_base + "/index.php/apps/integration_weknora/api/v1/files/" +
           str(state["file_id"]) + "/status")
    code, raw = request(session, url, headers={"requesttoken": csrf})
    if code == 200:
        return code, json.loads(raw)
    return code, None


def ask_target(wk_base, query, token):
    return e2e["http_json"](wk_base, "GET", "/api/v1/integrations/nextcloud/ask-target" +
                            query, token=token)


def knowledge(wk_base, knowledge_id, token):
    return e2e["http_json"](wk_base, "GET", "/api/v1/knowledge/" + knowledge_id,
                            token=token)


def wait_ldap_login(wk_base, user, password):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            token = matrix["weknora_login"](wk_base, user, password)
            if token:
                return token
        except matrix["ProbeError"]:
            pass
        time.sleep(2)
    raise RuntimeError("synthetic LDAP login did not recover after group mutation")


def source_link(status, wk_base, fixture):
    require(status["knowledge_state"] == "ready", "Nextcloud file is not indexed and ready")
    parsed = urllib.parse.urlsplit(status.get("weknora_ask_url") or "")
    require((parsed.scheme, parsed.netloc, parsed.path) ==
            (*urllib.parse.urlsplit(wk_base)[:2], "/platform/nextcloud-ask"),
            "Files sidebar link does not use the isolated WeKnora browser origin")
    selector = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
    require(set(selector) == {"instance_id", "binding_id", "file_id", "source_etag"} and
            all(len(v) == 1 for v in selector.values()) and
            selector["binding_id"] == [fixture["binding_id"]] and
            selector["file_id"] == [str(fixture["file_id"])] and
            selector["source_etag"] == [status["source_etag"]],
            "Files sidebar link has an unexpected source selector")
    return "?" + parsed.query


def citation_url(project, knowledge_id, file_id, nc_base):
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", knowledge_id) is not None,
            "invalid isolated knowledge ID")
    db = project + "-wk-db-1"
    raw = index["sql_json"](db, "SELECT to_jsonb(metadata->>'nextcloud_human_url') "
                            f"FROM knowledges WHERE id='{knowledge_id}'")
    require(isinstance(raw, str), "indexed document has no human citation URL")
    parsed = urllib.parse.urlsplit(raw)
    public = urllib.parse.urlsplit(nc_base)
    require((parsed.scheme, parsed.netloc) == (public.scheme, public.netloc) and
            parsed.path.endswith("/f/" + str(file_id)) and
            parsed.query == parsed.fragment == "" and
            "/apps/integration_weknora/api/" not in raw,
            "indexed citation is not the employee-facing Nextcloud Files URL")
    return raw


def sse(wk_base, session_id, token, knowledge_id, model_id):
    payload = json.dumps({"query": "What is the synthetic approval code?",
                          "knowledge_ids": [knowledge_id], "agent_enabled": False,
                          "summary_model_id": model_id, "channel": "web",
                          "disable_title": True}, separators=(",", ":")).encode()
    url = wk_base + "/api/v1/knowledge-chat/" + urllib.parse.quote(session_id)
    outgoing = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(outgoing, timeout=120) as response:
            require(response.status == 200, "question stream did not start")
            content = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        error.read()
        raise RuntimeError(f"question stream returned HTTP {error.code}") from None
    require(len(content) <= 2 * 1024 * 1024, "question stream exceeded probe bound")
    events = []
    for line in content.decode("utf-8", "replace").splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if raw and raw != "[DONE]":
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError:
                raise RuntimeError("question stream contains invalid JSON") from None
    return events


def answer_and_citation(wk_base, token, runtime, human_url):
    code, created = e2e["http_json"](wk_base, "POST", "/api/v1/sessions", {}, token)
    require(code == 201 and isinstance(created.get("data", {}).get("id"), str),
            "mapped employee could not create a personal session")
    session_id = created["data"]["id"]
    events = sse(wk_base, session_id, token, runtime["knowledge_id"],
                 runtime["chat_model_id"])
    # Inspect both the live event and its persistence. The deterministic model
    # answers the marker only when the selected source text reaches its prompt.
    require(any(MARKER in json.dumps(item, ensure_ascii=False) for item in events
                if item.get("response_type") == "answer"),
            "file-scoped answer did not contain the synthetic marker")
    refs = []
    for event in events:
        if event.get("response_type") == "references":
            data = event.get("data") or {}
            refs.extend(data.get("references") or event.get("knowledge_references") or [])
    require(refs, "file-scoped answer emitted no citation")
    require(all(ref.get("knowledge_id") == runtime["knowledge_id"] for ref in refs),
            "answer cited a document outside the selected file")
    require(any((ref.get("metadata") or {}).get("nextcloud_human_url") == human_url
                for ref in refs), "answer did not cite the original Files URL")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        history_code, history = e2e["http_json"](
            wk_base, "GET", f"/api/v1/messages/{session_id}/load?limit=20",
            token=token)
        if history_code == 200:
            raw_history = json.dumps(history, ensure_ascii=False)
            if MARKER in raw_history and human_url in raw_history:
                break
        time.sleep(1)
    else:
        raise RuntimeError("authorized answer and citation were not persisted in history")
    return session_id


def revoke_owned_grant(project):
    # This command can reach only the marker-verified disposable OpenLDAP
    # container. The bind password is expanded inside that container.
    ldif = ("dn: " + owner["GRANT_DN"] + "\nchangetype: modify\nreplace: member\n"
            "member: " + owner["DISABLED_DN"] + "\n")
    command = ["docker", "exec", "-i", project + "-openldap-1", "sh", "-c",
               '/opt/bitnami/openldap/bin/ldapmodify -x -H ldap://127.0.0.1:1389 '
               '-D "cn=admin,dc=example,dc=test" -w "$LDAP_ADMIN_PASSWORD"']
    result = subprocess.run(command, input=ldif, text=True, capture_output=True,
                            timeout=30, check=False)
    require(result.returncode == 0, "isolated LDAP grant mutation failed")


def run(scratch, revoke):
    directory, state = owner["owned_state"](scratch)
    require(state["mode"] == "direct", "handoff probe requires the direct synthetic grant")
    fixture = json.loads((directory / "fixture.json").read_text())
    runtime = json.loads((directory / "runtime.json").read_text())
    passwords = json.loads((directory / "passwords.json").read_text())
    nc_base = f"http://127.0.0.1:{state['ports']['nextcloud']}"
    wk_base = f"http://127.0.0.1:{state['ports']['weknora']}"
    alice_status, alice = status_for(nc_base, runtime, passwords, "alice")
    bob_status, _ = status_for(nc_base, runtime, passwords, "bob")
    require(alice_status == 200 and bob_status in {403, 404},
            "Files status did not follow the synthetic folder grant")
    query = source_link(alice, wk_base, fixture)
    alice_token = wait_ldap_login(wk_base, "alice", passwords["alice"])
    bob_token = wait_ldap_login(wk_base, "bob", passwords["bob"])
    require(alice_token and bob_token, "synthetic LDAP login did not issue both user tokens")
    anonymous, _ = ask_target(wk_base, query, None)
    allowed, target = ask_target(wk_base, query, alice_token)
    denied, _ = ask_target(wk_base, query, bob_token)
    require(anonymous == 401 and allowed == 200 and denied in {403, 404},
            "ask target did not enforce interactive mapped-user access")
    require(target.get("data", {}).get("knowledge_id") == runtime["knowledge_id"] and
            target["data"].get("knowledge_base_id") == runtime["knowledge_base_id"] and
            target["data"].get("source_etag") == alice["source_etag"],
            "ask target resolved a different source version")
    stale = urllib.parse.parse_qs(query[1:])
    stale["source_etag"] = ["stale-synthetic-etag"]
    stale_query = "?" + urllib.parse.urlencode(stale, doseq=True)
    stale_code, _ = ask_target(wk_base, stale_query, alice_token)
    require(stale_code in {403, 404}, "stale Files link exposed a target")
    human = citation_url(state["project"], runtime["knowledge_id"],
                         runtime["file_id"], nc_base)
    # The Files route serves an app shell even for a logged-in user lacking
    # the file; only DAV/status prove the per-user read. Here verify the
    # employee URL reaches the route and requires a Nextcloud login.
    route = urllib.request.Request(human, method="GET")
    try:
        with urllib.request.urlopen(route, timeout=20) as response:
            require(response.status == 200, "human Files route is not reachable")
    except urllib.error.HTTPError as error:
        error.read()
        require(error.code in {401, 302, 303}, "human Files route is not reachable")
    alice_session, csrf = login(nc_base, "alice", passwords["alice"])
    route_code, _ = request(alice_session, human, headers={"requesttoken": csrf})
    require(route_code == 200, "permitted employee cannot open the Files route")
    session_id = answer_and_citation(wk_base, alice_token, runtime, human)
    result = {"phase": "baseline", "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "alice_status": alice_status, "bob_status": bob_status,
              "alice_ask": allowed, "bob_ask": denied, "stale_ask": stale_code,
              "answer_marker": True, "citation_original": True, "session_id": session_id}
    print(json.dumps(result, separators=(",", ":")), flush=True)
    if not revoke:
        return
    revoke_owned_grant(state["project"])
    mutation_at = dt.datetime.now(dt.timezone.utc).isoformat()
    deadline = time.monotonic() + 150
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            current_status, _ = status_for(nc_base, runtime, passwords, "alice")
            current_ask, _ = ask_target(wk_base, query, alice_token)
            current_knowledge, _ = knowledge(wk_base, runtime["knowledge_id"], alice_token)
            direct = matrix["direct_content_probe"](
                wk_base, runtime["knowledge_id"], alice_token)
            search = matrix["search_probe"](wk_base, fixture, alice_token)
        except (OSError, ValueError, RuntimeError, matrix["ProbeError"]):
            time.sleep(3)
            continue
        if current_status in {403, 404} and current_ask in {403, 404} and \
                current_knowledge in {403, 404} and not direct and not search:
            history_code, history = e2e["http_json"](
                wk_base, "GET", f"/api/v1/messages/{session_id}/load?limit=20",
                token=alice_token)
            require(history_code in {200, 403, 404} and
                    (history_code != 200 or (MARKER not in json.dumps(history) and
                     human not in json.dumps(history))),
                    "revoked user still received the prior answer or citation")
            print(json.dumps({"phase": "revoked", "mutated_at_utc": mutation_at,
                              "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                              "polls": attempts, "alice_status": current_status,
                              "alice_ask": current_ask, "alice_knowledge": current_knowledge,
                              "alice_direct": False, "alice_search": False,
                              "history_redacted": True},
                             separators=(",", ":")))
            return
        time.sleep(3)
    raise RuntimeError("revoked Files/ask/knowledge reads did not converge")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    try:
        run(args.scratch, args.revoke)
    except (KeyError, OSError, ValueError, RuntimeError, matrix["ProbeError"]) as error:
        print("Synthetic ask handoff failed: " + str(error), file=sys.stderr)
        sys.exit(1)
