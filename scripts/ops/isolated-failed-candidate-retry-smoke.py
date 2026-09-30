#!/usr/bin/env python3
"""Prove a failed V2 candidate retries automatically and restores an answer.

The script owns a fresh synthetic LDAP Compose project bound only to loopback.
It first publishes V1, then stops only that project's mock embedding service,
overwrites the same Nextcloud file, and waits for a durable failed V2 candidate.
The service is restarted before observing automatic retry. ``inject-only``
validates fault injection with an older image that lacks the retry worker;
``full`` additionally requires strict-latest denial and autonomous recovery.
The owned project, volumes, network and private credentials are always removed.
"""

import argparse
import base64
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time
import urllib.parse
import uuid


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "apps/integration_weknora/tests"))
from changes_http_smoke import file_id, request as dav_request  # noqa: E402

owner = runpy.run_path(str(HERE / "synthetic-ldap-fixture.py"))
e2e = runpy.run_path(str(HERE / "synthetic-ldap-e2e.py"))
handoff = runpy.run_path(str(HERE / "synthetic-ldap-ask-handoff.py"))
events = runpy.run_path(str(HERE / "isolated-event-rebind-smoke.py"))
index = runpy.run_path(str(HERE / "local-indexed-withdrawal-smoke.py"))
version_probe = runpy.run_path(str(HERE / "isolated-move-trash-restore-smoke.py"))

ANSWER = "ORCHID-QUARTZ-2749"
STALE_DENIED = {400, 403, 404, 409}
ACL_DENIED = {403, 404}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def command(*args, timeout=90):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout,
                            check=False)
    if result.returncode:
        # Docker and HTTP error bodies can contain source data or credentials.
        raise RuntimeError(f"isolated command {args[0]} failed (exit {result.returncode})")
    return result.stdout


def owned_compose(directory, state, verb, *services, timeout=90):
    owner["owned_state"](directory)
    owner["assert_owned_resources"](directory, state)
    return command(*owner["compose_command"](directory, state, verb, *services),
                   timeout=timeout)


