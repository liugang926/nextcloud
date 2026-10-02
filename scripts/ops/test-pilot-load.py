#!/usr/bin/env python3
"""Offline checks for the pilot harness safety boundary and statistics."""

import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest
from unittest import mock


SOURCE = Path(__file__).with_name("pilot-load.py")
SPEC = importlib.util.spec_from_file_location("pilot_load", SOURCE)
PILOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PILOT)


class PilotLoadTests(unittest.TestCase):
    def parse(self, *extra):
        with mock.patch.object(sys, "argv", [str(SOURCE),
                "--nextcloud-origin", "http://127.0.0.1:18195",
                "--weknora-origin", "http://127.0.0.1:18196",
                "--nextcloud-env-file", str(SOURCE),
                "--weknora-admin-env-file", str(SOURCE),
                "--nextcloud-compose-project", "pilot-test-nc",
                "--weknora-compose-project", "pilot-test-wk",
                "--nextcloud-compose-directory", str(SOURCE.parents[2]),
                "--embedding-model-id", "builtin-pilot-mock",
                *extra]):
            return PILOT.parse_args()

    def test_small_default_and_nearest_rank(self):
        args = self.parse()
        self.assertEqual((args.file_count, args.file_bytes, args.event_samples), (8, 1024, 20))
        self.assertEqual(PILOT.percentile_nearest_rank(list(range(1, 21)), .95), 19)
        self.assertEqual(len(PILOT.payload(6, 1049)), 1049)

    def test_post_accept_measures_a_new_task_after_previous_queue_acceptance(self):
        sequence = []
        next_id = 0

        def write():
            nonlocal next_id
            next_id += 1
            sequence.append(("write", next_id))
            return next_id

        def wait(field, event_id):
            sequence.append((field, event_id))
            return 2.0 if field == "applied_through_event_id" else 1.0

        times, applied, measured, priming = PILOT.measure_event_queue(
            2, "post-accept", write, wait, clock=lambda: 0.0)
        self.assertEqual((times, applied, measured, priming),
                         ([1000.0, 1000.0], [2000.0, 2000.0], [2, 4], [1, 3]))
        self.assertEqual(sequence, [
            ("write", 1), ("dispatched_through_event_id", 1),
            ("write", 2), ("dispatched_through_event_id", 2),
            ("applied_through_event_id", 2),
            ("write", 3), ("dispatched_through_event_id", 3),
            ("write", 4), ("dispatched_through_event_id", 4),
            ("applied_through_event_id", 4),
        ])

    def test_optional_observer_sees_only_measured_events_and_closes_on_failure(self):
        seen = []
        next_id = 0

        class Observer:
            def __init__(self, event_id):
                self.event_id = event_id

            def __enter__(self):
                seen.append(("enter", self.event_id))

            def __exit__(self, *_):
                seen.append(("exit", self.event_id))

        def write():
            nonlocal next_id
            next_id += 1
            return next_id

        def wait(field, event_id):
            if event_id == 4 and field == "applied_through_event_id":
                raise RuntimeError("synthetic wait failure")
            return 1.0

        with self.assertRaisesRegex(RuntimeError, "synthetic wait failure"):
            PILOT.measure_event_queue(2, "post-accept", write, wait,
                clock=lambda: 0.0,
                observer_factory=lambda event_id, _: Observer(event_id))
        self.assertEqual(seen, [("enter", 2), ("exit", 2),
                                ("enter", 4), ("exit", 4)])

    def test_hop_recorder_distinguishes_sender_recheck_admission_and_worker(self):
        recorder = PILOT.HopRecorder(22)
        base = {
            "inbox_state": None, "inbox_received_unix_ms": None,
            "checkpoint_received_id": 21, "dispatch_state": "queued",
            "dispatch_target_event_id": 21, "dispatch_dispatched_id": 21,
            "dispatch_next_attempt_unix_ms": 12000,
            "dispatch_updated_unix_ms": 7000,
            "sync_status": "running", "sync_log_created_unix_ms": 7100,
            "db_now_unix_ms": 11000,
        }
        recorder.observe({"outbox_created_unix_s": 10, "sender_received_id": 21,
                          "sender_status": "active"}, base, 100)
        received = {**base, "inbox_state": "pending", "inbox_received_unix_ms": 11250,
                    "checkpoint_received_id": 22, "db_now_unix_ms": 11500}
        recorder.observe({"outbox_created_unix_s": 10, "sender_received_id": 22,
                          "sender_status": "active"}, received, 500)
        recorder.observe({"outbox_created_unix_s": 10, "sender_received_id": 22,
                          "sender_status": "active"},
                         {**received, "db_now_unix_ms": 12001}, 1000)
        accepted = {**received, "dispatch_state": "queued", "dispatch_target_event_id": 22,
                    "dispatch_dispatched_id": 22, "dispatch_updated_unix_ms": 12345,
                    "sync_status": "running", "sync_log_created_unix_ms": 12200,
                    "db_now_unix_ms": 12400}
        recorder.observe({"outbox_created_unix_s": 10, "sender_received_id": 22,
                          "sender_status": "active"}, accepted, 1500)
        recorder.observe({"outbox_created_unix_s": 10, "sender_received_id": 22,
                          "sender_status": "active"},
                         {**accepted, "sync_status": "running",
                          "sync_worker_started_unix_ms": 12500},
                         1700)
        report = recorder.report()
        self.assertEqual(report["first_observed_after_put_start_ms"], {
            "outbox_row": 100, "sender_receipt": 500, "weknora_inbox": 500,
            "waiting_behind_prior_queue": 500, "prior_queue_recheck_due": 1000,
            "prior_queue_candidate_due": 1000,
            "queue_log_created": 1500, "durable_queue_accept": 1500,
            "worker_started": 1700,
        })
        self.assertEqual(report["database_timestamps"], {
            "outbox_created_unix_s": 10, "inbox_received_unix_ms": 11250,
            "prior_queue_scheduled_recheck_unix_ms": 12000,
            "dispatch_row_updated_unix_ms_at_first_accept_observation": 12345,
            "queue_log_created_unix_ms": 12200,
            "worker_started_unix_ms": 12500,
        })
        self.assertEqual(report["state_changes"][1]["prior_queue_wait"], True)
        self.assertEqual(report["state_changes"][-1]["sync"], "running")

    def test_hop_report_redacts_unknown_fields_and_states(self):
        recorder = PILOT.HopRecorder(8)
        recorder.observe({"outbox_created_unix_s": 99, "sender_received_id": 8,
                          "sender_status": "password=private", "secret_ciphertext": "private"},
                         {"inbox_state": "file path private", "path": "private",
                          "dispatch_state": "token private", "sync_status": "private",
                          "last_error_code": "private", "dispatch_target_event_id": 8,
                          "dispatch_dispatched_id": 8, "db_now_unix_ms": 100000}, 10)
        encoded = json.dumps(recorder.report())
        self.assertNotIn("private", encoded)
        self.assertNotIn("password", encoded)
        self.assertIsNone(recorder.report()["state_changes"][0]["dispatch"])

    def test_completed_prior_sync_can_wake_queue_before_scheduled_recheck(self):
        recorder = PILOT.HopRecorder(42)
        recorder.observe({"sender_received_id": 42, "sender_status": "active"}, {
            "inbox_state": "pending", "inbox_received_unix_ms": 1000,
            "checkpoint_received_id": 42, "dispatch_state": "queued",
            "dispatch_target_event_id": 41, "dispatch_dispatched_id": 41,
            "dispatch_next_attempt_unix_ms": 20000,
            "dispatch_error_clear": True, "sync_status": "success",
            "sync_finished_unix_ms": 1100, "db_now_unix_ms": 1200,
        }, 300)
        first = recorder.report()["first_observed_after_put_start_ms"]
        self.assertEqual(first["prior_queue_success_wake"], 300)
        self.assertEqual(first["prior_queue_candidate_due"], 300)
        self.assertNotIn("prior_queue_recheck_due", first)
        self.assertTrue(recorder.report()["state_changes"][0]["prior_queue_success_wake"])

    def test_queued_row_update_is_visible_without_state_change(self):
        recorder = PILOT.HopRecorder(42)
        baseline = {"checkpoint_received_id": 42, "dispatch_state": "queued",
                    "dispatch_target_event_id": 41, "dispatch_dispatched_id": 41,
                    "dispatch_next_attempt_unix_ms": 20000,
                    "dispatch_updated_unix_ms": 1000, "db_now_unix_ms": 1100}
        recorder.observe({}, baseline, 100)
        recorder.observe({}, {**baseline, "dispatch_next_attempt_unix_ms": 21000,
                              "dispatch_updated_unix_ms": 1200}, 200)
        states = recorder.report()["state_changes"]
        self.assertEqual(len(states), 2)
        self.assertEqual(states[1]["dispatch_row_updated_unix_ms"], 1200)
        self.assertEqual(states[1]["scheduled_recheck_unix_ms"], 21000)

    def test_hop_snapshot_queries_are_read_only_and_scoped(self):
        queries = []

        def sql(container, query, timeout):
            queries.append((container, query, timeout))
            return {}

        with mock.patch.object(PILOT, "docker_sql", side_effect=sql):
            PILOT.hop_snapshot("nc-db", "wk-db", "pilot-load-0123456789abcdef",
                               "00000000-0000-0000-0000-000000000001",
                               "00000000-0000-0000-0000-000000000002", 7, 22)
        self.assertEqual(len(queries), 2)
        self.assertEqual([row[0] for row in queries], ["nc-db", "wk-db"])
        self.assertEqual([row[2] for row in queries], [4, 4])
        self.assertTrue(all(query.startswith(("SELECT", "WITH")) for _, query, _ in queries))
        self.assertIn("binding_id='pilot-load-0123456789abcdef' AND id=22", queries[0][1])
        self.assertIn("connection_id='00000000-0000-0000-0000-000000000001' AND datasource_id='00000000-0000-0000-0000-000000000002'", queries[1][1])
        self.assertIn("connection_id=(SELECT connection_id FROM c) AND event_id=22", queries[1][1])
        self.assertIn("data_source_id='00000000-0000-0000-0000-000000000002'", queries[1][1])
        self.assertNotIn("secret", queries[0][1] + queries[1][1])
        self.assertFalse(any(re.search(r"\b(?:INSERT|UPDATE|DELETE|ALTER|DROP)\b", query)
                             for _, query, _ in queries))

    def test_sampler_errors_do_not_echo_database_exception(self):
        def failure(_):
            raise RuntimeError("password=private file text")

        sampler = PILOT.HopSampler(9, 0.0, failure)
        with mock.patch.object(PILOT, "MAX_HOP_POLLS", 1), \
             mock.patch.object(PILOT, "HOP_POLL_SECONDS", 0):
            sampler._sample()
        encoded = json.dumps(sampler.recorder.report())
        self.assertNotIn("private", encoded)
        self.assertEqual(sampler.recorder.report()["diagnostic_codes"],
                         ["read_only_snapshot_unavailable"])

    def test_large_fixture_needs_explicit_opt_in(self):
        with self.assertRaises(SystemExit):
            self.parse("--file-count", "101")
        args = self.parse("--pilot-10k-100gb")
        self.assertEqual(args.file_count * args.file_bytes, 100_000_000_000)
        with self.assertRaises(SystemExit):
            self.parse("--pilot-10k-100gb", "--file-count", "12")
        with self.assertRaises(SystemExit):
            self.parse("--hop-diagnostics")

    def test_shared_or_non_loopback_project_is_rejected_before_docker(self):
        with self.assertRaises(SystemExit):
            self.parse("--nextcloud-compose-project", "nextcloud-weknora-dev")
        with self.assertRaises(SystemExit):
            self.parse("--nextcloud-origin", "http://10.0.0.4:18195")

    def test_direct_source_alias_must_have_one_owner(self):
        nc_network = "pilot-test-nc_default"
        wk_network = "pilot-test-wk_default"
        nc = {"Id": "nc", "Config": {"Labels": {"com.docker.compose.project": "pilot-test-nc"}},
              "NetworkSettings": {"Ports": {"80/tcp": [{"HostPort": "18195"}]},
                                  "Networks": {nc_network: {"NetworkID": "network-1",
                                                            "Aliases": ["nextcloud"]}}}}
        wk = {"Id": "wk", "Config": {"Labels": {"com.docker.compose.project": "pilot-test-wk"},
                                   "Env": ["WEKNORA_NEXTCLOUD_DEV_HTTP=1",
                                           "WEKNORA_NEXTCLOUD_ALLOWED_ORIGINS=http://nextcloud"]},
              "NetworkSettings": {"Ports": {"8080/tcp": [{"HostPort": "18196"}]},
                                  "Networks": {nc_network: {"NetworkID": "network-1", "Aliases": ["app"]},
                                               wk_network: {"NetworkID": "network-2", "Aliases": ["app"]}}}}
        with mock.patch.object(PILOT, "inspect_network", return_value={"Containers": {"nc": {}, "wk": {}}}), \
             mock.patch.object(PILOT, "inspect", side_effect=lambda key: {"nc": nc, "wk": wk}[key]):
            PILOT.require_direct_source_network(nc, wk, 18195, 18196, "pilot-test-nc")
            wk["NetworkSettings"]["Networks"][nc_network]["Aliases"].append("nextcloud")
            with self.assertRaises(ValueError):
                PILOT.require_direct_source_network(nc, wk, 18195, 18196, "pilot-test-nc")


if __name__ == "__main__":
    unittest.main()
