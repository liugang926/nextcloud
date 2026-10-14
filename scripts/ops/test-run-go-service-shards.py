#!/usr/bin/env python3
"""Contract tests for complete bounded service-test sharding."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "service_shards", Path(__file__).with_name("run-go-service-shards.py"))
SHARDS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SHARDS)


def events(package, names, terminal="pass"):
    rows = [{"Action": "start", "Package": package}]
    for name in names:
        for test in (name, name + "/actual-boundary"):
            rows += [{"Action": "run", "Package": package, "Test": test},
                     {"Action": terminal, "Package": package, "Test": test}]
    rows.append({"Action": "pass", "Package": package})
    return "".join(json.dumps(row) + "\n" for row in rows)


class ServiceShardContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.prefix = Path(self.directory.name) / "service"
        self.other = Path(self.directory.name) / "other.jsonl"
        self.other.write_text(events("other", ["TestOtherRequired"]))
        self.names = ["TestD", "ExampleB", "FuzzC", "TestA"]
        self.executed = []

    def go(self, args, **kwargs):
        if "-list" in args:
            return subprocess.CompletedProcess(args, 0,
                "\n".join(self.names + ["BenchmarkIgnored", "ok service"]) + "\n", "")
        self.assertEqual(args[args.index("-timeout") + 1], "20m")
        self.assertEqual(args[args.index("-p") + 1], "1")
        self.assertIn("-count=1", args)
        self.assertEqual(args[args.index("-tags") + 1], "sqlite_fts5")
        exact = args[args.index("-run") + 1]
        self.assertTrue(exact.startswith("^(") and exact.endswith(")$"))
        names = exact[2:-2].split("|")
        self.executed += names
        kwargs["stdout"].write(events("service", names))
        return subprocess.CompletedProcess(args, 0)

    def run_shards(self, **overrides):
        args = dict(package="./internal/application/service", selector="Original|Normal",
                    tags="sqlite_fts5", shards=3, output_prefix=self.prefix,
                    other_evidence=self.other,
                    required_tests=["TestOtherRequired", "TestA"])
        args.update(overrides)
        return SHARDS.run_shards(**args)

    @mock.patch.object(SHARDS.subprocess, "run")
    def test_exact_union_once_with_all_subtests_and_existing_requirements(self, run):
        run.side_effect = self.go
        result = self.run_shards()
        self.assertCountEqual(self.executed, self.names)
        self.assertEqual(len(self.executed), len(set(self.executed)))
        self.assertEqual(run.call_args_list[0].args[0][-1], "Original|Normal")
        self.assertEqual(result["selected_tests"], 4)
        self.assertEqual(sum(item["tests"]["pass"] for item in result["service_shards"]), 8)
        plan = json.loads(Path(str(self.prefix) + "-plan.json").read_text())
        self.assertEqual(plan["shards"], [["ExampleB", "TestD"], ["FuzzC"], ["TestA"]])

    @mock.patch.object(SHARDS.subprocess, "run")
    def test_go_failure_propagates_with_retained_log(self, run):
        def fail(args, **kwargs):
            if "-list" in args:
                return self.go(args, **kwargs)
            kwargs["stdout"].write("retained failing output\n")
            return subprocess.CompletedProcess(args, 1)
        run.side_effect = fail
        with self.assertRaisesRegex(SHARDS.ShardError, "shard 1 failed"):
            self.run_shards()
        self.assertEqual(Path(str(self.prefix) + "-1.jsonl").read_text(), "retained failing output\n")

    @mock.patch.object(SHARDS.subprocess, "run")
    def test_successful_go_cannot_omit_selected_test(self, run):
        def omit(args, **kwargs):
            if "-list" in args:
                return self.go(args, **kwargs)
            kwargs["stdout"].write(events("service", ["TestUnrelated"]))
            return subprocess.CompletedProcess(args, 0)
        run.side_effect = omit
        with self.assertRaisesRegex(SHARDS.EVIDENCE.EvidenceError, "required test did not pass"):
            self.run_shards()

    @mock.patch.object(SHARDS.subprocess, "run")
    def test_successful_go_cannot_skip_selected_test(self, run):
        def skip(args, **kwargs):
            if "-list" in args:
                return self.go(args, **kwargs)
            name = args[args.index("-run") + 1][2:-2].split("|")[0]
            kwargs["stdout"].write(events("service", [name], "skip"))
            return subprocess.CompletedProcess(args, 0)
        run.side_effect = skip
        with self.assertRaisesRegex(SHARDS.EVIDENCE.EvidenceError, "skipped"):
            self.run_shards()

    @mock.patch.object(SHARDS.subprocess, "run")
    def test_missing_existing_requirement_is_rejected(self, run):
        run.side_effect = self.go
        with self.assertRaisesRegex(SHARDS.EVIDENCE.EvidenceError, "TestMissing"):
            self.run_shards(required_tests=["TestMissing"])
        self.assertFalse(self.executed)

    @mock.patch.object(SHARDS.subprocess, "run")
    def test_empty_or_duplicate_discovery_is_rejected(self, run):
        run.side_effect = self.go
        for names in ([], ["TestA", "TestA"]):
            self.names = names
            with self.assertRaises(SHARDS.ShardError):
                self.run_shards()
        self.assertFalse(self.executed)


if __name__ == "__main__":
    unittest.main()
