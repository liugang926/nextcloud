#!/usr/bin/env python3
"""Hold synthetic users' JWTs while an operator revokes a private LDAP grant.

Start after baseline indexing. The script verifies both users' initial reads,
prints a readiness timestamp, then polls until the target denial matrix holds
with those *same* JWTs. Change only the disposable LDAP fixture externally.
"""

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys
import time


MODULE_PATH = Path(__file__).with_name("ad-permission-acceptance.py")
SPEC = importlib.util.spec_from_file_location("ad_permission_acceptance", MODULE_PATH)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def observe(data, nc_origin, wk_origin, passwords, machine, tokens):
    observed = {}
    for label in ("a", "b"):
        account = data["accounts"][label]
        login, dav = PROBE.dav_probe(nc_origin, account, passwords[label], data["file_id"])
        result = {
            "nextcloud_login": login,
            "dav": dav,
            "source": PROBE.source_probe(nc_origin, data, account, *machine),
            "knowledge": PROBE.knowledge_probe(wk_origin, data["knowledge_id"], tokens[label]),
            "direct_content": PROBE.direct_content_probe(
                wk_origin, data["knowledge_id"], tokens[label]),
            "search": PROBE.search_probe(wk_origin, data, tokens[label]),
        }
        observed[label] = result
    return observed


def expected_reads(expectations):
    return {label: {"nextcloud_login": row["nextcloud_login"],
                    "dav": row["dav"], "source": row["source"],
                    "knowledge": row["knowledge"], "direct_content": row["knowledge"],
                    "search": row["search"]}
            for label, row in expectations.items()}


def watch(data, initial, target, nc_origin, wk_origin, passwords, machine,
          *, timeout_seconds, interval_seconds):
    tokens = {}
    for label in ("a", "b"):
        account = data["accounts"][label]
        token = PROBE.weknora_login(wk_origin, account["weknora_identifier"], passwords[label])
        if token is None:
            raise PROBE.ProbeError("baseline synthetic LDAP login failed for account " + label)
        tokens[label] = token
    baseline = observe(data, nc_origin, wk_origin, passwords, machine, tokens)
    if baseline != expected_reads(initial):
        raise PROBE.ProbeError("baseline read matrix did not match; no revocation watch started")
    ready_at = timestamp()
    print(json.dumps({"phase": "baseline_ready", "checked_at_utc": ready_at},
                     separators=(",", ":")), flush=True)
    deadline = time.monotonic() + timeout_seconds
    errors = 0
    polls = 0
    expected = expected_reads(target)
    while time.monotonic() < deadline:
        try:
            current = observe(data, nc_origin, wk_origin, passwords, machine, tokens)
            polls += 1
            if current == expected:
                report = {"phase": "target_observed", "checked_at_utc": timestamp(),
                          "baseline_ready_at_utc": ready_at, "polls": polls,
                          "transient_probe_errors": errors, "checks": current}
                print(json.dumps(report, separators=(",", ":")))
                return report
        except PROBE.ProbeError:
            # A 503 or transport error is never interpreted as denial. It
            # counts as a failed poll and can only end in timeout/failure.
            errors += 1
        time.sleep(interval_seconds)
    raise PROBE.ProbeError(
        f"target denial matrix did not converge within {timeout_seconds}s "
        f"({polls} complete polls, {errors} failed polls)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--target-case", default="group_removed")
    parser.add_argument("--nextcloud-origin", required=True)
    parser.add_argument("--weknora-origin", required=True)
    parser.add_argument("--allow-loopback-http", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if not 1 <= args.timeout_seconds <= 600 or not 0.5 <= args.interval_seconds <= 10:
        raise PROBE.ProbeError("watch timeout/interval is outside the synthetic test bounds")
    data, initial = PROBE.fixture(args.fixture, "baseline")
    _, target = PROBE.fixture(args.fixture, args.target_case)
    for label in ("a", "b"):
        if not initial[label]["ldap_login"] or not initial[label]["nextcloud_login"]:
            raise PROBE.ProbeError("baseline requires both accounts to log in")
        if not target[label]["ldap_login"] or not target[label]["nextcloud_login"]:
            raise PROBE.ProbeError("watch covers content revocation, not account disablement")
    if initial == target:
        raise PROBE.ProbeError("target matrix must differ from baseline")
    nc_origin = PROBE.origin(args.nextcloud_origin, allow_remote_https=False,
                             allow_loopback_http=args.allow_loopback_http)
    wk_origin = PROBE.origin(args.weknora_origin, allow_remote_https=False,
                             allow_loopback_http=args.allow_loopback_http)
    passwords, machine = PROBE.required_environment()
    watch(data, initial, target, nc_origin, wk_origin, passwords, machine,
          timeout_seconds=args.timeout_seconds, interval_seconds=args.interval_seconds)


if __name__ == "__main__":
    try:
        main()
    except (PROBE.ProbeError, OSError, ValueError, json.JSONDecodeError) as error:
        print("Synthetic revocation watch failed: " + str(error), file=sys.stderr)
        sys.exit(1)
