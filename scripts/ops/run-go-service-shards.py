#!/usr/bin/env python3
"""Run the exact selected service tests in bounded, sequential Go processes."""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess


VERIFIER_PATH = Path(__file__).with_name("verify-go-test-evidence.py")
SPEC = importlib.util.spec_from_file_location("go_test_evidence", VERIFIER_PATH)
EVIDENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVIDENCE)


class ShardError(ValueError):
    pass


def selected_tests(package, selector, tags):
    result = subprocess.run(
        ["go", "test", "-tags", tags, "-p", "1", package, "-list", selector],
        stdout=subprocess.PIPE, text=True, check=False,
    )
    if result.returncode:
        raise ShardError(f"Go test discovery failed (exit {result.returncode})")
    # -list also prints benchmarks and the package result. The original
    # -run invocation executes tests, examples and fuzz seed corpora only.
    names = [line for line in result.stdout.splitlines()
             if re.fullmatch(r"(?:Test|Example|Fuzz)\w*", line)]
    if not names or len(names) != len(set(names)):
        raise ShardError("Go test discovery was empty or contained duplicates")
    return sorted(names)


def run_shards(package, selector, tags, shards, output_prefix,
               other_evidence, required_tests=()):
    if shards < 1:
        raise ShardError("shard count must be positive")
    names = selected_tests(package, selector, tags)
    selected = set(names)
    # Retain the existing cross-package requirements. Each service name is
    # required by its own shard; all remaining names stay with the other log.
    other_required = [name for name in required_tests if name not in selected]
    other_result = EVIDENCE.verify(other_evidence, other_required)
    buckets = [names[index::shards] for index in range(min(shards, len(names)))]
    plan = {
        "package": package, "selector": selector, "tags": tags,
        "timeout": "20m", "selected_tests": names, "shards": buckets,
    }
    Path(str(output_prefix) + "-plan.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n")
    results = []
    for number, bucket in enumerate(buckets, 1):
        path = Path(f"{output_prefix}-{number}.jsonl")
        # Exact top-level names preserve every subtest of each selected case.
        # No test is run twice, omitted, or replaced with a substring match.
        exact = "^(" + "|".join(re.escape(name) for name in bucket) + ")$"
        with path.open("w") as output:
            result = subprocess.run(
                ["go", "test", "-json", "-tags", tags, "-p", "1", package,
                 "-run", exact, "-count=1", "-timeout", "20m"],
                stdout=output, check=False,
            )
        if result.returncode:
            raise ShardError(f"service shard {number} failed (exit {result.returncode}); see {path}")
        results.append(EVIDENCE.verify(path, bucket))
    return {"selected_tests": len(names), "service_shards": results,
            "other_packages": other_result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", default="./internal/application/service")
    parser.add_argument("--selector", required=True)
    parser.add_argument("--tags", default="sqlite_fts5")
    parser.add_argument("--shards", type=int, default=4)
    parser.add_argument("--output-prefix", type=Path, default=Path("candidate-tests-service"))
    parser.add_argument("--other-evidence", type=Path, required=True)
    parser.add_argument("--require-test", action="append", default=[])
    args = parser.parse_args()
    try:
        result = run_shards(args.package, args.selector, args.tags, args.shards,
                            args.output_prefix, args.other_evidence, args.require_test)
    except (ShardError, EVIDENCE.EvidenceError, OSError) as exc:
        parser.exit(1, f"Go service shards rejected: {exc}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