def model_health(directory, state):
    container = state["project"] + "-mock-embedding-1"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = subprocess.run(["docker", "exec", container, "python", "-c",
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"],
            text=True, capture_output=True, timeout=8, check=False)
        if result.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError("owned mock embedding service did not recover")


def version(database, source_id, file_number):
    return version_probe["version"](database, source_id, file_number)


def old_copy(database, knowledge_id):
    require(re.fullmatch(r"[0-9a-fA-F-]{36}", knowledge_id) is not None,
            "invalid owned knowledge ID")
    query = ("SELECT jsonb_build_object('exists',true,'deleted',deleted_at IS NOT NULL,"
             "'parse',parse_status,'enabled',enable_status,"
             "'etag',metadata->>'nextcloud_etag') FROM knowledges "
             f"WHERE id='{knowledge_id}'")
    return index["sql_json"](database, query)


def wait_failed_candidate(state, runtime, event_id, delivery_job):
    database = state["project"] + "-wk-db-1"
    container = state["project"] + "-nextcloud-1"
    deadline = time.monotonic() + 240
    observed = None
    while time.monotonic() < deadline:
        events["deliver"](container, delivery_job)
        try:
            observed = version(database, runtime["source_id"], runtime["file_id"])
        except (RuntimeError, ValueError):
            observed = None
        if (observed and observed.get("state") == "staging" and
                observed.get("parse") == "failed" and
                observed.get("candidate_id") and
                observed["candidate_id"] != runtime["knowledge_id"] and
                observed.get("desired_etag") and
                observed["desired_etag"] != runtime["source_etag"]):
            return observed
        time.sleep(3)
    raise RuntimeError(f"V2 never reached staging/failed after outbox event {event_id}")


def retry_status(wk_base, token, runtime, *, allow_processing=False):
    path = ("/api/v1/datasource/nextcloud-source-pairings/" +
            urllib.parse.quote(runtime["operation_id"], safe="") +
            f"/candidates/{runtime['file_id']}/retry")
    code, body = e2e["http_json"](wk_base, "GET", path, token=token)
    if allow_processing and code == 409:
        # A new candidate has replaced the failed one, so the failed-only
        # endpoint correctly stops presenting the old candidate as retryable.
        return None
    require(code == 200 and isinstance(body.get("retry"), dict),
            f"candidate retry status unavailable: HTTP {code}")
    return body["retry"]


def strict_latest_denial(nc_base, wk_base, passwords, runtime, alice):
    code, status = handoff["status_for"](
        nc_base, {"file_id": runtime["file_id"]}, passwords, "alice")
    require(code == 200 and isinstance(status, dict) and
            status.get("knowledge_state") != "ready",
            "failed V2 still advertises a ready V1 answer")
    old_selector = runtime["v1_selector"]
    ask_code, _ = handoff["ask_target"](wk_base, old_selector, alice)
    old_code, _ = handoff["knowledge"](wk_base, runtime["knowledge_id"], alice)
    require(ask_code in STALE_DENIED and old_code in ACL_DENIED,
            f"failed V2 exposed V1 retrieval (ask={ask_code}, knowledge={old_code})")
    search_code, search = e2e["http_json"](
        wk_base, "POST", "/api/v1/knowledge-search",
        {"query": ANSWER, "knowledge_ids": [runtime["knowledge_id"]]},
        token=alice)
    if search_code == 200:
        require(search.get("success") is True and
                isinstance(search.get("data"), list) and
                not any(item.get("knowledge_id") == runtime["knowledge_id"]
                        for item in search["data"]),
                "failed V2 exposed a V1 search result")
    else:
        require(search_code in ACL_DENIED,
                f"failed V2 search returned unexpected HTTP {search_code}")
    session_code, created = e2e["http_json"](
        wk_base, "POST", "/api/v1/sessions", {}, token=alice)
    require(session_code == 201 and isinstance(created.get("data", {}).get("id"), str),
            "could not create isolated failed-V2 answer session")
    try:
        stale_events = handoff["sse"](
            wk_base, created["data"]["id"], alice, runtime["knowledge_id"],
            runtime["chat_model_id"])
    except RuntimeError as error:
        match = re.fullmatch(r"question stream returned HTTP ([0-9]{3})", str(error))
        require(match is not None and int(match.group(1)) in STALE_DENIED,
                "failed V2 answer stream failed unexpectedly")
        stale_events = []
    require(not any(ANSWER in json.dumps(item, ensure_ascii=False)
                    for item in stale_events if item.get("response_type") == "answer") and
            not any(ref.get("knowledge_id") == runtime["knowledge_id"]
                    for item in stale_events if item.get("response_type") == "references"
                    for ref in ((item.get("data") or {}).get("references") or
                                item.get("knowledge_references") or [])),
            "failed V2 streamed a V1 answer or citation")
    return {"files_state": status.get("knowledge_state"),
            "old_ask_http": ask_code, "old_knowledge_http": old_code,
            "old_search_http": search_code, "old_answer_suppressed": True}


def wait_recovered(wk_base, token, state, runtime, failed_id):
    database = state["project"] + "-wk-db-1"
    deadline = time.monotonic() + 15 * 60
    max_attempts = 0
    last_state = ""
    while time.monotonic() < deadline:
        current = version(database, runtime["source_id"], runtime["file_id"])
        if (current.get("state") == "published" and
                current.get("candidate_id") not in {None, "", failed_id,
                                                     runtime["knowledge_id"]} and
                current.get("parse") == "completed" and
                current.get("enabled") == "enabled" and
                current.get("desired_etag") == runtime["v2_etag"] and
                current.get("visible_etag") == runtime["v2_etag"] and
                current.get("ready_chunks", 0) >= 1 and
                current.get("embeddings", 0) >= 1):
            return current, max_attempts
        status = retry_status(wk_base, token, runtime, allow_processing=True)
        if status is None:
            progressing = version(database, runtime["source_id"], runtime["file_id"])
            require(progressing.get("state") in {"staging", "published"} and
                    progressing.get("candidate_id") not in {None, "", failed_id},
                    "retry endpoint conflicted without a new staged candidate")
            time.sleep(5)
            continue
        require(status.get("file_id") in {runtime["file_id"], str(runtime["file_id"])},
                "retry status file identity changed")
        require(status.get("source_etag") == runtime["v2_etag"] and
                status.get("candidate_id") == failed_id,
                "retry status lost exact failed candidate identity")
        require(status.get("state") != "manual",
                "automatic retry exhausted without publication")
        count = status.get("attempt_count")
        require(type(count) is int and count >= 0, "retry attempt count is invalid")
        max_attempts = max(max_attempts, count)
        last_state = status.get("state", "")
        time.sleep(5)
    raise RuntimeError(f"automatic V2 retry timed out (last state {last_state})")


def answer_restored(nc_base, wk_base, passwords, state, runtime, alice, bob,
                    published_id, v2_marker):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        code, status = handoff["status_for"](
            nc_base, {"file_id": runtime["file_id"]}, passwords, "alice")
        if (code == 200 and status.get("knowledge_state") == "ready" and
                status.get("source_etag") == runtime["v2_etag"]):
            break
        time.sleep(2)
    else:
        raise RuntimeError("Nextcloud Files status did not recover for V2")
    fixture = {"binding_id": runtime["binding_id"], "file_id": runtime["file_id"]}
    selector = handoff["source_link"](status, wk_base, fixture)
    ask_code, target = handoff["ask_target"](wk_base, selector, alice)
    bob_code, _ = handoff["ask_target"](wk_base, selector, bob)
    require(ask_code == 200 and bob_code in ACL_DENIED and
            target.get("data", {}).get("knowledge_id") == published_id,
            "V2 file-scoped answer target did not recover with source ACL")
    database = state["project"] + "-wk-db-1"
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", published_id) is not None and
            re.fullmatch(r"RETRY-V2-[0-9a-f]{16}", v2_marker) is not None,
            "invalid V2 proof identity")
    marker_found = index["sql_json"](
        database, "SELECT to_jsonb(EXISTS(SELECT 1 FROM chunks c "
        f"WHERE c.knowledge_id='{published_id}' AND c.content LIKE '%{v2_marker}%' "
        "AND c.deleted_at IS NULL AND c.is_enabled AND c.index_status='ready'))")
    require(marker_found is True, "recovered V2 marker is absent from ready chunks")
    human = handoff["citation_url"](state["project"], published_id,
                                    runtime["file_id"], nc_base)
    handoff["answer_and_citation"](
        wk_base, alice, {"knowledge_id": published_id,
                         "chat_model_id": runtime["chat_model_id"]}, human)
    return {"alice_ask_http": ask_code, "bob_ask_http": bob_code,
            "v2_chunk_marker": True, "answer_and_citation": True}


