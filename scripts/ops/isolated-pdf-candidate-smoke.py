#!/usr/bin/env python3
"""Check the pinned AnyDoc candidate with an owned, loopback-only PDF fixture.

The candidate image must already exist. The script creates and destroys its own
synthetic LDAP Compose project, and never accepts a shared project or URL.
Output contains only IDs, booleans, timing, and image provenance.
"""

import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time
import urllib.request


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "apps/integration_weknora/tests"))
from changes_http_smoke import file_id  # noqa: E402
from publication_http_smoke import request  # noqa: E402


owner = runpy.run_path(str(HERE / "synthetic-ldap-fixture.py"))
e2e = runpy.run_path(str(HERE / "synthetic-ldap-e2e.py"))
handoff = runpy.run_path(str(HERE / "synthetic-ldap-ask-handoff.py"))
matrix = runpy.run_path(str(HERE / "ad-permission-acceptance.py"))
index = runpy.run_path(str(HERE / "local-indexed-withdrawal-smoke.py"))

CANDIDATES = {
    "weknora-ldap-app:nextcloud-rag-e5-anydoc-rebind": (
        "e5cc3e4491ee10fb85e0c2ad79f1e3329826d02e",
        "ed055900b1eca78cc15a14021794fb6dc95dafb3e8e5592ce3f865ca1538af03",
    ),
    "weknora-ldap-app:rag-nc-3ad-251f": (
        "3ad3b31f2b3e6409c6a9c196bbab70e2eac6c666",
        "251f416a0262fedeea74ef2962664e005122d950670e24792341f7cef5f77611",
    ),
    "weknora-ldap-app:nextcloud-rag-duckdb-20261002": (
        "77c97fd72f26e84435503d24eeed88cb5dfe1f01",
        "176a514658adbe949ec5f12490fda4f48655cda5abd65e3e943c5ca21720acef",
    ),
}
DEFAULT_IMAGE = "weknora-ldap-app:nextcloud-rag-e5-anydoc-rebind"
PDF_NAME = "candidate-pdf.pdf"
MARKER = "ORCHID-QUARTZ-2749"
PDF_MARKER = "PDF-CANDIDATE-8734"
DENIED = {401, 403, 404}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def candidate_image(image):
    base, patch_sha = CANDIDATES[image]
    inspected = subprocess.run(
        ["docker", "image", "inspect", image,
         "--format", "{{json .Config.Labels}}"],
        text=True, capture_output=True, check=False, timeout=20)
    require(inspected.returncode == 0,
            "candidate image is absent; build the selected AnyDoc image first")
    labels = json.loads(inspected.stdout)
    require(isinstance(labels, dict) and
            labels.get("org.opencontainers.image.revision") == base and
            labels.get("io.github.liugang926.weknora.nextcloud-patch-sha256") == patch_sha,
            "candidate image does not carry the pinned source and patch labels")
    return labels


def pdf_bytes():
    """Build a one-page, born-digital PDF without host PDF dependencies."""
    lines = [
        "Synthetic PDF acceptance record. The approval code is " + MARKER + ".",
        "The protected PDF marker is " + PDF_MARKER + ".",
        "This page is synthetic and belongs only to the disposable Nextcloud source.",
        "The document has searchable text, a real PDF page, and a citation target.",
        "Only Engineering members with access to the source may read this sentence.",
    ]
    stream = ("BT /F1 11 Tf 48 780 Td 17 TL " +
              " ".join("(" + line.replace("\\", "\\\\").replace("(", "\\(")
                       .replace(")", "\\)") + ") Tj T*" for line in lines) +
              " ET").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = len(document)
    document.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend((f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
                     f"startxref\n{xref_at}\n%%EOF\n").encode())
    return bytes(document)


def public_read_status(nc_base, wk_base, passwords, pdf_id, fixture):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        code, data = handoff["status_for"](
            nc_base, {"file_id": pdf_id}, passwords, "alice")
        if code == 200 and data.get("knowledge_state") == "ready":
            return handoff["source_link"](data, wk_base, fixture)
        time.sleep(2)
    raise RuntimeError("PDF Files status never became ready")


