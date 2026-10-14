#!/usr/bin/env python3
"""Offline fail-closed checks for rotate-lan-ip.py."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "rotate_lan_ip", Path(__file__).with_name("rotate-lan-ip.py")
)
rotation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rotation)


class RotationSafetyTests(unittest.TestCase):
    OLD = "10.106.105.128"
    NEW = "10.106.105.121"

    def test_weknora_commands_use_the_running_three_file_stack(self):
        self.assertEqual(rotation.wk_compose(), [
            "docker", "compose", "--project-directory", str(rotation.WEKNORA),
            "-f", str(rotation.WEKNORA / "compose.yaml"),
            "-f", str(rotation.PROJECT / "integration/weknora.override.yaml"),
            "-f", str(rotation.PROJECT / "integration/weknora-rag-local.override.yaml"),
        ])

    def test_only_rfc1918_host_addresses(self):
        for value in ("10.106.105.121", "172.16.1.2", "192.168.1.20"):
            self.assertEqual(rotation.private_host_ipv4(value), value)
        for value in ("0.0.0.0", "10.0.0.0", "127.0.0.1", "169.254.2.3",
                      "100.64.1.2", "224.0.0.1", "240.0.0.1", "::1", "not-an-ip"):
            with self.subTest(value=value), self.assertRaises(rotation.RotationError):
                rotation.private_host_ipv4(value)

    def test_nextcloud_env_requires_old_host_exact_overlay_and_loopback(self):
        text = ("NEXTCLOUD_DB_PASSWORD=do-not-change\n"
                "NEXTCLOUD_LAN_HOST=10.106.105.128\n"
                "NEXTCLOUD_HTTP_BIND_IP=127.0.0.1\n"
                "COMPOSE_FILE=compose.yaml:integration/nextcloud.lan.yaml\n")
        self.assertEqual(rotation.validate_nc_env(text, self.OLD), (18082, 18482))
        for changed in (text.replace(self.OLD, "10.106.105.100"),
                        text.replace("NEXTCLOUD_HTTP_BIND_IP=127.0.0.1",
                                     "NEXTCLOUD_HTTP_BIND_IP=0.0.0.0"),
                        text.replace("compose.yaml:integration/nextcloud.lan.yaml",
                                     "compose.yaml"),
                        text + "NEXTCLOUD_LAN_HOST=10.106.105.128\n"):
            with self.assertRaises(rotation.RotationError):
                rotation.validate_nc_env(changed, self.OLD)

    def test_weknora_transform_changes_only_three_expected_values(self):
        before = ("    APP_EXTERNAL_URL: https://10.106.105.128:18443\n"
                  "    FRONTEND_BASE_URL: https://10.106.105.128:18443\n"
                  "    OTHER_SECRET: do-not-change\n"
                  "      - \"127.0.0.1:18080:80\"\n"
                  "      - \"10.106.105.128:18443:443\"\n")
        after = rotation.rewrite_weknora_compose(before, self.OLD, self.NEW)
        self.assertIn("OTHER_SECRET: do-not-change", after)
        self.assertIn('"127.0.0.1:18080:80"', after)
        self.assertEqual(after.count(self.NEW), 3)
        self.assertNotIn(self.OLD, after)
        with self.assertRaises(rotation.RotationError):
            rotation.rewrite_weknora_compose(after, self.OLD, self.NEW)
        with self.assertRaises(rotation.RotationError):
            rotation.rewrite_weknora_compose(before.replace("127.0.0.1:18080:80",
                                                           "0.0.0.0:18080:80"),
                                             "10.106.105.100", self.NEW)

    def test_nginx_and_san_require_unique_old_values(self):
        nginx = "    server_name 10.106.105.128;\n"
        self.assertEqual(rotation.rewrite_nginx(nginx, self.OLD, self.NEW),
                         "    server_name 10.106.105.121;\n")
        with self.assertRaises(rotation.RotationError):
            rotation.rewrite_nginx(nginx + nginx, self.OLD, self.NEW)
        san = "subjectAltName = IP:10.106.105.128,IP:10.106.105.224\n"
        self.assertEqual(rotation.rewrite_san_config(san, self.OLD, self.NEW),
                         "subjectAltName = IP:10.106.105.121,IP:10.106.105.224\n")
        already_new = "subjectAltName = IP:10.106.105.121,IP:10.106.105.128\n"
        self.assertEqual(rotation.rewrite_san_config(already_new, self.OLD, self.NEW),
                         "subjectAltName = IP:10.106.105.121\n")
        with self.assertRaises(rotation.RotationError):
            rotation.rewrite_san_config("subjectAltName = DNS:example.test\n",
                                        self.OLD, self.NEW)

    def test_compose_rejects_extra_or_public_listeners_and_wrong_cert(self):
        with tempfile.TemporaryDirectory() as directory:
            wk = Path(directory) / "weknora"
            nc_cert = wk / "certs/weknora-lan-signed.crt"
            nc_key = wk / "certs/weknora-lan.key"

            def port(host, published, target):
                return {"host_ip": host, "published": str(published),
                        "target": target, "protocol": "tcp"}

            def mount(target, source):
                return {"type": "bind", "target": target, "source": str(source),
                        "read_only": True}

            nc = {"name": "nextcloud-weknora-dev", "services": {
                "nextcloud": {"ports": [port("127.0.0.1", 18082, 80),
                                         port(self.OLD, 18082, 80)]},
                "nextcloud-https": {
                    "ports": [port(self.OLD, 18482, 443)],
                    "volumes": [mount("/etc/nginx/tls/server.crt", nc_cert),
                                mount("/etc/nginx/tls/server.key", nc_key)]}}}
            weknora = {"name": "weknora-ldap-local", "services": {
                "app": {"ports": [port("127.0.0.1", 18081, 8080)],
                        "environment": {"APP_EXTERNAL_URL": f"https://{self.OLD}:18443",
                                        "FRONTEND_BASE_URL": f"https://{self.OLD}:18443"}},
                "frontend": {"ports": [port("127.0.0.1", 18080, 80)]},
                "lan-gateway": {"ports": [port(self.OLD, 18443, 443)],
                                "volumes": [mount("/certs/weknora-lan.crt", nc_cert),
                                            mount("/certs/weknora-lan.key", nc_key)]}}}
            with patch.object(rotation, "WEKNORA", wk), patch.object(
                rotation, "load_json", side_effect=[nc, weknora]
            ):
                rotation.validate_compose(self.OLD, 18082, 18482, {})
            nc["services"]["nextcloud"]["ports"].append(port("0.0.0.0", 18082, 80))
            with patch.object(rotation, "WEKNORA", wk), patch.object(
                rotation, "load_json", side_effect=[nc, weknora]
            ), self.assertRaises(rotation.RotationError):
                rotation.validate_compose(self.OLD, 18082, 18482, {})
            nc["services"]["nextcloud"]["ports"].pop()
            weknora["services"]["lan-gateway"]["volumes"][0]["read_only"] = False
            with patch.object(rotation, "WEKNORA", wk), patch.object(
                rotation, "load_json", side_effect=[nc, weknora]
            ), self.assertRaises(rotation.RotationError):
                rotation.validate_compose(self.OLD, 18082, 18482, {})

    def test_backup_is_private_complete_and_immutable_from_original(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            secret = project / ".env"
            secret.write_text("NEXTCLOUD_DB_PASSWORD=private\n")
            os.chmod(secret, 0o600)
            config = project / "compose.yaml"
            config.write_text("name: example\n")
            files = {"nextcloud.env": secret, "weknora.compose.yaml": config}
            with patch.object(rotation, "PROJECT", project):
                backup = rotation.backup_files(files, self.OLD, self.NEW,
                                               {"trusted_domains": [self.OLD]})
            self.assertEqual(json.loads((backup / "manifest.json").read_text())["old_ip"],
                             self.OLD)
            for file in backup.iterdir():
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
            secret.write_text("changed")
            self.assertEqual((backup / "nextcloud.env").read_text(),
                             "NEXTCLOUD_DB_PASSWORD=private\n")

    def test_file_change_after_preflight_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compose.yaml"
            path.write_text("old")
            expected = rotation.sha256(path)
            path.write_text("another operator changed this")
            with self.assertRaises(rotation.RotationError):
                rotation.atomic_replace(path, b"new", expected_hash=expected)
            self.assertEqual(path.read_text(), "another operator changed this")

    def test_missing_trusted_proxies_is_not_a_failed_occ_read(self):
        with patch.object(rotation.secrets, "token_hex", return_value="x"), patch.object(
            rotation, "run", return_value=json.dumps("__lan_rotation_missing_x__")
        ):
            self.assertEqual(rotation.nc_occ_get("trusted_proxies", {},
                                                 allow_missing=True), [])

    def test_remote_docker_daemon_is_rejected(self):
        with self.assertRaises(rotation.RotationError):
            rotation.validate_local_docker({"DOCKER_HOST": "ssh://other-host"})
        with patch.object(rotation, "load_json", return_value=[
            {"Endpoints": {"docker": {"Host": "tcp://example.test:2376"}}}
        ]), self.assertRaises(rotation.RotationError):
            rotation.validate_local_docker({})
        with patch.object(rotation, "load_json", return_value=[
            {"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}
        ]):
            rotation.validate_local_docker({"DOCKER_HOST": "unix:///var/run/docker.sock"})

    def test_weknora_app_drift_blocks_automatic_recreation(self):
        config = {"services": {"app": {"image": "example/app:local"}}}
        image_id = "sha256:" + "a" * 64
        container_id = "b" * 64
        config_hash = "c" * 64
        results = [container_id, image_id, image_id, f"app {config_hash}",
                   "d" * 64]
        with patch.object(rotation, "load_json", return_value=config), patch.object(
            rotation, "run", side_effect=results
        ), self.assertRaises(rotation.RotationError):
            rotation.ensure_running_image_matches({})

    def test_weknora_frontend_drift_blocks_automatic_recreation(self):
        config = {"services": {"app": {"image": "example/app:local"},
                               "frontend": {"image": "example/frontend:local"}}}
        image_id = "sha256:" + "a" * 64
        config_hash = "c" * 64
        results = ["b" * 64, image_id, image_id, f"app {config_hash}", config_hash,
                   "d" * 64, image_id, image_id, f"frontend {config_hash}", "e" * 64]
        with patch.object(rotation, "load_json", return_value=config), patch.object(
            rotation, "run", side_effect=results
        ) as command, self.assertRaisesRegex(rotation.RotationError, "frontend config"):
            rotation.ensure_running_image_matches({})
        self.assertIn([*rotation.wk_compose(), "config", "--hash", "frontend"],
                      [call.args[0] for call in command.call_args_list])

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
    def test_candidate_cert_uses_existing_ca_and_server_key(self):
        with tempfile.TemporaryDirectory() as directory:
            wk = Path(directory)
            certs = wk / "certs"
            certs.mkdir()

            def openssl(*args):
                subprocess.run(["openssl", *args], cwd=wk, check=True,
                               capture_output=True, text=True)

            openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(certs / "weknora-lan-ca.key"),
                    "-out", str(certs / "weknora-lan-ca.crt"),
                    "-subj", "/CN=Test LAN CA", "-days", "2",
                    "-addext", "basicConstraints=critical,CA:TRUE")
            openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                    "-out", str(certs / "weknora-lan.key"))
            output = wk / "candidate"
            output.mkdir()
            config = ("[server_cert]\n"
                      "basicConstraints = critical,CA:FALSE\n"
                      "keyUsage = critical,digitalSignature,keyEncipherment\n"
                      "extendedKeyUsage = serverAuth\n"
                      f"subjectAltName = IP:{self.NEW}\n")
            with patch.object(rotation, "WEKNORA", wk):
                rotation.sign_candidate(config, self.NEW, output / "server.crt", {})
                self.assertIn(("IP Address", self.NEW),
                              rotation.certificate_sans(output / "server.crt"))
            openssl("verify", "-purpose", "sslserver", "-CAfile",
                    str(certs / "weknora-lan-ca.crt"), str(output / "server.crt"))


if __name__ == "__main__":
    unittest.main()
