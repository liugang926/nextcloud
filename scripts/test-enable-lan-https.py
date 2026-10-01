#!/usr/bin/env python3
"""Offline safety checks for the opt-in LAN HTTPS operator helper."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "enable_lan_https", Path(__file__).with_name("enable-lan-https.py")
)
gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway)


class GatewaySafetyTests(unittest.TestCase):
    def test_recreation_removes_only_managed_old_ips(self):
        existing = ["172.20.0.10", "172.20.0.21", "10.0.0.9"]
        result = gateway.desired_proxies(
            existing, "172.20.0.30", ("172.20.0.10", "172.20.0.21")
        )
        self.assertEqual(result, ["10.0.0.9", "172.20.0.30"])
        self.assertEqual(gateway.desired_proxies(result, "172.20.0.30", ("172.20.0.30",)), result)
        self.assertEqual(
            gateway.desired_proxies(["172.20.0.30", "10.0.0.9"], "172.20.0.30", ()),
            ["172.20.0.30", "10.0.0.9"],
        )

    def test_unexpected_proxy_format_fails_closed(self):
        with self.assertRaises(gateway.GatewayError):
            gateway.desired_proxies({"0": "10.0.0.9"}, "172.20.0.30", ())
        with self.assertRaises(gateway.GatewayError):
            gateway.desired_proxies(["10.0.0.9", 5], "172.20.0.30", ())

    def test_compose_rejects_wildcard_https_bind(self):
        config = {
            "name": "nextcloud-weknora-dev",
            "services": {
                "nextcloud": {"ports": [{"host_ip": "10.1.2.3", "target": 80}]},
                "nextcloud-https": {
                    "ports": [{"host_ip": "0.0.0.0", "target": 443,
                               "published": "18482", "protocol": "tcp"}],
                    "volumes": [],
                },
            },
        }
        with self.assertRaises(gateway.GatewayError):
            gateway.gateway_config(config)

    def test_gateway_network_must_be_unambiguous(self):
        networks = {"one": {"IPAddress": "172.20.0.2"},
                    "two": {"IPAddress": "172.21.0.2"}}
        with patch.object(gateway, "command", return_value=json.dumps(networks)):
            with self.assertRaises(gateway.GatewayError):
                gateway.container_ip("a" * 64)

    def test_state_write_and_read_is_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "gateway.json"
            with patch.object(gateway, "STATE_FILE", state):
                gateway.write_state("172.20.0.3")
                self.assertEqual(gateway.read_state(), "172.20.0.3")
                self.assertEqual(state.stat().st_mode & 0o777, 0o600)

    def test_redirect_to_http_fails_https_probe(self):
        with self.assertRaises(gateway.GatewayError):
            gateway.validate_login_origin(
                "https://10.1.2.3:18482",
                ("200", "http://10.1.2.3:18482/login", "https://10.1.2.3:18482"),
            )

    def test_wrong_scheme_in_html_fails_https_probe(self):
        with self.assertRaises(gateway.GatewayError):
            gateway.validate_login_origin(
                "https://10.1.2.3:18482",
                ("200", "https://10.1.2.3:18482/login",
                 '<meta property="og:url" content="http://10.1.2.3:18482/login">'
                 '<link href="https://10.1.2.3:18482/core.css">'),
            )

    def test_reads_exact_keys_without_redacted_config_list(self):
        values = {
            "trusted_domains": ["localhost", "10.1.2.3"],
            "trusted_proxies": ["172.20.0.5"],
            "overwrite.cli.url": "http://10.1.2.3:18082",
        }

        def fake_process(args, label, *, env=None):
            index = args.index("config:system:get")
            name = args[index + 1]
            self.assertIn("--output=json", args)
            if name == "trusted_proxies":
                self.assertTrue(args[-1].startswith("--default-value="))
            else:
                self.assertEqual(args[-1], "--output=json")
            return subprocess.CompletedProcess(args, 0, json.dumps(values[name]), "")

        with patch.object(gateway, "process", side_effect=fake_process):
            self.assertEqual(gateway.system_config(["docker", "compose"], {}), values)

    def test_missing_proxy_value_is_empty_but_other_failure_is_not(self):
        def absent_process(args, label, *, env=None):
            marker = args[-1].split("=", 1)[1]
            return subprocess.CompletedProcess(args, 0, json.dumps(marker), "")

        with patch.object(gateway, "process", side_effect=absent_process):
            self.assertEqual(gateway.occ_get([], {}, "trusted_proxies", allow_missing=True), [])
        with patch.object(gateway, "process", return_value=subprocess.CompletedProcess(
            [], 1, "", ""
        )):
            with self.assertRaises(gateway.GatewayError):
                gateway.occ_get([], {}, "trusted_proxies", allow_missing=True)
        with patch.object(gateway, "process", return_value=subprocess.CompletedProcess(
            [], 1, "", "database unavailable"
        )):
            with self.assertRaises(gateway.GatewayError):
                gateway.occ_get([], {}, "trusted_proxies", allow_missing=True)


if __name__ == "__main__":
    unittest.main()
