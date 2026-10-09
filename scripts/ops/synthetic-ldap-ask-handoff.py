#!/usr/bin/env python3
"""Exercise one disposable synthetic file handoff, answer and revocation.

The marker-verified fixture is created by synthetic-ldap-fixture.py. This
probe never accepts a remote origin or arbitrary Compose project. It prints
only booleans, IDs and timing, never passwords, tokens, source text or SSE.
"""

import argparse
import base64
import datetime as dt
import hashlib
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


def parse_sse_events(content):
    """Parse actual HTTP bytes; neither references nor transport EOF are answers."""
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise RuntimeError("question stream contains invalid UTF-8") from None
    events = []
    for line in lines:
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError("question stream contains invalid JSON") from None
        require(isinstance(event, dict), "question stream contains a non-object event")
        events.append(event)
    return events


def reference_metadata(refs, knowledge_id, human_url):
    require(isinstance(refs, list) and refs and all(isinstance(ref, dict) for ref in refs),
            "actual answer has no structured citations")
    require(all(ref.get("knowledge_id") == knowledge_id for ref in refs),
            "answer cited a document outside the selected file")
    require(any(isinstance(ref.get("metadata"), dict) and
                ref["metadata"].get("nextcloud_human_url") == human_url for ref in refs),
            "answer did not cite the original Files URL")
    return {"citation_sha256": hashlib.sha256(human_url.encode()).hexdigest(),
            "references_sha256": hashlib.sha256(json.dumps(
                refs, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()}


def completed_answer_metadata(row, knowledge_id, human_url):
    require(isinstance(row, dict) and row.get("role") == "assistant" and
            row.get("is_completed") is True and isinstance(row.get("id"), str) and row["id"],
            "actual saved answer is not a completed assistant")
    require(isinstance(row.get("request_id"), str) and row["request_id"] and
            isinstance(row.get("session_id"), str) and row["session_id"],
            "actual saved answer has no producer identity")
    content = row.get("content")
    require(isinstance(content, str) and MARKER in content,
            "actual saved assistant content lacks the controlled answer")
    result = reference_metadata(row.get("knowledge_references"), knowledge_id, human_url)
    result.update(message_id=row["id"], request_id=row["request_id"], session_id=row["session_id"],
                  answer_sha256=hashlib.sha256(content.encode()).hexdigest(),
                  answer_bytes=len(content.encode()))
    return result


def answer_stream_metadata(events, knowledge_id, human_url, saved_content=None, native_replay=False):
    require(isinstance(events, list) and all(isinstance(event, dict) for event in events),
            "actual answer stream events are invalid")
    require(not any(event.get("response_type") == "error" for event in events),
            "actual answer stream emitted an error")
    require(not any(event.get("finish_reason") == "incomplete" or
                    (isinstance(event.get("data"), dict) and event["data"].get("finish_reason") == "incomplete")
                    for event in events),
            "actual answer stream has an incomplete finish reason")
    complete = [event for event in events if event.get("response_type") == "complete"
                and event.get("done") is True]
    require(len(complete) == 1, "actual answer stream did not reach one successful complete terminal")
    complete_index = next(index for index, event in enumerate(events) if event is complete[0])
    business_types = {"answer", "references", "thinking", "tool_call", "tool_result", "reflection", "agent_query",
                      "user_message_injected", "context_compacted"}
    require(not any(event.get("response_type") in business_types | {"complete"} or event.get("content")
                    for event in events[complete_index + 1:]),
            "actual answer stream emitted business content after complete")
    business = [event for event in events if event.get("response_type") in business_types | {"complete"}]
    require(all(isinstance(event.get("id"), str) and event["id"] for event in business) and
            len({event["id"] for event in business}) == 1, "actual answer stream mixed producer request IDs")
    queries = [event for event in events if event.get("response_type") == "agent_query"]
    require(queries and all(isinstance(event.get("session_id"), str) and event["session_id"] and
                            isinstance(event.get("assistant_message_id"), str) and event["assistant_message_id"]
                            for event in queries), "actual answer stream has no producer message identity")
    require(len({(event["session_id"], event["assistant_message_id"]) for event in queries}) == 1,
            "actual answer stream mixed producer message IDs")
    answers = [event for event in events if event.get("response_type") == "answer"]
    require(answers and all(isinstance(event.get("content"), str) for event in answers),
            "actual answer stream has no text answer frames")
    content = "".join(event["content"] for event in answers)
    require(MARKER in content, "actual answer frames lack the controlled answer")
    data = complete[0].get("data") or {}
    require(isinstance(data, dict), "actual complete terminal data is invalid")
    if native_replay or "final_content" in data:
        require(data.get("final_content") == content,
                "actual native complete terminal differs from its answer frames")
    if saved_content is not None:
        require(content == saved_content, "actual replay answer differs from the saved original answer")
    refs = []
    for event in events:
        if event.get("response_type") == "references":
            ref_data = event.get("data") or {}
            require(isinstance(ref_data, dict), "actual reference event data is invalid")
            current = ref_data.get("references") or event.get("knowledge_references") or []
            require(isinstance(current, list), "actual reference event list is invalid")
            refs.extend(current)
    result = reference_metadata(refs, knowledge_id, human_url)
    result.update(answer_sha256=hashlib.sha256(content.encode()).hexdigest(),
                  answer_bytes=len(content.encode()), successful_complete_terminal=True,
                  request_id=business[0]["id"], session_id=queries[0]["session_id"],
                  message_id=queries[0]["assistant_message_id"])
    return result


def require_same_answer_material(stream, saved):
    require(all(stream.get(key) == saved.get(key) for key in
                ("message_id", "request_id", "session_id", "answer_sha256", "answer_bytes",
                 "citation_sha256", "references_sha256")),
            "actual stream differs from its saved original producer answer or citations")


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
    return parse_sse_events(content)


def answer_and_citation(wk_base, token, runtime, human_url):
    code, created = e2e["http_json"](wk_base, "POST", "/api/v1/sessions", {}, token)
    require(code == 201 and isinstance(created.get("data", {}).get("id"), str),
            "mapped employee could not create a personal session")
    session_id = created["data"]["id"]
    events = sse(wk_base, session_id, token, runtime["knowledge_id"],
                 runtime["chat_model_id"])
    # Inspect both the live event and its persistence. The deterministic model
    # answers the marker only when the selected source text reaches its prompt.
    actual_stream = answer_stream_metadata(events, runtime["knowledge_id"], human_url)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        history_code, history = e2e["http_json"](
            wk_base, "GET", f"/api/v1/messages/{session_id}/load?limit=20",
            token=token)
        if history_code == 200:
            rows = history.get("data") if isinstance(history, dict) else None
            completed = [row for row in rows if isinstance(row, dict) and row.get("role") == "assistant"
                         and row.get("is_completed") is True] if isinstance(rows, list) else []
            if len(completed) == 1:
                try:
                    saved = completed_answer_metadata(completed[0], runtime["knowledge_id"], human_url)
                except RuntimeError:
                    pass
                else:
                    require_same_answer_material(actual_stream, saved)
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


def revoke_source_share(nc_base, passwords, share_id):
    require(type(share_id) is int and share_id > 0,
            "isolated source share ID is invalid")
    admin, csrf = login(nc_base, "devadmin", passwords["nc_admin"])
    url = nc_base + "/ocs/v2.php/apps/files_sharing/api/v1/shares/" + str(share_id)
    status, raw = request(admin, url, "DELETE", {
        "requesttoken": csrf, "OCS-APIRequest": "true", "Accept": "application/json"})
    try:
        ocs = json.loads(raw)["ocs"]
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("source share deletion returned invalid OCS JSON") from None
    require(status == 200 and ocs.get("meta", {}).get("statuscode") == 200,
            "isolated Nextcloud source share deletion failed")


def deny_team_folder_file(state, team_folder_id):
    require(type(team_folder_id) is int and team_folder_id > 0,
            "isolated Team Folder ID is invalid")
    e2e["occ"](state, "groupfolders:permissions", str(team_folder_id), "--enable")
    e2e["occ"](state, "groupfolders:permissions", str(team_folder_id),
               "--user=alice", "acl-note.txt", "--", "-read")


def source_context_unchanged(state, nc_base, passwords, runtime, team_acl=False):
    groups = json.loads(e2e["occ"](state, "group:list", "--output=json"))
    require("alice" in groups.get("Engineering", []),
            "source-only denial also lost Alice's Engineering membership")
    wk_base = f"http://127.0.0.1:{state['ports']['weknora']}"
    require(wait_ldap_login(wk_base, "alice", passwords["alice"]),
        "source-only denial also disabled Alice's LDAP account")
    admin_code, admin_login = e2e["http_json"](wk_base, "POST", "/api/v1/auth/login", {
        "email": "synthetic-admin@example.test", "password": passwords["wk_admin"]})
    admin_token = admin_login.get("token")
    require(admin_code == 200 and isinstance(admin_token, str) and admin_token,
            "source-only denial could not inspect the WeKnora KB grant")
    policy_code, policy = e2e["http_json"](
        wk_base, "GET", "/api/v1/group-access/knowledge_base/" +
        runtime["knowledge_base_id"], token=admin_token)
    access = policy.get("data", {})
    require(policy_code == 200 and access.get("mode") == "restricted" and
            any(grant.get("display_name") == "Engineering" and
                grant.get("permission") == "read" and
                grant.get("directory_id") == state["directory_id"]
                for grant in access.get("grants", [])),
            "source-only denial also lost the WeKnora Engineering KB grant")
    owner_file = nc_base + "/remote.php/dav/files/devadmin/Published/acl-note.txt"
    owner_auth = base64.b64encode(
        ("devadmin:" + passwords["nc_admin"]).encode()).decode()
    require(e2e["file_id"](owner_file, {"Authorization": "Basic " + owner_auth}) ==
            runtime["file_id"], "source-only denial also removed the owner's file")
    if team_acl:
        alice_root = nc_base + "/remote.php/dav/files/alice/Published"
        alice_auth = base64.b64encode(
            ("alice:" + passwords["alice"]).encode()).decode()
        require(e2e["file_id"](
            alice_root, {"Authorization": "Basic " + alice_auth}) ==
                runtime["root_file_id"],
                "Team Folder ACL also removed Alice's root folder access")


def run(scratch, revoke, revoke_source_share_only, deny_team_acl):
    directory, state = owner["owned_state"](scratch)
    require(state["mode"] == "direct", "handoff probe requires the direct synthetic grant")
    fixture = json.loads((directory / "fixture.json").read_text())
    runtime = json.loads((directory / "runtime.json").read_text())
    root_kind = runtime.get("publication_root", "group_share")
    require((deny_team_acl and root_kind == "team_folder") or
            (not deny_team_acl and root_kind == "group_share"),
            "revocation mode does not match the disposable publication root")
    passwords = json.loads((directory / "passwords.json").read_text())
    nc_base = f"http://127.0.0.1:{state['ports']['nextcloud']}"
    wk_base = f"http://127.0.0.1:{state['ports']['weknora']}"
    alice_status, alice = status_for(nc_base, runtime, passwords, "alice")
    bob_status, _ = status_for(nc_base, runtime, passwords, "bob")
    require(alice_status == 200 and bob_status in {403, 404},
            "Files status did not follow the synthetic folder grant")
    alice_account = fixture["accounts"]["a"]
    alice_login, alice_dav = matrix["dav_probe"](
        nc_base, alice_account, passwords["alice"], runtime["file_id"])
    alice_source = matrix["source_probe"](
        nc_base, fixture, alice_account, runtime["key_id"], runtime["token"])
    require(alice_login and alice_dav and alice_source,
            "Alice's baseline DAV/source read was not authorized")
    browser_port = state["ports"].get("weknora_ui", state["ports"]["weknora"])
    browser_base = f"http://127.0.0.1:{browser_port}"
    query = source_link(alice, browser_base, fixture)
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
    if not revoke and not revoke_source_share_only and not deny_team_acl:
        return
    if deny_team_acl:
        deny_team_folder_file(state, runtime["team_folder_id"])
    elif revoke_source_share_only:
        revoke_source_share(nc_base, passwords, runtime["share_id"])
    else:
        revoke_owned_grant(state["project"])
    mutation_at = dt.datetime.now(dt.timezone.utc).isoformat()
    deadline = time.monotonic() + 150
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            current_status, _ = status_for(nc_base, runtime, passwords, "alice")
            current_login, current_dav = matrix["dav_probe"](
                nc_base, alice_account, passwords["alice"], runtime["file_id"])
            current_source = matrix["source_probe"](
                nc_base, fixture, alice_account, runtime["key_id"], runtime["token"])
            current_ask, _ = ask_target(wk_base, query, alice_token)
            current_knowledge, _ = knowledge(wk_base, runtime["knowledge_id"], alice_token)
            direct = matrix["direct_content_probe"](
                wk_base, runtime["knowledge_id"], alice_token)
            search = matrix["search_probe"](wk_base, fixture, alice_token)
        except (OSError, ValueError, RuntimeError, matrix["ProbeError"]):
            time.sleep(3)
            continue
        if current_login and not current_dav and not current_source and \
                current_status in {403, 404} and current_ask in {403, 404} and \
                current_knowledge in {403, 404} and not direct and not search:
            bob_status_after, _ = status_for(nc_base, runtime, passwords, "bob")
            bob_account = fixture["accounts"]["b"]
            bob_login, bob_dav = matrix["dav_probe"](
                nc_base, bob_account, passwords["bob"], runtime["file_id"])
            bob_source = matrix["source_probe"](
                nc_base, fixture, bob_account, runtime["key_id"], runtime["token"])
            bob_ask_after, _ = ask_target(wk_base, query, bob_token)
            bob_knowledge, _ = knowledge(wk_base, runtime["knowledge_id"], bob_token)
            bob_direct = matrix["direct_content_probe"](
                wk_base, runtime["knowledge_id"], bob_token)
            bob_search = matrix["search_probe"](wk_base, fixture, bob_token)
            require(bob_login and not bob_dav and not bob_source and
                    bob_status_after in {403, 404} and bob_ask_after in {403, 404} and
                    bob_knowledge in {403, 404} and not bob_direct and not bob_search,
                    "Bob gained a source or WeKnora read after Alice's revocation")
            history_code, history = e2e["http_json"](
                wk_base, "GET", f"/api/v1/messages/{session_id}/load?limit=20",
                token=alice_token)
            require(history_code in {200, 403, 404} and
                    (history_code != 200 or (MARKER not in json.dumps(history) and
                     human not in json.dumps(history))),
                    "revoked user still received the prior answer or citation")
            if revoke_source_share_only or deny_team_acl:
                source_context_unchanged(state, nc_base, passwords, runtime,
                                         team_acl=deny_team_acl)
            phase = ("team_acl_denied" if deny_team_acl else
                     "source_share_revoked" if revoke_source_share_only else "revoked")
            print(json.dumps({"phase": phase, "mutated_at_utc": mutation_at,
                              "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                              "polls": attempts, "alice_status": current_status,
                              "alice_dav": current_dav, "alice_source": current_source,
                              "alice_ask": current_ask, "alice_knowledge": current_knowledge,
                              "alice_direct": False, "alice_search": False,
                              "bob_dav": bob_dav, "bob_source": bob_source,
                              "bob_ask": bob_ask_after, "bob_search": bob_search,
                              "history_redacted": True},
                             separators=(",", ":")))
            return
        time.sleep(3)
    raise RuntimeError("revoked Files/ask/knowledge reads did not converge")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=True, type=Path)
    revocation = parser.add_mutually_exclusive_group()
    revocation.add_argument("--revoke", action="store_true",
                            help="remove Alice's sole synthetic LDAP group grant")
    revocation.add_argument("--revoke-source-share", action="store_true",
                            help="delete only the owned Nextcloud group share")
    revocation.add_argument("--deny-team-acl", action="store_true",
                            help="apply a real Team Folder file ACL deny to Alice")
    args = parser.parse_args()
    try:
        run(args.scratch, args.revoke, args.revoke_source_share, args.deny_team_acl)
    except (KeyError, OSError, ValueError, RuntimeError, subprocess.CalledProcessError,
            matrix["ProbeError"]) as error:
        print("Synthetic ask handoff failed: " + str(error), file=sys.stderr)
        sys.exit(1)