def drill(directory, state, *, phase):
    require(state["mode"] == "direct" and (directory / "fixture.json").is_file(),
            "fault drill needs its bootstrapped direct fixture")
    passwords = json.loads((directory / "passwords.json").read_text())
    runtime = json.loads((directory / "runtime.json").read_text())
    nc_base = f"http://127.0.0.1:{state['ports']['nextcloud']}"
    wk_base = f"http://127.0.0.1:{state['ports']['weknora']}"
    database = state["project"] + "-wk-db-1"
    baseline = version_probe["published"](database, runtime["source_id"],
                                          runtime["file_id"])
    require(baseline["candidate_id"] == runtime["knowledge_id"],
            "bootstrap did not publish the expected V1 source")
    runtime["source_etag"] = baseline["desired_etag"]
    code, auth = e2e["http_json"](wk_base, "POST", "/api/v1/auth/login", {
        "email": "synthetic-admin@example.test", "password": passwords["wk_admin"]})
    require(code == 200 and isinstance(auth.get("token"), str),
            "isolated administrator login failed")
    token = auth["token"]
    alice = handoff["wait_ldap_login"](wk_base, "alice", passwords["alice"])
    bob = handoff["wait_ldap_login"](wk_base, "bob", passwords["bob"])
    if phase == "full":
        code, status = handoff["status_for"](
            nc_base, {"file_id": runtime["file_id"]}, passwords, "alice")
        require(code == 200 and status.get("knowledge_state") == "ready",
                "V1 was not ready before controlled failure")
        fixture = {"binding_id": runtime["binding_id"], "file_id": runtime["file_id"]}
        runtime["v1_selector"] = handoff["source_link"](status, wk_base, fixture)
        human = handoff["citation_url"](state["project"], runtime["knowledge_id"],
                                        runtime["file_id"], nc_base)
        handoff["answer_and_citation"](wk_base, alice, runtime, human)

    dav_url = nc_base + "/remote.php/dav/files/devadmin/Published/acl-note.txt"
    basic = base64.b64encode(("devadmin:" + passwords["nc_admin"]).encode()).decode()
    dav_headers = {"Authorization": "Basic " + basic, "Content-Type": "text/plain"}
    v2_marker = "RETRY-V2-" + uuid.uuid4().hex[:16]
    updated = ("Which synthetic approval code is in this updated document? "
               "The synthetic approval code is " + ANSWER + ". "
               "This is the new version " + v2_marker + ".\n").encode()
    nextcloud_container = state["project"] + "-nextcloud-1"
    delivery_job = events["background_job"](nextcloud_container,
        r"OCA\IntegrationWeknora\BackgroundJob\EventDeliveryJob")
    applied_job = events["background_job"](nextcloud_container,
        r"OCA\IntegrationWeknora\BackgroundJob\EventAppliedStatusJob")
    embedding_stopped = False
    try:
        owned_compose(directory, state, "stop", "mock-embedding")
        embedding_stopped = True
        put_code, _ = dav_request(dav_url, "PUT", dav_headers, updated)
        require(put_code in {201, 204} and
                file_id(dav_url, dav_headers) == runtime["file_id"],
                "V2 overwrite failed or changed source file ID")
        event_id = version_probe["event_id"](
            state, runtime["binding_id"], runtime["file_id"], "upsert")
        failed = wait_failed_candidate(state, runtime, event_id, delivery_job)
        runtime["v2_etag"] = failed["desired_etag"]
        failed_id = failed["candidate_id"]
        require(failed.get("enabled") != "enabled",
                "failed V2 candidate was enabled for retrieval")
        copy = old_copy(database, runtime["knowledge_id"])
        require(copy["exists"] and not copy["deleted"] and
                copy["parse"] == "completed",
                "V1 recovery copy was destroyed during V2 failure")
        version_probe["source_content"](
            nc_base, runtime, runtime["file_id"], {200}, updated)
        fault = {"event_id": event_id, "file_id": runtime["file_id"],
                 "v1_candidate_id": runtime["knowledge_id"],
                 "failed_candidate_id": failed_id,
                 "staging_failed": True, "v1_recovery_copy_retained": True}
    finally:
        if embedding_stopped:
            owned_compose(directory, state, "start", "mock-embedding")
            model_health(directory, state)

    if phase == "inject-only":
        return {"phase": phase, "project": state["project"],
                "image_id": state["weknora_image_id"], "fault": fault,
                "embedding_restored": True}

    initial_retry = retry_status(wk_base, token, runtime)
    require(initial_retry.get("source_etag") == runtime["v2_etag"] and
            initial_retry.get("candidate_id") == failed_id,
            "retry endpoint did not identify exact failed V2")
    require(type(initial_retry.get("attempt_count")) is int and
            initial_retry["attempt_count"] >= 0 and
            isinstance(initial_retry.get("next_attempt_at"), str) and
            bool(initial_retry["next_attempt_at"]),
            "retry endpoint did not expose a scheduled automatic attempt")
    denial = strict_latest_denial(nc_base, wk_base, passwords, runtime, alice)
    recovered, attempts = wait_recovered(wk_base, token, state, runtime, failed_id)
    require(recovered["candidate_id"] != runtime["knowledge_id"],
            "automatic retry republished the V1 recovery copy")
    nc_admin, csrf = handoff["login"](nc_base, "devadmin", passwords["nc_admin"])
    event_url = (nc_base + "/index.php/apps/integration_weknora/api/v1/admin/bindings/" +
                 urllib.parse.quote(runtime["binding_id"], safe="") + "/event-connection")
    sender, receiver = events["wait_applied"](
        state, nc_admin, csrf, event_url, wk_base, token, runtime["source_id"],
        nextcloud_container, delivery_job, applied_job, event_id)
    answer = answer_restored(nc_base, wk_base, passwords, state, runtime,
                             alice, bob, recovered["candidate_id"], v2_marker)
    return {"phase": phase, "project": state["project"],
            "image_id": state["weknora_image_id"], "fault": fault,
            "retry": {"automatic": True, "max_observed_attempt_count": attempts,
                      "initial_next_attempt_at": initial_retry["next_attempt_at"],
                      "published_candidate_id": recovered["candidate_id"],
                      "sender_applied": events["decimal"](
                          sender, "applied_through_event_id"),
                      "receiver_applied": events["decimal"](
                          receiver, "applied_through_event_id")},
            "strict_latest": denial, "answer": answer,
            "embedding_restored": True}


