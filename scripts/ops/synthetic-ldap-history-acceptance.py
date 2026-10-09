#!/usr/bin/env python3
"""Probe real saved QA and replay in a marker-owned synthetic LDAP fixture.

Use after fixture prepare/up/bootstrap. Supports direct and nested isolated
group shares. It mutates only the owned source share when --revoke-source-share
is supplied, and emits metadata without credentials, document text or SSE.
"""

import argparse
import datetime as dt
import json
from pathlib import Path
import runpy
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "apps/integration_weknora/tests"))
probe = runpy.run_path(str(HERE / "synthetic-ldap-ask-handoff.py"))
owner, e2e = probe["owner"], probe["e2e"]
require = probe["require"]
MARKER = probe["MARKER"]


def history(base, session_id, token, expected):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status, response = e2e["http_json"](
            base, "GET", f"/api/v1/messages/{session_id}/load?limit=20", token=token)
        rows = response.get("data") if isinstance(response, dict) else None
        if status == 200 and isinstance(rows, list):
            completed = [row for row in rows if row.get("role") == "assistant"
                         and row.get("is_completed") is True]
            if len(completed) == expected and all(
                    MARKER in json.dumps(row, ensure_ascii=False) for row in completed):
                return completed
        time.sleep(0.5)
    raise RuntimeError("actual completed history did not reach the expected QA count")


def replay(base, session_id, message_id, token):
    path = ("/api/v1/sessions/continue-stream/" + urllib.parse.quote(session_id)
            + "?" + urllib.parse.urlencode({"message_id": message_id}))
    request = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            content = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        status, content = error.code, error.read(2 * 1024 * 1024 + 1)
    require(len(content) <= 2 * 1024 * 1024, "saved replay exceeded probe bound")
    return status, content


def rejected_generation(base, session_id, token, runtime):
    payload = json.dumps({"query": "What is the synthetic approval code?",
                          "knowledge_ids": [runtime["knowledge_id"]],
                          "agent_enabled": False, "summary_model_id": runtime["chat_model_id"],
                          "channel": "web", "disable_title": True}).encode()
    request = urllib.request.Request(base + "/api/v1/knowledge-chat/" +
                                     urllib.parse.quote(session_id), data=payload, method="POST",
                                     headers={"Authorization": "Bearer " + token,
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, content = response.status, response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        status, content = error.code, error.read(2 * 1024 * 1024 + 1)
    require(len(content) <= 2 * 1024 * 1024, "rejected QA exceeded probe bound")
    events = []
    if status == 200:
        for line in content.decode("utf-8").splitlines():
            if line.startswith("data:") and line[5:].strip() not in {"", "[DONE]"}:
                events.append(json.loads(line[5:].strip()))
    return status, content, events


def run(scratch, revoke):
    directory, state = owner["owned_state"](scratch)
    require(state["mode"] in {"direct", "nested"}, "unsupported owned LDAP mode")
    runtime = json.loads((directory / "runtime.json").read_text())
    require(runtime.get("publication_root", "group_share") == "group_share",
            "probe requires the owned group share")
    passwords = json.loads((directory / "passwords.json").read_text())
    base = f"http://127.0.0.1:{state['ports']['weknora']}"
    nc_base = f"http://127.0.0.1:{state['ports']['nextcloud']}"
    token = probe["wait_ldap_login"](base, "alice", passwords["alice"])
    human = probe["citation_url"](state["project"], runtime["knowledge_id"],
                                  runtime["file_id"], nc_base)
    session = probe["answer_and_citation"](base, token, runtime, human)
    first = history(base, session, token, 1)
    events = probe["sse"](base, session, token, runtime["knowledge_id"],
                          runtime["chat_model_id"])
    require(any(item.get("response_type") == "answer" and
                MARKER in json.dumps(item, ensure_ascii=False) for item in events),
            "second actual QA did not emit the controlled marker")
    second = history(base, session, token, 2)
    require(first[0]["id"] in {row["id"] for row in second},
            "second QA replaced the first saved answer")
    for row in second:
        status, content = replay(base, session, row["id"], token)
        require(status == 200 and MARKER.encode() in content and human.encode() in content,
                "completed native replay did not return the original answer and citation")
    result = {"checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "project": state["project"], "ldap_mode": state["mode"],
              "session_id": session, "actual_qa_rounds": 2,
              "completed_history_count": len(second), "completed_replays": len(second),
              "original_citations": True, "source_revocation_checked": False}
    if revoke:
        probe["revoke_source_share"](nc_base, passwords, runtime["share_id"])
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status, response = e2e["http_json"](
                base, "GET", f"/api/v1/messages/{session}/load?limit=20", token=token)
            raw = json.dumps(response, ensure_ascii=False)
            if status in {200, 403, 404} and MARKER not in raw and human not in raw:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("source revocation still exposed original saved history")
        for row in second:
            replay_status, content = replay(base, session, row["id"], token)
            require(replay_status in {200, 403, 404} and
                    MARKER.encode() not in content and human.encode() not in content,
                    "source revocation still exposed original replay")
        # A new generation must also stop without using the old saved body.
        started = time.monotonic()
        denied_status, denied_content, denied_events = rejected_generation(base, session, token, runtime)
        elapsed = time.monotonic() - started
        require(MARKER.encode() not in denied_content and human.encode() not in denied_content and
                (denied_status in {403, 404} or (denied_status == 200 and
                 any(item.get("response_type") == "error" and
                     item.get("done") is True for item in denied_events))),
                "new revoked QA did not terminate with a safe error")
        # Confirm the identity and KB grant still exist, so the source denial
        # was exercised independently of account removal.
        probe["source_context_unchanged"](state, nc_base, passwords, runtime)
        result.update(source_revocation_checked=True, history_redacted=True,
                      completed_replays_redacted=True, new_qa_denied=True,
                      denied_qa_http_status=denied_status,
                      denied_qa_terminal_seconds=round(elapsed, 3))
    print(json.dumps(result, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--revoke-source-share", action="store_true")
    args = parser.parse_args()
    try:
        run(args.scratch, args.revoke_source_share)
    except Exception as error:
        # HTTP payloads and tokens must not reach public evidence or logs.
        print("Owned history acceptance failed: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
