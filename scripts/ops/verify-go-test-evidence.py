#!/usr/bin/env python3
"""Reject incomplete, skipped, or empty selected Go test evidence."""
import argparse
import json
from pathlib import Path


class EvidenceError(ValueError):
    pass


def verify(path, required_tests=(), compile_only=False):
    packages = {}
    tests = {}
    counts = {"pass": 0, "fail": 0, "skip": 0}
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        try:
            event = json.loads(line)
        except (ValueError, TypeError) as exc:
            raise EvidenceError(f"invalid JSON at line {number}") from exc
        if not isinstance(event, dict):
            raise EvidenceError(f"invalid event at line {number}")
        action, package, test = (event.get(key) for key in ("Action", "Package", "Test"))
        if not isinstance(package, str) or not package or not isinstance(action, str):
            raise EvidenceError(f"missing package/action at line {number}")
        if test is not None:
            if not isinstance(test, str) or not test:
                raise EvidenceError(f"invalid test at line {number}")
            key = (package, test)
            if action == "run":
                if key in tests:
                    raise EvidenceError("duplicate test execution; use -count=1")
                tests[key] = None
            elif action in counts:
                if key not in tests or tests[key] is not None:
                    raise EvidenceError("test terminal without exactly one run")
                tests[key] = action
                counts[action] += 1
        elif action == "start":
            if package in packages:
                raise EvidenceError("duplicate package start")
            packages[package] = None
        elif action in counts:
            if package not in packages or packages[package] is not None:
                raise EvidenceError("package terminal without exactly one start")
            packages[package] = action

    if not packages or any(result is None for result in packages.values()):
        raise EvidenceError("empty or unfinished package evidence")
    if "fail" in packages.values() or counts["fail"]:
        raise EvidenceError("failed package or test")
    if any(result is None for result in tests.values()):
        raise EvidenceError("unfinished test evidence")
    if counts["skip"]:
        raise EvidenceError("selected test was skipped")
    # Go marks a package with no test files as skip. It is not a skipped test.
    if any(result == "skip" and any(key[0] == package for key in tests)
           for package, result in packages.items()):
        raise EvidenceError("package skipped after starting selected tests")
    if compile_only:
        if tests or required_tests:
            raise EvidenceError("compile-only evidence must contain no test executions")
    elif not tests:
        raise EvidenceError("selector executed no tests")
    for required in required_tests:
        if not any(test == required and result == "pass"
                   for (_, test), result in tests.items()):
            raise EvidenceError(f"required test did not pass: {required}")
    return {"packages": len(packages), "tests": counts, "compile_only": compile_only}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--require-test", action="append", default=[])
    parser.add_argument("--compile-only", action="store_true")
    args = parser.parse_args()
    try:
        result = verify(args.path, args.require_test, args.compile_only)
    except (EvidenceError, OSError) as exc:
        parser.exit(1, f"Go evidence rejected: {exc}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
