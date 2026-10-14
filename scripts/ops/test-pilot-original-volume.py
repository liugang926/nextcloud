#!/usr/bin/env python3
"""Offline original pilot safety tests, including a real loopback streaming PUT."""

import copy
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import tracemalloc
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("pilot_original", Path(__file__).with_name("pilot-original-volume.py"))
PILOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PILOT)


class OriginalVolumePilotTests(unittest.TestCase):
    def state(self):
        return {"project": "pilot-original-012345abcdef", "owner_token": "a" * 32,
                "port": 18998, "app_path": "/private/owned/app", "directory": "/private/owned/state",
                "image_ids": {service: "sha256:" + "b" * 64 for service in PILOT.SERVICES}}

    def resources(self, state):
        project = state["project"]
        result = {"container": {project + "-" + service + "-1" for service in PILOT.SERVICES},
                  "volume": {project + "_" + volume for volume in PILOT.VOLUMES},
                  "network": {project + "_default"}}
        inspected = {}
        for service in PILOT.SERVICES:
            name = project + "-" + service + "-1"
            inspected[("container", name)] = {
                "Id": service, "Image": state["image_ids"][service], "State": {"Running": True},
                "Config": {"Labels": {PILOT.OWNER_LABEL: state["owner_token"],
                           "com.docker.compose.project": project, "com.docker.compose.service": service}},
                "NetworkSettings": {"Networks": {project + "_default": {}}, "Ports": {
                    "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(state["port"])}]} if service == "nextcloud" else {}},
                "Mounts": [{"Type": "volume", "Name": project + "_nextcloud-html"}],
            }
        for volume in PILOT.VOLUMES:
            name = project + "_" + volume
            inspected[("volume", name)] = {"Labels": {PILOT.OWNER_LABEL: state["owner_token"],
                "com.docker.compose.project": project, "com.docker.compose.volume": volume}}
        inspected[("network", project + "_default")] = {
            "Labels": {PILOT.OWNER_LABEL: state["owner_token"], "com.docker.compose.project": project,
                       "com.docker.compose.network": "default"},
            "Containers": {service: {} for service in PILOT.SERVICES},
        }
        return result, inspected

    def test_full_preset_is_exact_decimal_100gb(self):
        args = PILOT.parse_args(["run", "--state-dir", "/private/new", "--pilot-10k-100gb"])
        self.assertEqual((args.file_count, args.file_bytes), (10_000, 10_000_000))
        self.assertEqual(args.file_count * args.file_bytes, 100_000_000_000)
        self.assertEqual(PILOT.parse_args(["run", "--state-dir", "/private/new"]).file_count, 20)

    def test_synthetic_identity_flags_are_scoped_to_fresh_nextcloud(self):
        config = PILOT.compose_data(self.state(), {"database": "private-db", "admin": "private-admin"})
        nc = config["services"]["nextcloud"]["environment"]
        self.assertEqual([nc[key] for key in ("WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY", "WEKNORA_LOCAL_COMPOSE",
                                             "WEKNORA_EVENT_DEV_HTTP")], ["1", "1", "1"])
        self.assertEqual(set(config["services"]), {"nextcloud", "db", "redis"})
        self.assertEqual(config["services"]["nextcloud"]["ports"][0]["host_ip"], "127.0.0.1")

    def test_large_and_uneven_custom_loads_require_valid_opt_in(self):
        for flags in (["--file-count", "101"], ["--file-count", "102"],
                      ["--file-bytes", "10000000"], ["--workers", "5"],
                      ["--pilot-10k-100gb", "--file-count", "100"]):
            with self.subTest(flags=flags), mock.patch("sys.stderr"), self.assertRaises(SystemExit):
                PILOT.parse_args(["run", "--state-dir", "/private/new", *flags])

    def test_streaming_generation_is_deterministic_and_bounded(self):
        def consume(index, count):
            digest, size, maximum = hashlib.sha256(), 0, 0
            for chunk in PILOT.payload_chunks(index, count):
                digest.update(chunk)
                size += len(chunk)
                maximum = max(maximum, len(chunk))
            return digest.hexdigest(), size, maximum
        tracemalloc.start()
        a = consume(17, 10_000_000)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(a, consume(17, 10_000_000))
        self.assertNotEqual(a[0], consume(18, 10_000_000)[0])
        self.assertEqual(a[1:], (10_000_000, 64 * 1024))
        self.assertLess(peak, 1024 * 1024)

    def test_real_loopback_put_uses_exact_content_length_and_digest(self):
        received = {}
        class Handler(BaseHTTPRequestHandler):
            def do_PUT(self):
                received["path"] = self.path
                received["transfer_encoding"] = self.headers.get("Transfer-Encoding")
                count = int(self.headers["Content-Length"])
                body = self.rfile.read(count)
                received["bytes"] = len(body)
                received["digest"] = hashlib.sha256(body).hexdigest()
                self.send_response(201)
                self.send_header("Content-Length", "0")
                self.end_headers()
            def log_message(self, *_):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        try:
            result = PILOT.upload(server.server_port, "/synthetic.bin", {}, 2, 131_073)
        finally:
            thread.join(timeout=5)
            server.server_close()
        self.assertEqual(received["bytes"], 131_073)
        self.assertEqual(received["digest"], result["sha256"])
        self.assertIsNone(received["transfer_encoding"])
        self.assertEqual(result["http_status"], 201)

    def test_capacity_estimate_has_no_zero_headroom_path(self):
        self.assertEqual(PILOT.required_free_bytes(100_000_000_000), 120_000_000_000 + 8 * 1024 ** 3)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(PILOT.shutil, "disk_usage") as disk:
            disk.return_value.free = PILOT.required_free_bytes(4096) - 1
            with self.assertRaisesRegex(RuntimeError, "capacity"):
                PILOT.host_capacity(Path(directory), 4096)
            disk.return_value.free += 1
            self.assertEqual(PILOT.host_capacity(Path(directory), 4096)["required_free_bytes"], disk.return_value.free)

    def test_login_and_file_id_ignore_inherited_http_proxy(self):
        class Handler(BaseHTTPRequestHandler):
            def reply(self, status, content):
                self.send_response(status)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            def do_GET(self):
                self.reply(200, b'<html data-requesttoken="synthetic-csrf"></html>')
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.reply(200, b"synthetic login accepted")
            def do_PROPFIND(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.reply(207, b'<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><oc:fileid>17</oc:fileid></d:multistatus>')
            def log_message(self, *_):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with mock.patch.dict(PILOT.os.environ, {"HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9",
                                                   "NO_PROXY": "", "no_proxy": ""}):
                base = f"http://127.0.0.1:{server.server_port}"
                _, csrf = PILOT.local_login(base, "synthetic", "private-synthetic-password")
                self.assertEqual(csrf, "synthetic-csrf")
                self.assertEqual(PILOT.local_file_id(base + "/synthetic", {}), 17)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_failed_upload_comparison_restores_app_and_deletes_owned_folder(self):
        state = self.state()
        state.update({"upload_impact_samples": 20, "file_count": 20, "file_bytes": 1024, "workers": 2})
        with mock.patch.object(PILOT, "host_capacity"), mock.patch.object(PILOT, "vm_capacity"), \
             mock.patch.object(PILOT, "request", side_effect=[(201, b""), (204, b"")]), \
             mock.patch.object(PILOT, "upload", side_effect=RuntimeError("failed synthetic upload")), \
             mock.patch.object(PILOT, "occ") as occ, self.assertRaisesRegex(RuntimeError, "failed synthetic"):
            PILOT.measure_upload_impact(state, {"admin": "private"})
        calls = [row.args[1:] for row in occ.call_args_list]
        self.assertEqual(calls, [("app:disable", "integration_weknora"),
                                 ("app:enable", "integration_weknora"), ("trashbin:cleanup", "pilotadmin")])

    def check_resources(self, state, result, inspected):
        with mock.patch.object(PILOT, "docker_names", side_effect=lambda kind, project=None: result[kind]), \
             mock.patch.object(PILOT, "inspect", side_effect=lambda kind, name: inspected[(kind, name)]):
            return PILOT.assert_owned_resources(state)

    def test_owner_labels_image_and_loopback_mounts_are_validated(self):
        state = self.state()
        resources, inspected = self.resources(state)
        self.assertEqual(self.check_resources(state, resources, inspected), resources)
        mutations = [
            lambda data: data[("volume", state["project"] + "_postgres-data")]["Labels"].update({PILOT.OWNER_LABEL: "foreign"}),
            lambda data: data[("container", state["project"] + "-nextcloud-1")]["NetworkSettings"]["Ports"]["80/tcp"][0].update({"HostIp": "0.0.0.0"}),
            lambda data: data[("container", state["project"] + "-nextcloud-1")].update({"Image": "sha256:" + "c" * 64}),
            lambda data: data[("container", state["project"] + "-nextcloud-1")]["Mounts"][0].update({"Name": "shared-nextcloud-html"}),
            lambda data: data[("network", state["project"] + "_default")]["Containers"].update({"shared-app": {}}),
        ]
        for mutate in mutations:
            data = copy.deepcopy(inspected)
            mutate(data)
            with self.subTest(mutate=mutate), self.assertRaises(RuntimeError):
                self.check_resources(state, resources, data)

    def test_unlabeled_name_collision_rejected_before_create(self):
        state = self.state()
        def names(kind, project=None):
            return {state["project"] + "_postgres-data"} if kind == "volume" and project is None else set()
        with mock.patch.object(PILOT, "docker_names", side_effect=names), self.assertRaisesRegex(RuntimeError, "unlabeled"):
            PILOT.assert_owned_resources(state, empty=True)

    def test_cleanup_owner_failure_never_runs_removal(self):
        with mock.patch.object(PILOT, "assert_owned_resources", side_effect=RuntimeError("foreign")), \
             mock.patch.object(PILOT, "command") as command, self.assertRaises(RuntimeError):
            PILOT.cleanup(self.state())
        command.assert_not_called()

    def test_docker_desktop_prefix_accepts_only_exact_owned_path(self):
        with mock.patch.object(PILOT.sys, "platform", "darwin"):
            self.assertTrue(PILOT.bind_source_matches("/host_mnt/Users/test/app", "/Users/test/app"))
            self.assertFalse(PILOT.bind_source_matches("/host_mnt/Users/test/app-other", "/Users/test/app"))
        with mock.patch.object(PILOT.sys, "platform", "linux"):
            self.assertFalse(PILOT.bind_source_matches("/host_mnt/Users/test/app", "/Users/test/app"))

    def test_comparison_capacity_includes_both_phases_and_corpus(self):
        state = self.state()
        state.update({"upload_impact_samples": 20, "file_count": 20, "file_bytes": 1024})
        with mock.patch.object(PILOT, "host_capacity", side_effect=RuntimeError("capacity")) as capacity, \
             mock.patch.object(PILOT, "occ") as occ, self.assertRaises(RuntimeError):
            PILOT.measure_upload_impact(state, {})
        self.assertEqual(capacity.call_args.args[1], 60 * 1024)
        occ.assert_not_called()

    def test_cleanup_removes_exact_inventory_and_private_files(self):
        state = self.state()
        resources, _ = self.resources(state)
        with tempfile.TemporaryDirectory() as directory:
            state["directory"] = directory
            for name in ("credentials.json", "compose.json", "report.json"):
                (Path(directory) / name).write_text("owned")
            with mock.patch.object(PILOT, "assert_owned_resources", side_effect=[resources, {}]), \
                 mock.patch.object(PILOT, "command") as command:
                PILOT.cleanup(state)
            calls = [row.args for row in command.call_args_list]
            self.assertEqual(len(calls), 7)
            self.assertTrue(all("prune" not in row and "image" not in row for row in calls))
            self.assertFalse((Path(directory) / "credentials.json").exists())
            self.assertFalse((Path(directory) / "compose.json").exists())
            self.assertTrue((Path(directory) / "report.json").exists())

    def test_private_state_and_compose_digest_cannot_be_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            path.chmod(0o700)
            state = self.state()
            state.update({"marker": PILOT.MARKER, "directory": str(path.resolve())})
            config = {"services": {}}
            state["compose_sha256"] = PILOT.fingerprint(config)
            PILOT.private_json(path / "state.json", state)
            PILOT.private_json(path / "compose.json", config)
            self.assertEqual((path / "state.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(PILOT.load_state(path)["project"], state["project"])
            (path / "compose.json").write_text(json.dumps({"services": {"foreign": {}}}))
            with self.assertRaisesRegex(ValueError, "changed"):
                PILOT.load_state(path)

    def test_generated_password_cannot_be_misread_as_console_option(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(PILOT, "host_capacity", return_value={}), \
             mock.patch.object(PILOT, "inspect", return_value={"Id": "sha256:" + "b" * 64}), \
             mock.patch.object(PILOT, "assert_owned_resources", return_value={}), \
             mock.patch.object(PILOT, "command", return_value="f0e5516"), \
             mock.patch.object(PILOT.secrets, "token_urlsafe", return_value="-synthetic-option-like-password"):
            path = Path(directory) / "new-private-pilot"
            PILOT.prepare(path, 20, 1024, 2, False)
            passwords = json.loads((path / "credentials.json").read_text())
            self.assertTrue(all(value.startswith("x-") for value in passwords.values()))

    def test_interrupted_run_still_records_failure_and_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.state()
            state.update({"directory": directory, "source_revision": "f0e5516", "file_count": 20,
                          "file_bytes": 1024, "full_preset": False, "host_capacity_before": {}})
            with mock.patch.object(PILOT, "assert_owned_resources", return_value={}), \
                 mock.patch.object(PILOT, "compose", side_effect=KeyboardInterrupt), \
                 mock.patch.object(PILOT, "cleanup", return_value={"owned_containers_remaining": 0}) as cleanup, \
                 mock.patch("builtins.print"), self.assertRaises(KeyboardInterrupt):
                PILOT.execute(state, retain=False)
            cleanup.assert_called_once_with(state)
            report = json.loads((Path(directory) / "report.json").read_text())
            self.assertEqual(report["result"], "failed")
            self.assertEqual(report["failure_category"], "KeyboardInterrupt")
            self.assertEqual(report["cleanup"]["owned_containers_remaining"], 0)


if __name__ == "__main__":
    unittest.main()
