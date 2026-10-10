#!/usr/bin/env python3
"""Offline contract checks for the operator-run AD permission probe."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("ad-permission-acceptance.py")
SPEC = importlib.util.spec_from_file_location("ad_permission_acceptance", MODULE_PATH)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def fixture_data():
    denied = {"nextcloud_login": True, "ldap_login": True, "dav": False,
              "source": False, "knowledge": False, "search": False}
    allowed = {key: True for key in denied}
    return {
        "schema_version": 1, "synthetic_fixture": True,
        "binding_id": "pilot-test", "directory_id": "corp-ad-test",
        "file_id": 12345, "knowledge_base_id": "kb-test", "knowledge_id": "document-test",
        "synthetic_query": "What is the pilot answer?",
        "accounts": {
            "a": {"nextcloud_uid": "ad-test-a", "weknora_identifier": "ad-test-a",
                  "object_guid": "11111111-1111-1111-1111-111111111111",
                  "dav_path": "Pilot/synthetic.txt"},
            "b": {"nextcloud_uid": "ad-test-b", "weknora_identifier": "ad-test-b",
                  "object_guid": "22222222-2222-2222-2222-222222222222",
                  "dav_path": "Pilot/synthetic.txt"},
        },
        "cases": {"baseline": {"a": allowed, "b": denied}},
    }


class ProbeContractTest(unittest.TestCase):
    def test_fixture_requires_distinct_guids_and_complete_expectations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            good = fixture_data()
            path.write_text(json.dumps(good), encoding="utf-8")
            self.assertEqual(PROBE.fixture(path, "baseline")[0]["file_id"], 12345)
            bad = fixture_data()
            bad["accounts"]["b"]["object_guid"] = bad["accounts"]["a"]["object_guid"]
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(PROBE.ProbeError):
                PROBE.fixture(path, "baseline")
            bad = fixture_data()
            bad["synthetic_fixture"] = False
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(PROBE.ProbeError):
                PROBE.fixture(path, "baseline")
            bad = fixture_data()
            del bad["cases"]["baseline"]["a"]["source"]
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(PROBE.ProbeError):
                PROBE.fixture(path, "baseline")

    def test_missing_explicit_test_environment_refuses_to_run(self):
        with mock.patch.dict(PROBE.os.environ, {}, clear=True):
            with self.assertRaises(PROBE.ProbeError):
                PROBE.required_environment()
        with mock.patch.dict(PROBE.os.environ, {
            "AD_ACCEPTANCE_TEST_ENV": "production", "AD_TEST_A_PASSWORD": "x",
            "AD_TEST_B_PASSWORD": "x", "AD_TEST_BINDING_KEY_ID": "test-key",
            "AD_TEST_BINDING_TOKEN": "x",
        }, clear=True):
            with self.assertRaises(PROBE.ProbeError):
                PROBE.required_environment()

    def test_origins_require_explicit_scope(self):
        with self.assertRaises(PROBE.ProbeError):
            PROBE.origin("http://nextcloud.example.invalid:80",
                         allow_remote_https=True, allow_loopback_http=True)
        with self.assertRaises(PROBE.ProbeError):
            PROBE.origin("https://nextcloud.example.invalid:443",
                         allow_remote_https=False, allow_loopback_http=False)
        self.assertEqual(PROBE.origin("http://127.0.0.1:18082",
                                      allow_remote_https=False, allow_loopback_http=True),
                         "http://127.0.0.1:18082")

    def test_dav_probe_requires_account_root_and_exact_file_id(self):
        account = fixture_data()["accounts"]["a"]
        xml = (b'<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
               b'<d:response><d:propstat><d:prop><oc:fileid>12345</oc:fileid>'
               b'</d:prop></d:propstat></d:response></d:multistatus>')
        with mock.patch.object(PROBE, "http", side_effect=[(207, b""), (207, xml)]) as call:
            self.assertEqual(PROBE.dav_probe("https://localhost:443", account, "secret", 12345),
                             (True, True))
            self.assertEqual(call.call_count, 2)
        with mock.patch.object(PROBE, "http", return_value=(401, b"")) as call:
            self.assertEqual(PROBE.dav_probe("https://localhost:443", account, "secret", 12345),
                             (False, False))
            self.assertEqual(call.call_count, 1)
        with mock.patch.object(PROBE, "http", side_effect=[(207, b""), (207, xml)]):
            with self.assertRaises(PROBE.ProbeError):
                PROBE.dav_probe("https://localhost:443", account, "secret", 999)

    def test_denied_knowledge_and_search_never_accept_http_401(self):
        with mock.patch.object(PROBE, "http", return_value=(401, b"")):
            with self.assertRaises(PROBE.ProbeError):
                PROBE.knowledge_probe("https://localhost:443", "document-test", "jwt")
            with self.assertRaises(PROBE.ProbeError):
                PROBE.search_probe("https://localhost:443", fixture_data(), "jwt")
            with self.assertRaises(PROBE.ProbeError):
                PROBE.direct_content_probe("https://localhost:443", "document-test", "jwt")

    def test_old_jwt_direct_content_requires_both_chunk_and_preview_decisions(self):
        good = json.dumps({"success": True, "data": [{"id": "chunk-test"}]}).encode()
        with mock.patch.object(PROBE, "http", side_effect=[(200, good), (200, b"preview")]):
            self.assertTrue(PROBE.direct_content_probe("https://localhost:443", "document-test", "jwt"))
        with mock.patch.object(PROBE, "http", side_effect=[(403, b""), (404, b"")]):
            self.assertFalse(PROBE.direct_content_probe("https://localhost:443", "document-test", "jwt"))
        with mock.patch.object(PROBE, "http", side_effect=[(200, good), (403, b"")]):
            with self.assertRaises(PROBE.ProbeError):
                PROBE.direct_content_probe("https://localhost:443", "document-test", "jwt")

    def test_search_checks_kb_and_document_scopes_independently(self):
        scopes = []

        def respond(method, url, *, headers=None, payload=None):
            scope = json.loads(payload)
            scopes.append(scope)
            return 200, json.dumps({"success": True, "data": [
                {"knowledge_id": "document-test"}]}).encode()

        with mock.patch.object(PROBE, "http", side_effect=respond):
            self.assertTrue(PROBE.search_probe("https://localhost:443", fixture_data(), "jwt"))
        self.assertEqual(scopes[0].get("knowledge_base_ids"), ["kb-test"])
        self.assertNotIn("knowledge_ids", scopes[0])
        self.assertEqual(scopes[1].get("knowledge_ids"), ["document-test"])
        self.assertNotIn("knowledge_base_ids", scopes[1])

        good = json.dumps({"success": True, "data": [
            {"knowledge_id": "document-test"}]}).encode()
        empty = json.dumps({"success": True, "data": []}).encode()
        with mock.patch.object(PROBE, "http", side_effect=[(200, good), (200, empty)]):
            with self.assertRaises(PROBE.ProbeError):
                PROBE.search_probe("https://localhost:443", fixture_data(), "jwt")


if __name__ == "__main__":
    unittest.main()