def pdf_index(project, source_id, pdf_id):
    require(re.fullmatch(r"[0-9a-f-]{36}", source_id) is not None,
            "owned source ID has unexpected syntax")
    require(type(pdf_id) is int and pdf_id > 0, "owned PDF file ID is invalid")
    database = project + "-wk-db-1"
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        proof = index["index_proof"](database, source_id, pdf_id)
        if proof:
            query = ("SELECT to_jsonb(k.id) FROM nextcloud_source_versions v "
                     "JOIN knowledges k ON k.id=v.candidate_knowledge_id "
                     f"WHERE v.datasource_id='{source_id}' "
                     f"AND v.external_id LIKE '%:{pdf_id}' AND v.state='published'")
            knowledge_id = index["sql_json"](database, query)
            require(isinstance(knowledge_id, str) and
                    re.fullmatch(r"[A-Za-z0-9_-]{1,128}", knowledge_id),
                    "indexed PDF knowledge ID is invalid")
            marker_found = index["sql_json"](
                database, "SELECT to_jsonb(EXISTS(SELECT 1 FROM chunks c "
                f"WHERE c.knowledge_id='{knowledge_id}' AND c.deleted_at IS NULL "
                "AND c.is_enabled AND c.index_status='ready' "
                f"AND c.content LIKE '%{PDF_MARKER}%'))")
            require(marker_found is True,
                    "ready PDF chunks did not contain the protected PDF marker")
            return knowledge_id, proof
        time.sleep(2)
    raise RuntimeError("PDF never reached published state with ready chunk and embedding")


def search(wk_base, token, query, scope, knowledge_id):
    code, body = e2e["http_json"](
        wk_base, "POST", "/api/v1/knowledge-search",
        {"query": query, **scope}, token=token)
    if code in DENIED:
        return False
    require(code == 200 and body.get("success") is True and
            isinstance(body.get("data"), list),
            "PDF search returned an unexpected response")
    rows = body["data"]
    require(all(isinstance(row, dict) and row.get("knowledge_id") == knowledge_id
                for row in rows), "PDF document search escaped its target")
    return bool(rows)


def source_denial(nc_base, wk_base, state, passwords, fixture, pdf_id,
                  knowledge_id, query, alice_token, session_id, human):
    account = fixture["accounts"]["a"]
    deadline = time.monotonic() + 150
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            status, _ = handoff["status_for"](
                nc_base, {"file_id": pdf_id}, passwords, "alice")
            logged_in, dav = matrix["dav_probe"](
                nc_base, account, passwords["alice"], pdf_id)
            source = matrix["source_probe"](
                nc_base, fixture, account, fixture["key_id"], fixture["token"])
            ask_code, _ = handoff["ask_target"](wk_base, query, alice_token)
            knowledge_code, _ = handoff["knowledge"](
                wk_base, knowledge_id, alice_token)
            direct = matrix["direct_content_probe"](
                wk_base, knowledge_id, alice_token)
            document_search = search(wk_base, alice_token, PDF_MARKER,
                                     {"knowledge_ids": [knowledge_id]}, knowledge_id)
        except (OSError, ValueError, RuntimeError, matrix["ProbeError"]):
            time.sleep(3)
            continue
        if (logged_in and not dav and not source and status in {403, 404} and
                ask_code in {403, 404} and knowledge_code in {403, 404} and
                not direct and not document_search):
            history_code, history = e2e["http_json"](
                wk_base, "GET", f"/api/v1/messages/{session_id}/load?limit=20",
                token=alice_token)
            require(history_code in {200, 403, 404},
                    "PDF history returned an unexpected response after revocation")
            if history_code != 200 or (
                    MARKER not in json.dumps(history) and
                    PDF_MARKER not in json.dumps(history) and
                    human not in json.dumps(history)):
                return attempts
        time.sleep(3)
    raise RuntimeError("old JWT PDF source, direct, search and citation denial did not converge")


