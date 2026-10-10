#!/usr/bin/env python3
"""Measure P5 on one marker-owned synthetic LDAP fixture, never shared stacks.

Requires a freshly bootstrapped candidate supplied by its owner. Outputs only
IDs, digests, timings and booleans. No measurement is invented by offline tests.
The fixture, documents and raw samples are retained for the owner's inspection.
"""

import argparse
import base64
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import runpy
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/integration_weknora/tests"))
from publication_http_smoke import login, request  # noqa: E402

owner = runpy.run_path(str(Path(__file__).with_name("synthetic-ldap-fixture.py")))
e2e = runpy.run_path(str(Path(__file__).with_name("synthetic-ldap-e2e.py")))
handoff = runpy.run_path(str(Path(__file__).with_name("synthetic-ldap-ask-handoff.py")))
SOURCE_BINDING = "synthetic-published"
POLL_SECONDS = 0.5
SENDER_SLEEP_SECONDS = 5


def require(value, code):
    if not value:
        raise RuntimeError(code)


def quantile(values, percentile=0.95):
    require(bool(values), "no_samples")
    require(all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values),
            "invalid_latency")
    return sorted(values)[math.ceil(len(values) * percentile) - 1]


def payload(nonce, sample, size=1024):
    prefix = f"synthetic P5 {nonce} sample {sample:04d}; no user data\n".encode()
    return (prefix * math.ceil(size / len(prefix)))[:size]


def validate_runtime(runtime, fixture):
    require(runtime.get("binding_id") == SOURCE_BINDING and
            fixture.get("binding_id") == SOURCE_BINDING and
            fixture.get("synthetic_fixture") is True, "unexpected_fixture_binding")
    for field in ("source_id", "knowledge_base_id", "operation_id", "model_id", "chat_model_id"):
        value = runtime.get(field)
        require(isinstance(value, str) and str(uuid.UUID(value)) == value, "invalid_runtime_id")
    require(type(runtime.get("tenant_id")) is int and runtime["tenant_id"] > 0 and
            type(runtime.get("file_id")) is int and runtime["file_id"] > 0,
            "invalid_runtime_scope")


def polling_report(records):
    intervals = [row["request_end_ms"] - row["previous_request_start_ms"]
                 for row in records if row.get("previous_request_start_ms") is not None]
    return {"configured_sleep_ms": POLL_SECONDS * 1000,
            "max_actual_observation_interval_ms": round(max(intervals, default=0), 3),
            "requests": len(records), "method": "actual request start-to-next-response end; includes request time"}


def abba_plan(samples):
    require(100 <= samples <= 1000, "samples_must_be_100_to_1000")
    remaining = {"on": samples, "off": samples}
    plan = []
    while any(remaining.values()):
        for condition in ("on", "off", "off", "on"):
            count = min(25, remaining[condition])
            if count:
                plan.append((condition, count))
                remaining[condition] -= count
    return plan


def idle_ready(row, cloud):
    zero = ("running", "processing", "staged", "content_leases", "body_leases", "auto_pending")
    numbers = [row.get(key) for key in zero] + [row.get("received"), row.get("applied"),
              cloud.get("outbox_id"), cloud.get("sender_received")]
    if not all(type(value) is int and value >= 0 for value in numbers):
        return False
    return (all(row[key] == 0 for key in zero) and row["applied"] >= row["received"] and
            row["received"] >= cloud["outbox_id"] and cloud["sender_received"] >= cloud["outbox_id"] and
            cloud.get("sender_status") == "active")


def summary(rows):
    require(len(rows) >= 100, "need_at_least_100_independent_samples")
    values = [row["elapsed_ms"] for row in rows]
    return {"samples": len(rows), "p50_ms": quantile(values, .5),
            "p95_ms": quantile(values), "max_ms": max(values),
            "percentile_method": "nearest rank ceil(0.95*N)"}


