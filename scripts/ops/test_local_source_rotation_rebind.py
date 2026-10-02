#!/usr/bin/env python3
"""Safety checks for the local source-rotation event rebind operation."""

import copy
from pathlib import Path
import runpy
import unittest
from unittest import mock


module = runpy.run_path(str(Path(__file__).with_name("local-source-rotation.py")))
rebind = module["rebind_and_resume_event_sender"]
require_rotation_scope = module["require_rotation_scope"]
event_connection_url = module["event_connection_url"]

OPERATION = "00000000-0000-4000-8000-000000000001"
CONNECTION = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
KEY = "evt_1234567890abcdef1234567890abcdef"
BASE = "http://127.0.0.1:18081"
NC_URL = "http://127.0.0.1:18082/index.php/apps/integration_weknora/api/v1/admin/bindings/dev-published/event-connection"


def sender(status="paused", reason="receiver_unauthorized"):
    return {
        "binding_id": "dev-published", "connection_id": CONNECTION, "key_id": KEY,
        "receiver_url": "http://weknora:8080/api/v1/integrations/nextcloud/events",
        "status": status, "last_error_code": reason,
        "received_through_event_id": "4", "applied_through_event_id": "4",
    }


def receiver(status="source_unpaired"):
    return {
        "binding_id": "dev-published", "nextcloud_instance_id": "instance-test",
        "connection_id": CONNECTION, "key_id": KEY,
        "receiver_url": "/api/v1/integrations/nextcloud/events", "status": status,
        "received_through_event_id": "4", "dispatched_through_event_id": "4",
        "applied_through_event_id": "4", "dispatch_state": "idle",
    }


def rebound(rebound_value=True):
    return {
        "rotation_id": OPERATION, "connection_id": CONNECTION, "key_id": KEY,
        "status": "active", "rebound": rebound_value,
        "received_through_event_id": "4", "dispatched_through_event_id": "4",
        "applied_through_event_id": "4", "dispatch_state": "idle",
    }


class FakeServices:
    def __init__(self, initial_sender=None, initial_receiver=None, after_sender=None,
                 after_receiver=None, response=None, retry_status=200,
                 retry_response=None, retry_receiver=None):
        self.senders = [initial_sender or sender(), after_sender or sender()]
        final_receiver = after_receiver or receiver("active")
        self.receivers = [initial_receiver or receiver(), final_receiver,
                          retry_receiver or copy.deepcopy(final_receiver)]
        self.response = response or rebound()
        self.retry_status = retry_status
        self.retry_response = retry_response or sender("active")
        self.nc_posts = []
        self.wk_posts = []

    def nc_request(self, _session, _csrf, url, method, payload=None):
        if method == "GET":
            return 200, copy.deepcopy(self.senders.pop(0))
        self.nc_posts.append((url, copy.deepcopy(payload)))
        if self.retry_status != 200:
            return self.retry_status, {"error": "connection_changed"}
        return 200, copy.deepcopy(self.retry_response)

    def wk_request(self, _base, _token, method, path, payload=None):
        if method == "GET":
            return 200, copy.deepcopy(self.receivers.pop(0))
        self.wk_posts.append((path, copy.deepcopy(payload)))
        return 200, copy.deepcopy(self.response)