def run(scratch, image, *, require_recovery=False):
    directory, state = owner["owned_state"](scratch)
    require(state["mode"] == "direct" and state["weknora_image"] == image,
            "PDF smoke requires its owned direct candidate fixture")
    passwords = json.loads((directory / "passwords.json").read_text())
    runtime = json.loads((directory / "runtime.json").read_text())
    fixture = json.loads((directory / "fixture.json").read_text())
    nc_base = f"http://127.0.0.1:{state['ports']['nextcloud']}"
    wk_base = f"http://127.0.0.1:{state['ports']['weknora']}"
    admin_code, auth = e2e["http_json"](
        wk_base, "POST", "/api/v1/auth/login",
        {"email": "synthetic-admin@example.test",
         "password": passwords["wk_admin"]})
    require(admin_code == 200 and isinstance(auth.get("token"), str),
            "candidate administrator login failed")
    token = auth["token"]
    code, engines = e2e["http_json"](
        wk_base, "GET", "/api/v1/system/parser-engines", token=token)
    require(code == 200 and engines.get("connected") is True and
            isinstance(engines.get("data"), list),
            "candidate DocReader is not connected")
    anydoc = next((item for item in engines["data"]
                   if item.get("Name") == "anydoc"), None)
    require(anydoc is not None and anydoc.get("Available") is True,
            "candidate binary does not expose an available AnyDoc engine")

    started = dt.datetime.now(dt.timezone.utc)
    owner_auth = base64.b64encode(
        ("devadmin:" + passwords["nc_admin"]).encode()).decode()
    dav_headers = {"Authorization": "Basic " + owner_auth,
                   "Content-Type": "application/pdf"}
    pdf_url = (nc_base + "/remote.php/dav/files/devadmin/Published/" + PDF_NAME)
    pdf = pdf_bytes()
    require(pdf.startswith(b"%PDF-1.4") and len(pdf) < 16_384,
            "synthetic PDF construction failed")
    put_code, _ = request(
        urllib.request.build_opener(), pdf_url, "PUT", dav_headers, pdf)
    require(put_code == 201, "isolated Nextcloud PDF upload failed")
    pdf_id = file_id(pdf_url, dav_headers)
    require(type(pdf_id) is int and pdf_id > 0 and pdf_id != runtime["file_id"],
            "PDF file ID is invalid or aliases the text fixture")
    code, _ = e2e["http_json"](
        wk_base, "POST", f"/api/v1/datasource/{runtime['source_id']}/sync",
        token=token)
    require(code in {200, 409}, "candidate PDF source sync failed")
    knowledge_id, proof = pdf_index(state["project"], runtime["source_id"], pdf_id)
    pdf_fixture = {**fixture, "file_id": pdf_id,
                   "knowledge_id": knowledge_id,
                   "key_id": runtime["key_id"], "token": runtime["token"]}
    for account in pdf_fixture["accounts"].values():
        account["dav_path"] = "Published/" + PDF_NAME
    query = public_read_status(
        nc_base, wk_base, passwords, pdf_id, pdf_fixture)
    alice = handoff["wait_ldap_login"](
        wk_base, "alice", passwords["alice"])
    bob = handoff["wait_ldap_login"](
        wk_base, "bob", passwords["bob"])
    require(alice and bob, "synthetic users did not receive JWTs")
    alice_login, alice_dav = matrix["dav_probe"](
        nc_base, pdf_fixture["accounts"]["a"], passwords["alice"], pdf_id)
    bob_login, bob_dav = matrix["dav_probe"](
        nc_base, pdf_fixture["accounts"]["b"], passwords["bob"], pdf_id)
    require(alice_login and alice_dav and bob_login and not bob_dav,
            "PDF DAV baseline did not enforce Engineering")
    require(matrix["source_probe"](
        nc_base, pdf_fixture, pdf_fixture["accounts"]["a"],
        runtime["key_id"], runtime["token"]) and
        not matrix["source_probe"](
            nc_base, pdf_fixture, pdf_fixture["accounts"]["b"],
            runtime["key_id"], runtime["token"]),
            "PDF signed-source baseline did not enforce Engineering")
    ask_alice, target = handoff["ask_target"](wk_base, query, alice)
    ask_bob, _ = handoff["ask_target"](wk_base, query, bob)
    require(ask_alice == 200 and ask_bob in {403, 404} and
            target.get("data", {}).get("knowledge_id") == knowledge_id,
            "PDF file-scoped ask target did not enforce source access")
    require(search(wk_base, alice, PDF_MARKER,
                   {"knowledge_ids": [knowledge_id]}, knowledge_id),
            "permitted PDF document search did not find the indexed marker")
    require(not search(wk_base, bob, PDF_MARKER,
                       {"knowledge_ids": [knowledge_id]}, knowledge_id),
            "denied user found the PDF marker")
    human = handoff["citation_url"](
        state["project"], knowledge_id, pdf_id, nc_base)
    code, session = e2e["http_json"](
        wk_base, "POST", "/api/v1/sessions", {}, token=alice)
    require(code == 201 and isinstance(session.get("data", {}).get("id"), str),
            "PDF answer session creation failed")
    session_id = session["data"]["id"]
    events = handoff["sse"](
        wk_base, session_id, alice, knowledge_id, runtime["chat_model_id"])
    require(any(MARKER in json.dumps(event, ensure_ascii=False)
                for event in events if event.get("response_type") == "answer"),
            "PDF text did not reach the file-scoped answer")
    refs = [ref for event in events if event.get("response_type") == "references"
            for ref in ((event.get("data") or {}).get("references") or
                        event.get("knowledge_references") or [])]
    require(refs and all(ref.get("knowledge_id") == knowledge_id for ref in refs) and
            any((ref.get("metadata") or {}).get("nextcloud_human_url") == human
                for ref in refs),
            "PDF answer did not cite its original Nextcloud file")
    history_deadline = time.monotonic() + 30
    while time.monotonic() < history_deadline:
        history_code, history = e2e["http_json"](
            wk_base, "GET", f"/api/v1/messages/{session_id}/load?limit=20",
            token=alice)
        if history_code == 200 and MARKER in json.dumps(history) and human in json.dumps(history):
            break
        time.sleep(1)
    else:
        raise RuntimeError("PDF answer and citation were not persisted before revocation")
    recovery = subprocess.run(
        ["docker", "logs", "--since", started.isoformat(),
         state["project"] + "-wk-app-1"],
        text=True, capture_output=True, check=False, timeout=30)
    require(recovery.returncode == 0, "candidate PDF processing logs unavailable")
    recovery_matches = re.findall(
        r"\[pdf\] recovered incomplete text with anydoc: "
        r"primary_chars=(\d+) recovered_chars=(\d+)",
        recovery.stdout + recovery.stderr)
    recovered = bool(recovery_matches)
    if require_recovery:
        require(recovered, "controlled short primary did not enter AnyDoc recovery")
    recovery_counts = ([int(value) for value in recovery_matches[-1]]
                       if recovered else None)
    if require_recovery:
        require(recovery_counts[0] < 120 and recovery_counts[1] >= 48 and
                recovery_counts[1] >= recovery_counts[0] + 32 and
                recovery_counts[1] >= recovery_counts[0] * 2,
                "AnyDoc recovery log did not meet the branch thresholds")
    print(json.dumps({"phase": "pdf_published", "project": state["project"],
                      "file_id": pdf_id, "knowledge_id": knowledge_id,
                      "ready_chunks": proof["chunks"],
                      "ready_embeddings": proof["embeddings"],
                      "anydoc_available": True, "docreader_connected": True,
                      "protected_text_indexed": True, "answer_marker": True,
                      "original_file_citation": True,
                      "short_text_recovery_observed": recovered,
                      "recovery_primary_chars": (recovery_counts[0]
                                                 if recovered else None),
                      "recovery_anydoc_chars": (recovery_counts[1]
                                                if recovered else None)},
                     separators=(",", ":")), flush=True)

    handoff["revoke_source_share"](
        nc_base, passwords, runtime["share_id"])
    polls = source_denial(
        nc_base, wk_base, state, passwords, pdf_fixture, pdf_id,
        knowledge_id, query, alice, session_id, human)
    # Only the owned source share changed; Alice remains an LDAP member of
    # Engineering and the original PDF remains in the owner's DAV tree.
    groups = json.loads(e2e["occ"](state, "group:list", "--output=json"))
    require("alice" in groups.get("Engineering", []),
            "PDF source-share mutation also removed Alice's group")
    require(file_id(pdf_url, dav_headers) == pdf_id,
            "PDF source-share mutation also removed the owner's file")
    require(handoff["wait_ldap_login"](
        wk_base, "alice", passwords["alice"]),
        "PDF source-share mutation also disabled Alice's LDAP login")
    handoff["source_context_unchanged"](
        state, nc_base, passwords, runtime)
    print(json.dumps({"phase": "pdf_source_share_revoked", "polls": polls,
                      "old_jwt_source_denied": True,
                      "old_jwt_ask_denied": True,
                      "old_jwt_direct_denied": True,
                      "old_jwt_search_denied": True,
                      "old_jwt_history_citation_redacted": True,
                      "ldap_group_and_owner_file_retained": True},
                     separators=(",", ":")))