class Sender:
    """Execute the real bounded CLI; this is an explicit probe-owned worker."""
    def __init__(self, probe):
        self.probe = probe
        self.stop_event = threading.Event()
        self.thread = None
        self.passes = []
        self.failed = False
        self.worker_marker = None

    def start(self):
        require(self.thread is None, "sender_already_started")
        self.probe.assert_owned()
        marker = self.probe.directory / "active-probe-workers.json"
        marker_payload = {"kind": "synthetic-p5-event-sender", "pid": os.getpid(),
                          "worker_nonce": uuid.uuid4().hex,
                          "owner_sha256": hashlib.sha256(self.probe.state["owner_token"].encode()).hexdigest()}
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(json.dumps(marker_payload, sort_keys=True) + "\n")
            output.flush(); os.fsync(output.fileno())
        self.worker_marker = marker_payload
        self.stop_event.clear()
        self.failed = False
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self.probe.assert_owned()
                result = subprocess.run([
                    "docker", "exec", "-u", "www-data", self.probe.nc_container,
                    "timeout", "-k", "5s", "90s", "php", "occ",
                    "integration_weknora:deliver-events", "--quiet"],
                    capture_output=True, timeout=100, check=False)
                self.failed = result.returncode != 0
            except Exception:
                self.failed = True
            self.passes.append({"started_monotonic_s": started,
                                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                                "success": not self.failed})
            if self.failed:
                return
            self.stop_event.wait(SENDER_SLEEP_SECONDS)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=110)
            require(not self.thread.is_alive(), "sender_stop_timeout")
            self.thread = None
        if self.worker_marker is not None:
            self.probe.assert_owned()
            marker = self.probe.directory / "active-probe-workers.json"
            info = marker.lstat()
            require(not marker.is_symlink() and info.st_uid == os.getuid() and
                    info.st_nlink == 1 and info.st_mode & 0o777 == 0o600 and
                    json.loads(marker.read_text()) == self.worker_marker, "sender_worker_marker_changed")
            marker.unlink()
            self.worker_marker = None


