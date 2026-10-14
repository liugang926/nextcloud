#!/usr/bin/env python3
"""Fail-closed contracts for the isolated identity-conflict HTTP probe."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


PATH = Path(__file__).with_name("synthetic-identity-conflict-smoke.py")
SPEC = importlib.util.spec_from_file_location("identity_conflicts", PATH)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)

A = {"nextcloud_uid": "alice", "object_guid": "11111111-2222-3333-4444-555555555555"}
B = {"nextcloud_uid": "bob", "object_guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}


class IdentityConflictProbeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fixture = Path(self.directory.name) / "fixture.json"
        self.fixture.write_text(json.dumps({"schema_version": 1, "synthetic_fixture": True,
                                            "directory_id": "test-ad", "accounts": {"a": A, "b": B}}))

    def test_fixture_requires_distinct_stable_identities(self):
        self.assertEqual(PROBE.parse_fixture(self.fixture)[0], "test-ad")
        data = json.loads(self.fixture.read_text())
        data["accounts"]["b"]["object_guid"] = A["object_guid"]
        self.fixture.write_text(json.dumps(data))
        with self.assertRaises(PROBE.ConflictProbeError):
            PROBE.parse_fixture(self.fixture)

    def test_cross_guid_requests_are_checked_and_registry_stays_unchanged(self):
        rows = [{"directory_id": "test-ad", "object_guid": account["object_guid"],
                 "nextcloud_uid": account["nextcloud_uid"]} for account in (A, B)]
        calls = []

        def response(opener, method, url, *, payload=None, csrf=None):
            calls.append((method, payload))
            if method == "GET":
                return 200, {"identities": rows}
            if payload == {"directory_id": "test-ad", "object_guid": A["object_guid"],
                           "nextcloud_uid": A["nextcloud_uid"]}:
                return 200, {"object_guid": A["object_guid"]}
            return 400, {"error": "invalid_identity"}

        with mock.patch.object(PROBE, "login", return_value=(object(), "csrf")), \
                mock.patch.object(PROBE, "json_request", side_effect=response):
            report = PROBE.check_conflicts("http://127.0.0.1:18192", self.fixture,
                                           "admin", "secret")
        self.assertEqual(report["conflicting_mappings_denied"], 5)
        self.assertEqual(sum(method == "POST" for method, _ in calls), 6)
        self.assertEqual(sum(method == "GET" for method, _ in calls), 2)

    def test_unexpected_insert_is_revoked_and_fails(self):
        rows = [{"directory_id": "test-ad", "object_guid": account["object_guid"],
                 "nextcloud_uid": account["nextcloud_uid"]} for account in (A, B)]
        calls = []

        def response(opener, method, url, *, payload=None, csrf=None):
            calls.append((method, url))
            if method == "GET":
                return 200, {"identities": rows}
            if url.endswith("/revoke"):
                return 200, {"revoked": True}
            if payload["nextcloud_uid"] == A["nextcloud_uid"]:
                return 200, {"object_guid": A["object_guid"]}
            return 201, {"object_guid": A["object_guid"]}

        with mock.patch.object(PROBE, "login", return_value=(object(), "csrf")), \
                mock.patch.object(PROBE, "json_request", side_effect=response):
            with self.assertRaisesRegex(PROBE.ConflictProbeError, "not rejected"):
                PROBE.check_conflicts("http://127.0.0.1:18192", self.fixture,
                                      "admin", "secret")
        self.assertTrue(any(url.endswith("/revoke") for _, url in calls))


if __name__ == "__main__":
    unittest.main()