def force_short_primary(scratch, image):
    """Force rasterized primary output only in this owned private fixture."""
    directory, state = owner["owned_state"](scratch)
    require(state["mode"] == "direct" and state["weknora_image"] == image,
            "short-primary injection requires an owned candidate fixture")
    path = directory / "compose.yaml"
    compose = json.loads(path.read_text())
    service = compose["services"]["docreader"]
    require(service["image"] == owner["DOCREADER_IMAGE"] and
            "ports" not in service and "environment" not in service,
            "owned DocReader configuration changed unexpectedly")
    service["environment"] = {"DOCREADER_PDF_FORCE_SCANNED": "1"}
    temporary = directory / "compose.short-primary.tmp"
    temporary.write_text(json.dumps(compose, indent=2) + "\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", choices=tuple(CANDIDATES), default=DEFAULT_IMAGE,
                        help="the pinned candidate tag; shared tags are rejected")
    parser.add_argument("--force-short-primary", action="store_true",
                        help="in the owned fixture only, force DocReader to "
                             "rasterize PDFs so the AnyDoc recovery branch is required")
    args = parser.parse_args()
    candidate_image(args.image)
    prepared = subprocess.run(
        [sys.executable, str(HERE / "synthetic-ldap-fixture.py"),
         "prepare", "--weknora-image", args.image, "--mode", "direct"],
        text=True, capture_output=True, check=True, timeout=45)
    scratch = Path(json.loads(prepared.stdout)["scratch"])
    try:
        if args.force_short_primary:
            force_short_primary(scratch, args.image)
        subprocess.run(
            [sys.executable, str(HERE / "synthetic-ldap-fixture.py"),
             "up", "--scratch", str(scratch)],
            text=True, capture_output=True, check=True, timeout=750)
        directory, state = owner["owned_state"](scratch)
        e2e["bootstrap"](directory, state)
        e2e["matrix"](directory, state)
        run(scratch, args.image, require_recovery=args.force_short_primary)
    finally:
        removed = subprocess.run(
            [sys.executable, str(HERE / "synthetic-ldap-fixture.py"),
             "destroy", "--scratch", str(scratch)],
            text=True, capture_output=True, check=False, timeout=300)
        if removed.returncode:
            raise RuntimeError(
                "owned fixture cleanup failed; inspect and destroy scratch " +
                str(scratch))


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, subprocess.TimeoutExpired):
        # Imported HTTP helpers may include response bytes in assertions, and
        # TimeoutExpired can carry captured Compose output. Keep both private.
        print("Isolated PDF candidate smoke failed: helper assertion or timeout",
              file=sys.stderr)
        sys.exit(1)
    except (KeyError, OSError, ValueError, RuntimeError,
            subprocess.CalledProcessError, matrix["ProbeError"]) as error:
        # Never emit captured subprocess stderr, HTTP bodies, JWTs, or secrets.
        print("Isolated PDF candidate smoke failed: " + str(error), file=sys.stderr)
        sys.exit(1)