def isolated_run(image, phase):
    prepared = command(sys.executable, str(HERE / "synthetic-ldap-fixture.py"),
                       "prepare", "--weknora-image", image, "--mode", "direct")
    directory = Path(json.loads(prepared)["scratch"])
    _, state = owner["owned_state"](directory)
    try:
        command(sys.executable, str(HERE / "synthetic-ldap-fixture.py"),
                "up", "--scratch", str(directory), timeout=900)
        command(sys.executable, str(HERE / "synthetic-ldap-e2e.py"),
                "bootstrap", "--scratch", str(directory), timeout=900)
        return drill(directory, state, phase=phase)
    finally:
        cleaned = subprocess.run([
            sys.executable, str(HERE / "synthetic-ldap-fixture.py"),
            "destroy", "--scratch", str(directory)], text=True,
            capture_output=True, timeout=180, check=False)
        require(cleaned.returncode == 0,
                f"owned fixture cleanup failed; inspect scratch {directory}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weknora-image", required=True,
                        help="local candidate image tag; the fixture pins its image ID")
    parser.add_argument("--phase", choices=("inject-only", "full"), default="full")
    args = parser.parse_args()
    print(json.dumps(isolated_run(args.weknora_image, args.phase),
                     separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.TimeoutExpired) as error:
        print("Isolated failed-candidate retry smoke failed: " + str(error),
              file=sys.stderr)
        sys.exit(1)
