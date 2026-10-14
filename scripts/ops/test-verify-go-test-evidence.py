#!/usr/bin/env python3
"""Regression checks for Go evidence selection and incomplete terminal logs."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "go_evidence", Path(__file__).with_name("verify-go-test-evidence.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def event(action, package="example/service", test=None):
    result = {"Action": action, "Package": package}
    if test is not None:
        result["Test"] = test
    return result


class EvidenceTests(unittest.TestCase):
    def verify(self, events, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tests.jsonl"
            path.write_text("".join(json.dumps(item) + "\n" for item in events))
            return module.verify(path, **kwargs)

    def passing(self):
        return [event("start"), event("run", test="TestActualHTTP"),
                event("output", test="TestActualHTTP"),
                event("pass", test="TestActualHTTP"), event("pass")]

    def test_selected_actual_test_and_no_test_package(self):
        events = self.passing() + [event("start", "example/types"),
                                  event("skip", "example/types")]
        result = self.verify(events, required_tests=["TestActualHTTP"])
        self.assertEqual(result["tests"], {"pass": 1, "fail": 0, "skip": 0})
        self.assertEqual(result["packages"], 2)

    def test_selector_miss_is_not_compile_success(self):
        with self.assertRaises(module.EvidenceError):
            self.verify([event("start"), event("pass")])

    def test_missing_required_test(self):
        with self.assertRaises(module.EvidenceError):
            self.verify(self.passing(), required_tests=["TestNewFollowUp"])

    def test_skipped_database_test_rejected(self):
        with self.assertRaises(module.EvidenceError):
            self.verify([event("start"), event("run", test="TestPostgres"),
                         event("skip", test="TestPostgres"), event("pass")])

    def test_truncated_package_or_test_rejected(self):
        for events in (self.passing()[:-1],
                       [event("start"), event("run", test="TestActualHTTP"), event("pass")]):
            with self.subTest(events=events), self.assertRaises(module.EvidenceError):
                self.verify(events)

    def test_failed_package_rejected_even_with_passing_tests(self):
        with self.assertRaises(module.EvidenceError):
            self.verify(self.passing()[:-1] + [event("fail")])

    def test_failed_test_rejected_even_with_passing_package(self):
        with self.assertRaises(module.EvidenceError):
            self.verify([event("start"), event("run", test="TestActualHTTP"),
                         event("fail", test="TestActualHTTP"), event("pass")])

    def test_duplicate_retry_cannot_hide_previous_execution(self):
        with self.assertRaises(module.EvidenceError):
            self.verify(self.passing()[:-1] + [event("run", test="TestActualHTTP"),
                                              event("pass", test="TestActualHTTP"), event("pass")])

    def test_orphan_terminal_rejected(self):
        with self.assertRaises(module.EvidenceError):
            self.verify([event("start"), event("pass", test="TestActualHTTP"), event("pass")])

    def test_compile_only_has_no_executed_tests(self):
        self.assertTrue(self.verify([event("start"), event("pass")], compile_only=True)["compile_only"])
        with self.assertRaises(module.EvidenceError):
            self.verify(self.passing(), compile_only=True)

    def test_empty_or_invalid_evidence_rejected(self):
        with self.assertRaises(module.EvidenceError):
            self.verify([])
        with self.assertRaises(module.EvidenceError):
            self.verify([{"Action": "pass"}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tests.jsonl"
            path.write_text('{"Action":')
            with self.assertRaises(module.EvidenceError):
                module.verify(path)


if __name__ == "__main__":
    unittest.main()
