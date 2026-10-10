#!/usr/bin/env python3
"""Offline method checks only; these tests never produce measured P5 claims."""
import json
from pathlib import Path
import runpy
import sqlite3
import tempfile
import unittest
from unittest import mock
import uuid

P = runpy.run_path(str(Path(__file__).with_name("synthetic-ldap-performance.py")))


class PerformanceMethodTest(unittest.TestCase):
    def test_nearest_rank_and_small_sample_rejection(self):
        self.assertEqual(P["quantile"](list(range(1,101))),95)
        with self.assertRaises(RuntimeError):
            P["summary"]([{"elapsed_ms":1}]*99)
        for value in [-1, float("nan"), float("inf")]:
            with self.assertRaises(RuntimeError):
                P["quantile"]([value])

    def test_abba_counts_are_exact_and_not_repeated_samples(self):
        for size in (100,101,123,1000):
            plan=P["abba_plan"](size)
            self.assertEqual(sum(n for mode,n in plan if mode=="on"),size)
            self.assertEqual(sum(n for mode,n in plan if mode=="off"),size)
            self.assertTrue(all(0<n<=25 for _,n in plan))
        self.assertEqual([x[0] for x in P["abba_plan"](100)],
                         ["on","off","off","on","on","off","off","on"])
        for size in (99,1001):
            with self.assertRaises(RuntimeError):
                P["abba_plan"](size)

    def test_owned_runtime_requires_the_fixed_binding_and_scope(self):
        runtime={name:str(uuid.uuid4()) for name in
                 ("source_id","knowledge_base_id","operation_id","model_id","chat_model_id")}
        runtime.update(binding_id="synthetic-published",tenant_id=7,file_id=17)
        fixture={"synthetic_fixture":True,"binding_id":"synthetic-published"}
        P["validate_runtime"](runtime,fixture)
        for field,value in [("binding_id","pilot-load-old"),("tenant_id",True),("file_id",0)]:
            changed=dict(runtime);changed[field]=value
            with self.assertRaises(RuntimeError):
                P["validate_runtime"](changed,fixture)

    def test_off_requires_idle_then_stops_builders_before_disabling(self):
        probe=object.__new__(P["Probe"])
        calls=[]
        probe.wait_idle=lambda:calls.append("idle")
        probe.sender=mock.Mock(stop=lambda:calls.append("sender-stop"))
        probe.compose_action=lambda action:calls.append("app-"+action)
        probe.assert_owned=lambda:calls.append("owned")
        probe.state={}
        probe.restore_required=False
        with mock.patch.dict(P["Probe"].off.__globals__["e2e"],
                             {"occ":lambda *args:calls.append("disable")}) as _:
            probe.off()
        self.assertEqual(calls,["idle","sender-stop","idle","app-stop","owned","disable"])
        self.assertTrue(probe.restore_required)

    def test_observer_reports_request_time_instead_of_false_half_second_bound(self):
        report=P["polling_report"]([
            {"request_start_ms":0,"request_end_ms":700,"previous_request_start_ms":None},
            {"request_start_ms":1200,"request_end_ms":1900,"previous_request_start_ms":0}])
        self.assertEqual(report["configured_sleep_ms"],500)
        self.assertEqual(report["max_actual_observation_interval_ms"],1900)

    def test_idle_requires_unsent_source_outbox_to_drain(self):
        row={key:0 for key in ("running","processing","staged","content_leases","body_leases","auto_pending")}
        row.update(received=7,applied=7)
        cloud={"outbox_id":8,"sender_received":7,"sender_status":"active"}
        self.assertFalse(P["idle_ready"](row,cloud))
        row.update(received=8,applied=8);cloud["sender_received"]=8
        self.assertTrue(P["idle_ready"](row,cloud))
        for key in ("running","processing","staged","content_leases","body_leases","auto_pending"):
            changed=dict(row);changed[key]=1
            self.assertFalse(P["idle_ready"](changed,cloud))
        bad=dict(cloud);bad["sender_received"]=True
        self.assertFalse(P["idle_ready"](row,bad))

    def test_idle_counts_direct_and_message_body_leases_without_parent_rows(self):
        probe=object.__new__(P["Probe"])
        probe.runtime={"tenant_id":7,"knowledge_base_id":str(uuid.uuid4()),"source_id":str(uuid.uuid4())}
        probe.sql=mock.Mock(return_value={})
        probe.state_snapshot()
        query=probe.sql.call_args.args[0]
        body_query=query.split("'body_leases',(",1)[1].split("),'auto_pending'",1)[0]
        self.assertIn("JOIN original_body_payloads b ON b.id=l.body_id",body_query)
        self.assertIn("b.tenant_id=7",body_query)
        self.assertNotIn("original_body_parent_refs",body_query)
        self.assertNotIn("knowledge_base_id",body_query)
        clock="EXTRACT(EPOCH FROM clock_timestamp())*1000"
        self.assertIn(clock,body_query)
        # Execute the actual count predicate with only its PostgreSQL clock
        # expression replaced by a bound test clock. No parent table exists.
        with sqlite3.connect(":memory:") as database:
            database.executescript("""
                CREATE TABLE original_body_payloads(id TEXT PRIMARY KEY,tenant_id INTEGER);
                CREATE TABLE original_body_leases(lease_id TEXT PRIMARY KEY,body_id TEXT,
                    released_at_ms INTEGER,expires_at_ms INTEGER);
                INSERT INTO original_body_payloads VALUES('direct-knowledge',7),('message-scope-only',7),('other-tenant',8);
                INSERT INTO original_body_leases VALUES('direct','direct-knowledge',NULL,6000),
                    ('message','message-scope-only',NULL,6000),('expired','direct-knowledge',NULL,2999),
                    ('released','message-scope-only',1000,6000),('other','other-tenant',NULL,6000);
            """)
            count=database.execute(body_query.replace(clock,"?"),(3000,)).fetchone()[0]
        self.assertEqual(count,2)
        row={key:0 for key in ("running","processing","staged","content_leases","body_leases","auto_pending")}
        row.update(received=8,applied=8,body_leases=count)
        cloud={"outbox_id":8,"sender_received":8,"sender_status":"active"}
        self.assertFalse(P["idle_ready"](row,cloud))

    def test_raw_sample_is_written_as_it_is_observed(self):
        probe=object.__new__(P["Probe"])
        probe.records=[]
        with tempfile.TemporaryDirectory() as directory:
            probe.sample_path=Path(directory)/"samples.jsonl"
            sample={"sample":0,"put_http_status":201,"elapsed_ms":3.2}
            probe.record(sample)
            self.assertEqual(json.loads(probe.sample_path.read_text()),sample)
            self.assertEqual(probe.records,[sample])

    def test_sender_marker_covers_sleep_and_is_removed_only_after_join(self):
        sender_type=P['Sender'];globals_=sender_type.start.__globals__
        with tempfile.TemporaryDirectory() as directory:
            probe=mock.Mock(directory=Path(directory).resolve(),state={'owner_token':'a'*32})
            probe.assert_owned=mock.Mock()
            thread=mock.Mock();thread.is_alive.return_value=False
            fake_threading=mock.Mock(Thread=mock.Mock(return_value=thread),Event=mock.Mock())
            with mock.patch.dict(globals_,{'threading':fake_threading}):
                sender=sender_type(probe);sender.start()
                marker=probe.directory/'active-probe-workers.json'
                self.assertTrue(marker.exists());self.assertEqual(marker.stat().st_mode&0o777,0o600)
                self.assertEqual(json.loads(marker.read_text())['kind'],'synthetic-p5-event-sender')
                # It is present even when no Docker CLI happens to be running.
                thread.is_alive.return_value=True
                with self.assertRaisesRegex(RuntimeError,'sender_stop_timeout'):sender.stop()
                self.assertTrue(marker.exists())
                thread.is_alive.return_value=False;sender.stop()
                self.assertFalse(marker.exists())


if __name__=="__main__":
    unittest.main()