class RebindSenderTest(unittest.TestCase):
    def test_binding_named_source_pairing_is_not_rewritten(self):
        self.assertEqual(
            event_connection_url("http://nextcloud/index.php/apps/integration_weknora/api/v1",
                                 "source-pairing"),
            "http://nextcloud/index.php/apps/integration_weknora/api/v1/admin/bindings/"
            "source-pairing/event-connection",
        )

    def call(self, fake):
        with mock.patch.dict(rebind.__globals__, {
            "nc_request": fake.nc_request, "wk_request": fake.wk_request,
        }):
            return rebind(object(), "csrf", NC_URL, BASE, "token", "source-id",
                          "dev-published", "instance-test", OPERATION)

    def test_paused_source_denial_is_resumed_with_exact_cas(self):
        fake = FakeServices()
        connection, resumed = self.call(fake)
        self.assertTrue(connection["rebound"])
        self.assertEqual(resumed["action"], "resumed")
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(fake.wk_posts[0][1], {"operation_id": OPERATION})
        self.assertEqual(fake.nc_posts, [(NC_URL + "/retry", {
            "connection_id": CONNECTION, "key_id": KEY,
            "received_through_event_id": "4",
        })])

    def test_replay_with_active_sender_does_not_retry(self):
        fake = FakeServices(initial_sender=sender("active", ""),
                            after_sender=sender("active", ""),
                            initial_receiver=receiver("active"), response=rebound(False))
        connection, sender_status = self.call(fake)
        self.assertFalse(connection["rebound"])
        self.assertEqual(sender_status["action"], "already_active")
        self.assertEqual(fake.nc_posts, [])

    def test_wrong_connection_key_or_cursor_blocks_before_rebind(self):
        for name, mutate in (
            ("connection", lambda row: row.update(connection_id="other-connection-0001")),
            ("key", lambda row: row.update(key_id="evt_other")),
            ("cursor", lambda row: row.update(received_through_event_id="5")),
        ):
            with self.subTest(name=name):
                wrong = receiver()
                mutate(wrong)
                fake = FakeServices(initial_receiver=wrong)
                with self.assertRaises(RuntimeError):
                    self.call(fake)
                self.assertEqual(fake.wk_posts, [])
                self.assertEqual(fake.nc_posts, [])

    def test_rebind_response_cannot_change_key_or_watermark(self):
        wrong = rebound()
        wrong["received_through_event_id"] = "3"
        fake = FakeServices(response=wrong)
        with self.assertRaisesRegex(RuntimeError, "out of order|regressed"):
            self.call(fake)
        self.assertEqual(fake.nc_posts, [])

    def test_receiver_dispatch_and_applied_can_advance_during_rebind(self):
        pending_sender = sender()
        pending_sender["received_through_event_id"] = "5"
        pending_receiver = receiver()
        pending_receiver["received_through_event_id"] = "5"
        advanced_receiver = receiver("active")
        advanced_receiver["received_through_event_id"] = "5"
        advanced_receiver["dispatched_through_event_id"] = "5"
        advanced_receiver["applied_through_event_id"] = "5"
        advanced_response = rebound()
        advanced_response["received_through_event_id"] = "5"
        advanced_response["dispatched_through_event_id"] = "5"
        advanced_response["applied_through_event_id"] = "5"
        resumed_sender = sender("active")
        resumed_sender["received_through_event_id"] = "5"
        fake = FakeServices(initial_sender=pending_sender, after_sender=pending_sender,
                            initial_receiver=pending_receiver,
                            after_receiver=advanced_receiver, response=advanced_response,
                            retry_response=resumed_sender)
        connection, result = self.call(fake)
        self.assertEqual(connection["applied_through_event_id"], "5")
        self.assertEqual(result["action"], "resumed")
        self.assertEqual(fake.nc_posts[0][1]["received_through_event_id"], "5")

    def test_rebind_replay_with_blocked_dispatch_never_resumes_sender(self):
        blocked = receiver("active")
        blocked["dispatch_state"] = "blocked"
        blocked_response = rebound(False)
        blocked_response["dispatch_state"] = "blocked"
        fake = FakeServices(after_receiver=blocked, response=blocked_response)
        with self.assertRaisesRegex(RuntimeError, "dispatch needs manual review"):
            self.call(fake)
        self.assertEqual(fake.nc_posts, [])

    def test_active_sender_watermark_regression_is_rejected(self):
        regressed = sender("active", "")
        regressed["applied_through_event_id"] = "3"
        fake = FakeServices(initial_sender=sender("active", ""),
                            after_sender=regressed, initial_receiver=receiver("active"),
                            response=rebound(False))
        with self.assertRaisesRegex(RuntimeError, "watermarks regressed"):
            self.call(fake)
        self.assertEqual(fake.nc_posts, [])

    def test_retry_response_cannot_regress_applied_watermark(self):
        regressed = sender("active")
        regressed["applied_through_event_id"] = "3"
        fake = FakeServices(retry_response=regressed)
        with self.assertRaisesRegex(RuntimeError, "did not preserve"):
            self.call(fake)
        self.assertEqual(len(fake.nc_posts), 1)

    def test_receiver_blocked_after_cas_is_not_reported_as_recovered(self):
        blocked = receiver("active")
        blocked["dispatch_state"] = "blocked"
        fake = FakeServices(retry_receiver=blocked)
        with self.assertRaisesRegex(RuntimeError, "dispatch now needs manual review"):
            self.call(fake)
        self.assertEqual(len(fake.nc_posts), 1)

    def test_receiver_source_changes_after_cas_is_not_reported_as_recovered(self):
        fake = FakeServices(retry_receiver=receiver("source_unpaired"))
        with self.assertRaisesRegex(RuntimeError, "dispatch now needs manual review"):
            self.call(fake)
        self.assertEqual(len(fake.nc_posts), 1)

    def test_receiver_dispatch_watermark_regression_after_cas_is_rejected(self):
        initial_sender = sender()
        initial_sender["applied_through_event_id"] = "2"
        initial_receiver = receiver()
        initial_receiver["dispatched_through_event_id"] = "3"
        initial_receiver["applied_through_event_id"] = "3"
        advanced = receiver("active")
        advanced["applied_through_event_id"] = "3"
        response = rebound()
        response["applied_through_event_id"] = "3"
        regressed = receiver("active")
        regressed["dispatched_through_event_id"] = "3"
        regressed["applied_through_event_id"] = "3"
        resumed = sender("active")
        resumed["applied_through_event_id"] = "2"
        fake = FakeServices(initial_sender=initial_sender, after_sender=initial_sender,
                            initial_receiver=initial_receiver, after_receiver=advanced,
                            response=response, retry_response=resumed,
                            retry_receiver=regressed)
        with self.assertRaisesRegex(RuntimeError, "receiver watermarks regressed"):
            self.call(fake)
        self.assertEqual(len(fake.nc_posts), 1)

    def test_other_paused_reason_requires_manual_review(self):
        fake = FakeServices(initial_sender=sender("paused", "receiver_checkpoint_conflict"),
                            after_sender=sender("paused", "receiver_checkpoint_conflict"))
        with self.assertRaisesRegex(RuntimeError, "manual review"):
            self.call(fake)
        self.assertEqual(len(fake.wk_posts), 1)
        self.assertEqual(fake.nc_posts, [])

    def test_sender_change_at_cas_is_not_reported_as_recovered(self):
        fake = FakeServices(retry_status=409)
        with self.assertRaisesRegex(RuntimeError, "CAS retry failed: HTTP 409"):
            self.call(fake)
        self.assertEqual(len(fake.nc_posts), 1)

    def test_rotations_must_match_active_pair_scope(self):
        pair = {"binding_id": "dev-published", "operation_id": OPERATION,
                "tenant_id": "7", "instance_id": "instance-test",
                "knowledge_base_id": "kb-test", "data_source_id": "source-id",
                "publication_epoch": 2, "key_id": "rot_new"}
        nc = {**pair, "operation_id": "00000000-0000-4000-8000-000000000002",
              "pair_operation_id": OPERATION, "old_key_id": "default",
              "new_key_id": "rot_new"}
        wk = {name: nc[name] for name in (
            "binding_id", "instance_id", "knowledge_base_id", "data_source_id",
            "operation_id", "pair_operation_id", "old_key_id", "new_key_id")}
        require_rotation_scope(pair, nc, wk, "dev-published", OPERATION,
                               nc["operation_id"], "7")
        wk["knowledge_base_id"] = "another-kb"
        with self.assertRaisesRegex(RuntimeError, "WeKnora rotation differs"):
            require_rotation_scope(pair, nc, wk, "dev-published", OPERATION,
                                   nc["operation_id"], "7")


if __name__ == "__main__":
    unittest.main()
