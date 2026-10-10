#!/usr/bin/env python3
"""Contracts for retaining the same synthetic JWTs across revocation polls."""

import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest import mock


PATH = Path(__file__).with_name("synthetic-ldap-revocation-watch.py")
SPEC = importlib.util.spec_from_file_location("synthetic_ldap_revocation_watch", PATH)
WATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WATCH)


def expected(allow_a):
    allowed = {"nextcloud_login": True, "ldap_login": True, "dav": True,
               "source": True, "knowledge": True, "search": True}
    denied = {"nextcloud_login": True, "ldap_login": True, "dav": False,
              "source": False, "knowledge": False, "search": False}
    return {"a": allowed if allow_a else denied, "b": denied}


class RevocationWatchTest(unittest.TestCase):
    def test_target_requires_prechange_jwts_and_ignores_failed_poll(self):
        data = {"accounts": {"a": {"weknora_identifier": "alice"},
                             "b": {"weknora_identifier": "bob"}}}
        before, after = expected(True), expected(False)
        calls = []

        def observed(*args):
            calls.append(args[-1])
            if len(calls) == 1:
                return WATCH.expected_reads(before)
            if len(calls) == 2:
                raise WATCH.PROBE.ProbeError("HTTP 503")
            return WATCH.expected_reads(after)

        with mock.patch.object(WATCH.PROBE, "weknora_login", side_effect=["old-A", "old-B"]), \
                mock.patch.object(WATCH, "observe", side_effect=observed), \
                mock.patch.object(WATCH.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            report = WATCH.watch(data, before, after, "http://127.0.0.1:1",
                                 "http://127.0.0.1:2", {"a": "x", "b": "y"},
                                 ("key", "secret"), timeout_seconds=5,
                                 interval_seconds=0.5)
        self.assertEqual(report["transient_probe_errors"], 1)
        self.assertEqual(report["checks"], WATCH.expected_reads(after))
        self.assertEqual(calls, [{"a": "old-A", "b": "old-B"}] * 3)


if __name__ == "__main__":
    unittest.main()