class Probe:
    def __init__(self, scratch, samples):
        self.directory, self.state = owner["owned_state"](scratch)
        self.assert_owned()
        require(self.state.get("body_journal_initialized") is True, "candidate_body_journal_not_ready")
        self.runtime = json.loads((self.directory / "runtime.json").read_text())
        fixture = json.loads((self.directory / "fixture.json").read_text())
        validate_runtime(self.runtime, fixture)
        require(self.runtime.get("publication_root", "group_share") == "group_share",
                "probe_requires_owned_group_share")
        self.passwords = json.loads((self.directory / "passwords.json").read_text())
        self.samples = samples
        self.nonce = os.urandom(8).hex()
        self.nc = f"http://127.0.0.1:{self.state['ports']['nextcloud']}"
        self.wk = f"http://127.0.0.1:{self.state['ports']['weknora']}"
        self.nc_container = self.state["project"] + "-nextcloud-1"
        self.db = self.state["project"] + "-wk-db-1"
        self.nc_db = self.state["project"] + "-nc-db-1"
        self.app = self.state["project"] + "-wk-app-1"
        self.admin, self.csrf = login(self.nc, "devadmin", self.passwords["nc_admin"])
        self.alice, self.alice_csrf = login(self.nc, "alice", self.passwords["alice"])
        self.alice_token = handoff["wait_ldap_login"](self.wk, "alice", self.passwords["alice"])
        self.admin_token = self.admin_login()
        encoded = base64.b64encode(("devadmin:" + self.passwords["nc_admin"]).encode()).decode()
        self.dav = {"Authorization": "Basic " + encoded}
        self.folder = f"{self.nc}/remote.php/dav/files/devadmin/Published/p5-{self.nonce}"
        self.sender = Sender(self)
        self.records = []
        self.sample_path = self.directory / ("p5-performance-" + self.nonce + "-samples.jsonl")
        require(not self.sample_path.exists(), "samples_file_exists")
        fd = os.open(self.sample_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        self.warmups = []
        self.restore_required = False
        self.baseline = None
        self.seen_events, self.seen_jobs, self.seen_files = set(), set(), set()

    def assert_owned(self):
        owner["assert_owned_resources"](self.directory, self.state)

    def admin_login(self):
        code, body = e2e["http_json"](self.wk, "POST", "/api/v1/auth/login", {
            "email": "synthetic-admin@example.test", "password": self.passwords["wk_admin"]})
        require(code == 200 and isinstance(body.get("token"), str), "owned_admin_login_failed")
        require(body.get("active_tenant", {}).get("id") == self.runtime["tenant_id"],
                "owned_tenant_changed")
        return body["token"]

    def sql(self, query, cloud=False):
        return e2e["sql_json"](self.nc_db if cloud else self.db, query)

    def identity(self):
        r = self.runtime
        return self.sql("SELECT jsonb_build_object("
            "'source_id',s.id,'tenant_id',s.tenant_id,'kb',s.knowledge_base_id,"
            "'source_status',s.status,'source_config_digest',md5(s.config::text),"
            "'connection_id',c.connection_id,'connection_status',c.status,"
            "'connection_config',c.datasource_config_sha256,'binding_id',c.binding_id,"
            "'instance_id',c.nextcloud_instance_id,'operation_id',p.operation_id,"
            "'publication_epoch',p.publication_epoch,'pair_state',p.state,"
            "'body_journal_id',(SELECT journal_id FROM original_body_journal_heads WHERE id=1),"
            "'baseline_candidate',(SELECT candidate_knowledge_id FROM nextcloud_source_versions "
            f"WHERE datasource_id=s.id AND external_id LIKE '%:{r['file_id']}'),"
            "'mock_models_verified',(SELECT count(*)=2 AND bool_and(parameters->>'base_url'='http://mock-embedding:8000/v1') "
            f"FROM models WHERE id IN ('{r['model_id']}','{r['chat_model_id']}')),"
            "'model_catalog_digest',(SELECT md5(string_agg(id||':'||parameters::text,',' ORDER BY id)) "
            f"FROM models WHERE id IN ('{r['model_id']}','{r['chat_model_id']}'))) "
            "FROM data_sources s JOIN nextcloud_event_connections c ON c.datasource_id=s.id "
            "JOIN nextcloud_source_pairings p ON p.datasource_id=s.id "
            f"WHERE s.id='{r['source_id']}' AND s.tenant_id={r['tenant_id']} "
            f"AND s.knowledge_base_id='{r['knowledge_base_id']}' AND c.status='active' AND p.state='active'")

    def state_snapshot(self):
        r = self.runtime
        return self.sql("SELECT jsonb_build_object("
            "'received',p.received_id,'dispatched',d.dispatched_id,'applied',d.applied_id,"
            "'dispatch_state',d.state,'sync_log_id',d.last_sync_log_id,"
            "'staged',(SELECT count(*) FROM nextcloud_source_versions WHERE "
            f"datasource_id='{r['source_id']}' AND state='staging'),"
            "'running',(SELECT count(*) FROM sync_logs WHERE "
            f"data_source_id='{r['source_id']}' AND tenant_id={r['tenant_id']} AND status='running'),"
            "'processing',(SELECT count(*) FROM knowledges WHERE "
            f"tenant_id={r['tenant_id']} AND knowledge_base_id='{r['knowledge_base_id']}' AND deleted_at IS NULL "
            "AND parse_status IN ('pending','processing','finalizing')),"
            "'content_leases',(SELECT count(*) FROM nextcloud_content_leases WHERE "
            f"tenant_id={r['tenant_id']} AND knowledge_base_id='{r['knowledge_base_id']}' "
            "AND released_at_ms IS NULL AND expires_at_ms>EXTRACT(EPOCH FROM clock_timestamp())*1000),"
            "'body_leases',(SELECT count(DISTINCT l.lease_id) FROM original_body_leases l "
            # This isolated fixture owns the whole tenant. Direct knowledge
            # and message bodies can have no parent_refs; count every owned
            # active body lease conservatively before admitting a sample.
            "JOIN original_body_payloads b ON b.id=l.body_id WHERE "
            f"b.tenant_id={r['tenant_id']} "
            "AND l.released_at_ms IS NULL AND l.expires_at_ms>EXTRACT(EPOCH FROM clock_timestamp())*1000),"
            "'auto_pending',(SELECT count(*) FROM task_pending_ops WHERE "
            f"tenant_id={r['tenant_id']} AND scope_id IN (SELECT id FROM knowledges WHERE "
            f"knowledge_base_id='{r['knowledge_base_id']}') AND task_type='knowledge:auto_tag' "
            "AND op IN ('auto_tag_completion','auto_tag_running'))) "
            "FROM nextcloud_event_connections c JOIN nextcloud_event_checkpoint p USING(connection_id) "
            "JOIN nextcloud_event_dispatch d USING(connection_id) "
            f"WHERE c.datasource_id='{r['source_id']}' AND c.tenant_id={r['tenant_id']} AND c.status='active'")

    def cloud_snapshot(self):
        return self.sql("SELECT jsonb_build_object('outbox_id',(SELECT COALESCE(MAX(id),0) FROM oc_weknora_outbox "
                        f"WHERE binding_id='{SOURCE_BINDING}'),'sender_received',received_id,'sender_status',status) "
                        f"FROM oc_weknora_event_conn WHERE binding_id='{SOURCE_BINDING}'",cloud=True)

    def wait_idle(self, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.state_snapshot()
            require(row.get("dispatch_state") != "blocked", "dispatch_blocked")
            cloud=self.cloud_snapshot()
            if idle_ready(row,cloud):
                return row
            require(not self.sender.failed, "sender_cli_failed")
            time.sleep(POLL_SECONDS)
        raise RuntimeError("owned_idle_timeout")

    def record(self, row):
        self.records.append(row)
        with self.sample_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(row, separators=(",", ":")) + "\n")
            output.flush()
            os.fsync(output.fileno())

    def put(self, name, body):
        self.assert_owned()
        started = time.monotonic()
        code, _ = request(urllib.request.build_opener(), self.folder + "/" + name,
                          "PUT", self.dav, body)
        ended = time.monotonic()
        require(code == 201, "independent_upload_not_created")
        return started, ended, code

    def file_identity(self, name):
        url = self.folder + "/" + name
        xml = (b'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
               b'<d:prop><oc:fileid/><d:getetag/></d:prop></d:propfind>')
        code, raw = request(urllib.request.build_opener(), url, "PROPFIND",
                            {**self.dav, "Depth": "0", "Content-Type": "application/xml"}, xml)
        require(code == 207, "uploaded_file_identity_failed")
        root = ET.fromstring(raw)
        fid = root.find(".//{http://owncloud.org/ns}fileid")
        etag = root.find(".//{DAV:}getetag")
        require(fid is not None and fid.text and fid.text.isdigit() and etag is not None and etag.text,
                "uploaded_file_identity_missing")
        return int(fid.text), etag.text.strip('"')

    def event_id(self, file_id):
        require(type(file_id) is int and file_id > 0, "invalid_sample_file")
        return self.sql("SELECT to_jsonb(COALESCE(MAX(id),0)) FROM oc_weknora_outbox "
                        f"WHERE binding_id='{SOURCE_BINDING}' AND file_id={file_id} AND event_type='upsert'", cloud=True)

    def publication(self, file_id):
        r = self.runtime
        return self.sql("SELECT jsonb_build_object('id',k.id,'ready',"
            "v.state='published' AND k.parse_status='completed' AND k.enable_status='enabled' "
            "AND v.desired_etag=k.metadata->>'nextcloud_etag','etag',v.desired_etag) "
            "FROM nextcloud_source_versions v JOIN knowledges k ON k.id=v.candidate_knowledge_id "
            f"WHERE v.datasource_id='{r['source_id']}' AND v.tenant_id={r['tenant_id']} "
            f"AND v.external_id='nextcloud:{self.baseline['instance_id']}:{file_id}'")

    def exact_readable(self, file_id, etag):
        url = self.nc + f"/index.php/apps/integration_weknora/api/v1/files/{file_id}/status"
        code, raw = request(self.alice, url, headers={"requesttoken": self.alice_csrf})
        if code != 200:
            return False
        row = json.loads(raw)
        if not (row.get("knowledge_state") == "ready" and row.get("source_etag") == etag and
                row.get("published_source_etag") == etag):
            return False
        published = self.publication(file_id)
        if not published or published.get("ready") is not True or published.get("etag") != etag:
            return False
        identifier = published["id"]
        require(str(uuid.UUID(identifier)) == identifier, "invalid_published_candidate")
        code, knowledge = e2e["http_json"](self.wk, "GET", "/api/v1/knowledge/" + identifier,
                                          token=self.alice_token)
        if code != 200 or knowledge.get("data", {}).get("id") != identifier:
            return False
        code, chunks = e2e["http_json"](self.wk, "GET", "/api/v1/chunks/" + identifier + "?page_size=1",
                                       token=self.alice_token)
        data = chunks.get("data", {})
        rows = data if isinstance(data, list) else data.get("chunks", [])
        return code == 200 and bool(rows)

    def measure_one(self, index, prefix="event"):
        self.wait_idle()
        name = f"{prefix}-{index:04d}.txt"
        start, upload_end, status = self.put(name, payload(self.nonce, index))
        file_id, etag = self.file_identity(name)
        event = self.event_id(file_id)
        require(event > 0 and event not in self.seen_events and file_id not in self.seen_files,
                "sample_event_not_independent")
        self.seen_events.add(event)
        self.seen_files.add(file_id)
        deadline = start + 120
        accepted, readable, job = None, None, None
        observations = []
        previous = None
        try:
            while time.monotonic() < deadline:
                poll_start = time.monotonic()
                row = self.state_snapshot()
                if accepted is None and row.get("dispatched", 0) >= event:
                    job = row.get("sync_log_id")
                    require(isinstance(job, str) and str(uuid.UUID(job)) == job and job not in self.seen_jobs,
                            "sample_job_not_independent")
                    self.seen_jobs.add(job)
                    accepted = time.monotonic()
                if accepted is not None and self.exact_readable(file_id, etag):
                    readable = time.monotonic()
                end = time.monotonic()
                observations.append({"request_start_ms": (poll_start - start)*1000,
                                     "request_end_ms": (end - start)*1000,
                                     "previous_request_start_ms": previous})
                previous = (poll_start - start)*1000
                if readable is not None:
                    break
                require(not self.sender.failed, "sender_cli_failed")
                time.sleep(POLL_SECONDS)
            require(accepted is not None and readable is not None, "sample_readable_timeout")
        except Exception as error:
            self.record({"sample":index,"phase":prefix,"file_id":file_id,"event_id":event,
                         "etag_sha256":hashlib.sha256(etag.encode()).hexdigest(),
                         "put_http_status":status,"upload_ms":round((upload_end-start)*1000,3),
                         "outcome":"failed","error_type":type(error).__name__,
                         "elapsed_ms":round((time.monotonic()-start)*1000,3),
                         "poll_observation":polling_report(observations)})
            raise
        record = {"sample": index, "phase": prefix, "file_id": file_id, "event_id": event,
                  "sync_log_id": job, "queue_task_id":"ncevent:"+self.baseline["connection_id"]+":"+job, "etag_sha256": hashlib.sha256(etag.encode()).hexdigest(),
                  "put_http_status": status, "upload_ms": round((upload_end-start)*1000,3),
                  "durable_job_upper_ms": round((accepted-start)*1000,3),
                  "readable_upper_ms": round((readable-start)*1000,3),
                  "poll_observation": polling_report(observations), "readable": True, "outcome":"passed"}
        self.record(record)
        return record

    def compose_action(self, action):
        self.assert_owned()
        result = subprocess.run(owner["compose_command"](self.directory, self.state,
                                action, "wk-app"), capture_output=True, timeout=120, check=False)
        require(result.returncode == 0, "owned_app_transition_failed")
        self.assert_owned()

    def off(self):
        self.wait_idle()
        self.sender.stop()
        self.wait_idle()
        self.compose_action("stop")
        self.restore_required = True
        # Stop does not destroy the app or its DB/generation/authority state.
        self.assert_owned()
        e2e["occ"](self.state, "app:disable", "integration_weknora")

    def on(self):
        started = time.monotonic()
        self.assert_owned()
        e2e["occ"](self.state, "app:enable", "integration_weknora")
        self.compose_action("start")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self.wk + "/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(POLL_SECONDS)
        else:
            raise RuntimeError("owned_app_restart_timeout")
        self.admin_token = self.admin_login()
        self.alice_token = handoff["wait_ldap_login"](self.wk, "alice", self.passwords["alice"])
        require(self.identity() == self.baseline, "authority_or_generation_changed")
        self.sender.start()
        code, _ = e2e["http_json"](self.wk, "POST",
                     f"/api/v1/datasource/{self.runtime['source_id']}/sync", token=self.admin_token)
        require(code in {200,409}, "post_restart_reconcile_failed")
        self.wait_idle()
        self.warmups.append({"start_monotonic_s": started, "end_monotonic_s": time.monotonic(),
                             "excluded_from_samples": True})
        self.restore_required = False

    def metadata(self):
        fields = {}
        for name in sorted(owner["project_resources"](self.state["project"])["container"]):
            item = owner["docker_inspect"]("container", name)
            host = item["HostConfig"]
            env = dict(x.split("=",1) for x in item["Config"].get("Env",[]) if "=" in x)
            fields[name] = {"image_id": item["Image"], "nano_cpus": host.get("NanoCpus",0),
                            "memory_limit_bytes": host.get("Memory",0), "cpuset_cpus": host.get("CpusetCpus", "")}
            if name == self.app:
                fields[name]["event_dispatch_interval"] = env.get("WEKNORA_NEXTCLOUD_EVENT_DISPATCH_INTERVAL","5s")
        raw = subprocess.check_output(["docker","info","--format",'{{json .}}'],text=True)
        info = json.loads(raw)
        return {"containers": fields, "host": {key:info.get(key) for key in
                ("NCPU","MemTotal","Architecture","KernelVersion","OperatingSystem")},
                "probe_architecture":platform.machine(), "mock_model":self.baseline["mock_models_verified"],
                "model_catalog_digest":self.baseline["model_catalog_digest"],
                "sender_kind":"probe-owned real bounded CLI, not fixture default service",
                "sender_sleep_seconds":SENDER_SLEEP_SECONDS,
                "nextcloud_default_delivery_job_interval_seconds":60,
                "default_dispatcher_seconds":5,
                "configured_observer_sleep_seconds":POLL_SECONDS,
                "source_anchor":"host monotonic before WebDAV PUT; conservative bound includes upload and all observation time"}

    def run(self, mode):
        self.baseline = self.identity()
        require(self.baseline and self.baseline["source_status"] == "active" and
                self.baseline["binding_id"] == SOURCE_BINDING and self.baseline.get("mock_models_verified") is True and
                re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", self.baseline.get("instance_id", "")),
                "source_authority_not_active")
        code, _ = request(urllib.request.build_opener(),self.folder,"MKCOL",self.dav)
        require(code == 201,"owned_performance_folder_not_created")
        result = {"schema_version":1,"project":self.state["project"],"run_nonce":self.nonce,
                  "candidate_image_id":self.state["weknora_image_id"],"measurement":mode,
                  "started_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"metadata":self.metadata(),
                  "raw_samples_jsonl":str(self.sample_path)}
        try:
            self.sender.start()
            self.wait_idle()
            if mode in {"latency","all"}:
                for index in range(self.samples):
                    self.measure_one(index)
                event = [r for r in self.records if r["phase"] == "event"]
                result["event_to_durable_job"] = summary([{"elapsed_ms":r["durable_job_upper_ms"]} for r in event])
                result["idle_small_text_to_readable"] = summary([{"elapsed_ms":r["readable_upper_ms"]} for r in event])
                result["event_to_durable_job"]["target_pass"] = result["event_to_durable_job"]["p95_ms"] <= 10000
                result["idle_small_text_to_readable"]["target_pass"] = result["idle_small_text_to_readable"]["p95_ms"] <= 60000
            if mode in {"uploads","all"}:
                uploads = {"on":[],"off":[]}
                # Exact requested counts in capped ABBA blocks. Restart and
                # reconciliation intervals are recorded as excluded warmups.
                for phase, (condition, count) in enumerate(abba_plan(self.samples)):
                    if condition == "off":
                        if not self.restore_required:
                            self.off()
                        for _ in range(count):
                            sample = len(uploads["off"])
                            start,end,status = self.put(f"off-{phase}-{sample:04d}.txt",payload(self.nonce,sample))
                            fid,etag=self.file_identity(f"off-{phase}-{sample:04d}.txt")
                            require(fid not in self.seen_files,"off_sample_not_independent")
                            self.seen_files.add(fid)
                            row={"sample":sample,"phase":"upload-off","file_id":fid,
                                 "etag_sha256":hashlib.sha256(etag.encode()).hexdigest(),
                                 "elapsed_ms":round((end-start)*1000,3),"put_http_status":status}
                            uploads["off"].append(row);self.record(row)
                    else:
                        if self.restore_required:
                            self.on()
                        for _ in range(count):
                            sample=len(uploads["on"])
                            row=self.measure_one(sample,prefix=f"on-{phase}")
                            uploads["on"].append({"elapsed_ms":row["upload_ms"]})
                if self.restore_required:
                    self.on()
                result["upload_on"],result["upload_off"] = summary(uploads["on"]),summary(uploads["off"])
                baseline=result["upload_off"]["p95_ms"]
                require(baseline > 0,"invalid_upload_baseline")
                increase=(result["upload_on"]["p95_ms"]/baseline-1)*100
                result["upload_p95_increase_percent"] = round(increase,3)
                result["upload_target_pass"] = increase <= 10
            self.wait_idle()
            require(self.identity() == self.baseline,"final_authority_or_generation_changed")
            result.update(samples=self.records,restart_warmups=self.warmups,
                          sender_passes=self.sender.passes,measurement_complete=True)
            return result
        finally:
            if self.restore_required:
                self.on()
            self.sender.stop()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch",required=True,type=Path)
    parser.add_argument("--samples",type=int,default=100)
    parser.add_argument("--mode",choices=("latency","uploads","all"),default="all")
    args=parser.parse_args()
    require(100 <= args.samples <= 1000,"samples_must_be_100_to_1000")
    probe=None
    try:
        probe=Probe(args.scratch,args.samples)
        destination=probe.directory/("p5-performance-"+probe.nonce+".json")
        require(not destination.exists(),"report_exists")
        result=probe.run(args.mode)
        destination.write_text(json.dumps(result,indent=2)+"\n")
        destination.chmod(0o600)
        print(json.dumps({"project":probe.state["project"],"report":str(destination),
                          "samples":len(probe.records),"measurement_complete":True}))
    except Exception as error:
        # Persist already-observed timings without HTTP responses/credentials.
        if probe:
            failure=probe.directory/("p5-performance-"+probe.nonce+"-failed.json")
            failure.write_text(json.dumps({"measurement_complete":False,"error_type":type(error).__name__,
                                         "samples":probe.records,"restart_warmups":probe.warmups},indent=2)+"\n")
            failure.chmod(0o600)
        print("Owned P5 probe failed: "+type(error).__name__,file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
