#!/usr/bin/env python3
"""Offline checks for the pilot harness safety boundary and statistics."""

import importlib.util
from pathlib import Path
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

    def test_large_fixture_needs_explicit_opt_in(self):
        with self.assertRaises(SystemExit):
            self.parse("--file-count", "101")
        args = self.parse("--pilot-10k-100gb")
        self.assertEqual(args.file_count * args.file_bytes, 100_000_000_000)
        with self.assertRaises(SystemExit):
            self.parse("--pilot-10k-100gb", "--file-count", "12")

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
