#!/usr/bin/env python3
"""Offline checks for the shared cold-checkpoint guard and command order."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).with_name("shared-cold-checkpoint.py")
SPEC = importlib.util.spec_from_file_location("shared_cold_checkpoint", SOURCE)
checkpoint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checkpoint)


class CheckpointTests(unittest.TestCase):
    def test_single_lan_stopped_placeholder_is_exact(self):
        placeholder = {("443/tcp", "127.0.0.1", "")}
        self.assertTrue(checkpoint.known_stopped_port_loss(
            {("443/tcp", "10.0.0.2", "18482")}, placeholder))
        for host in ("127.0.0.1", "0.0.0.0", "8.8.8.8", "::1", "localhost"):
            self.assertFalse(checkpoint.known_stopped_port_loss(
                {("443/tcp", host, "18482")}, placeholder))
        for port in ("", "0", "65536", "named"):
            self.assertFalse(checkpoint.known_stopped_port_loss(
                {("443/tcp", "10.0.0.2", port)}, placeholder))
        self.assertFalse(checkpoint.known_stopped_port_loss(
            {("443/tcp", "10.0.0.2", "18482")}, {("443/tcp", "127.0.0.1", "18483")}))
        self.assertFalse(checkpoint.known_stopped_port_loss(
            {("443/tcp", "10.0.0.2", "18482")}, {("80/tcp", "127.0.0.1", "")}))

    def stopped_port_fixture(self):
        config = {"volumes": {}, "networks": {"default": {"name": "nc_default"}}}
        service = {"environment": {"KEY": "value"}, "volumes": [],
                   "ports": [{"target": 80, "host_ip": "127.0.0.1", "published": "18082"},
                             {"target": 80, "host_ip": "10.0.0.2", "published": "18082"}],
                   "networks": {"default": None}}
        item = {"Id": "container", "Image": "sha256:" + "a" * 64,
                "Mounts": [], "State": {"Running": False},
                "Config": {"Env": ["KEY=value"],
                           "Labels": {"com.docker.compose.config-hash": "b" * 64}},
                "HostConfig": {"PortBindings": {"80/tcp": [
                    {"HostIp": "127.0.0.1", "HostPort": "18082"},
                    {"HostIp": "127.0.0.1", "HostPort": ""}]}}}
        return item, service, config

    def test_stopped_multi_address_port_loss_is_narrow(self):
        item, service, config = self.stopped_port_fixture()
        self.assertFalse(checkpoint.container_runtime_matches(item, service, config))
        self.assertTrue(checkpoint.container_runtime_matches(
            item, service, config, allow_stopped_port_loss=True))
        for running in (True, None):
            changed = json.loads(json.dumps(item))
            changed["State"]["Running"] = running
            self.assertFalse(checkpoint.container_runtime_matches(
                changed, service, config, allow_stopped_port_loss=True))
        for key, value in (("HostPort", "18083"), ("HostIp", "0.0.0.0"),
                           ("HostIp", "::1")):
            changed = json.loads(json.dumps(item))
            changed["HostConfig"]["PortBindings"]["80/tcp"][1][key] = value
            self.assertFalse(checkpoint.container_runtime_matches(
                changed, service, config, allow_stopped_port_loss=True))
        changed = json.loads(json.dumps(item))
        changed["Config"]["Env"] = ["KEY=changed"]
        self.assertFalse(checkpoint.container_runtime_matches(
            changed, service, config, allow_stopped_port_loss=True))
        changed = json.loads(json.dumps(item))
        changed["Mounts"] = [{"Type": "bind", "Source": "/tmp/foreign",
                              "Destination": "/foreign", "RW": True}]
        self.assertFalse(checkpoint.container_runtime_matches(
            changed, service, config, allow_stopped_port_loss=True))
        for target in ("81/tcp", "80/udp"):
            changed = json.loads(json.dumps(item))
            changed["HostConfig"]["PortBindings"][target] = changed["HostConfig"]["PortBindings"].pop("80/tcp")
            self.assertFalse(checkpoint.container_runtime_matches(
                changed, service, config, allow_stopped_port_loss=True))
        changed = json.loads(json.dumps(item))
        changed["HostConfig"]["PortBindings"]["80/tcp"] = changed["HostConfig"]["PortBindings"]["80/tcp"][:1]
        self.assertFalse(checkpoint.container_runtime_matches(
            changed, service, config, allow_stopped_port_loss=True))
        one_port = json.loads(json.dumps(service))
        one_port["ports"] = one_port["ports"][:1]
        self.assertFalse(checkpoint.container_runtime_matches(
            item, one_port, config, allow_stopped_port_loss=True))

    def test_stopped_port_loss_still_requires_original_identity_and_compose_hash(self):
        item, service, config = self.stopped_port_fixture()
        config["services"] = {"nextcloud": service}
        stack = {"project": "nc", "config": config,
                 "containers": {"nextcloud": {"id": item["Id"], "image_id": item["Image"],
                                              "compose_config_hash": "b" * 64}}}
        with mock.patch.object(checkpoint, "inspect", return_value=item):
            checkpoint.ensure_state(stack, Path("/tmp"), stopped={"nextcloud"})
        for field, value in (("Id", "replacement"), ("Image", "sha256:" + "c" * 64)):
            changed = json.loads(json.dumps(item)); changed[field] = value
            with mock.patch.object(checkpoint, "inspect", return_value=changed):
                with self.assertRaises(checkpoint.CheckpointError):
                    checkpoint.ensure_state(stack, Path("/tmp"), stopped={"nextcloud"})
        changed = json.loads(json.dumps(item))
        changed["Config"]["Labels"]["com.docker.compose.config-hash"] = "c" * 64
        with mock.patch.object(checkpoint, "inspect", return_value=changed):
            with self.assertRaisesRegex(checkpoint.CheckpointError, "Compose hash changed"):
                checkpoint.ensure_state(stack, Path("/tmp"), stopped={"nextcloud"})

    def test_running_checkout_mount_must_match_compose_exactly(self):
        source = "/Users/example/nextcloud/apps/integration_weknora"
        config = {"volumes": {"nextcloud-html": {"name": "nc_nextcloud-html"}},
                  "networks": {"default": {"name": "nc_default"}}}
        service = {
            "volumes": [
                {"type": "volume", "source": "nextcloud-html", "target": "/var/www/html"},
                {"type": "bind", "source": source,
                 "target": "/var/www/html/custom_apps/integration_weknora",
                 "read_only": True}],
            "ports": [{"target": 80, "protocol": "tcp", "host_ip": "127.0.0.1",
                       "published": "18082"}],
            "environment": {"KEY": "value"}, "networks": {"default": None},
        }
        item = {"Mounts": [
            {"Type": "volume", "Name": "nc_nextcloud-html",
             "Destination": "/var/www/html", "RW": True},
            {"Type": "bind", "Source": "/host_mnt" + source,
             "Destination": "/var/www/html/custom_apps/integration_weknora",
             "RW": False}],
            "HostConfig": {"PortBindings": {"80/tcp": [{"HostIp": "127.0.0.1",
                                                     "HostPort": "18082"}]}},
            "Config": {"Env": ["KEY=value"]},
            "State": {"Running": True},
            "NetworkSettings": {"Networks": {"nc_default": {}}},
        }
        self.assertTrue(checkpoint.container_runtime_matches(item, service, config))
        changed = json.loads(json.dumps(item))
        changed["Mounts"][1]["Source"] = "/host_mnt/private/tmp/isolated/app"
        self.assertFalse(checkpoint.container_runtime_matches(changed, service, config))
        changed = json.loads(json.dumps(item))
        changed["Mounts"][1]["RW"] = True
        self.assertFalse(checkpoint.container_runtime_matches(changed, service, config))
        changed = json.loads(json.dumps(item))
        changed["Mounts"].append({"Type": "bind", "Source": "/tmp/extra",
                                   "Destination": "/extra", "RW": True})
        self.assertFalse(checkpoint.container_runtime_matches(changed, service, config))
        changed = json.loads(json.dumps(item))
        changed["NetworkSettings"]["Networks"]["foreign"] = {}
        self.assertFalse(checkpoint.container_runtime_matches(changed, service, config))

    def test_validate_topology_requires_all_services_and_app_bind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nc = root / "nextcloud"
            wk = root / "weknora-ldap-local"
            app = nc / "apps/integration_weknora"
            app.mkdir(parents=True)
            wk.mkdir()
            services = {name: {"image": "example:fixed", "volumes": []}
                        for name in checkpoint.NC_SERVICES}
            services["redis"]["command"] = ["redis-server", "--appendonly", "yes"]
            for name in ("nextcloud", "cron", "event-worker", "event-status-worker"):
                services[name]["volumes"] = [{
                    "type": "bind", "source": str(app),
                    "target": "/var/www/html/custom_apps/integration_weknora"}]
            config = {
                "name": checkpoint.NC_PROJECT,
                "services": services,
                "volumes": {role: {"name": f"{checkpoint.NC_PROJECT}_{role}"}
                            for role in checkpoint.NC_VOLUMES},
            }
            checkpoint.validate_config(config, checkpoint.NC_PROJECT, nc, wk)
            config["services"]["db"]["ports"] = [{"target": 5432, "published": "15432"}]
            with self.assertRaisesRegex(checkpoint.CheckpointError, "publish a host port"):
                checkpoint.validate_config(config, checkpoint.NC_PROJECT, nc, wk)
            del config["services"]["db"]["ports"]
            del config["services"]["cron"]
            with self.assertRaisesRegex(checkpoint.CheckpointError, "topology"):
                checkpoint.validate_config(config, checkpoint.NC_PROJECT, nc, wk)
            config["services"]["cron"] = {"image": "example:fixed", "volumes": []}
            with self.assertRaisesRegex(checkpoint.CheckpointError, "app code bind"):
                checkpoint.validate_config(config, checkpoint.NC_PROJECT, nc, wk)

    def test_private_file_is_exclusive_and_mode_600(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret"
            checkpoint.private_file(path, data=b"local secret")
            self.assertEqual(path.read_bytes(), b"local secret")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                checkpoint.private_file(path, data=b"overwrite")

    def test_tar_verifier_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.tar"
            with tarfile.open(path, "w") as archive:
                info = tarfile.TarInfo("../escape")
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
            with self.assertRaisesRegex(checkpoint.CheckpointError, "unsafe path"):
                checkpoint.verify_tar(path)

    def test_database_client_fence_rejects_nonzero_connections(self):
        with mock.patch.object(checkpoint, "run", return_value=b"0\n"):
            checkpoint.database_has_no_other_clients("db", "user", "db", Path("/tmp"))
        with mock.patch.object(checkpoint, "run", return_value=b"1\n"):
            with self.assertRaisesRegex(checkpoint.CheckpointError, "external client"):
                checkpoint.database_has_no_other_clients("db", "user", "db", Path("/tmp"))

    def test_bind_fingerprint_detects_content_change(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            source.write_text("before")
            first = checkpoint.source_fingerprint({"runtime/config": source})
            source.write_text("after")
            self.assertNotEqual(first, checkpoint.source_fingerprint({"runtime/config": source}))

    def test_evidence_cannot_enter_bind_mount_or_tracked_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nc = root / "nextcloud"
            wk = root / "weknora-ldap-local"
            app = nc / "apps/integration_weknora"
            with self.assertRaisesRegex(checkpoint.CheckpointError, "runtime input"):
                checkpoint.safe_evidence_location(app / "backup", nc, wk, {})
            with self.assertRaisesRegex(checkpoint.CheckpointError, "dist/backups"):
                checkpoint.safe_evidence_location(nc / "docs/backup", nc, wk, {})
            checkpoint.safe_evidence_location(nc / "dist/backups/new", nc, wk, {})

    def test_foreign_container_on_pair_network_is_rejected(self):
        stacks = self.fake_stacks()
        for stack in stacks:
            stack["config"]["networks"] = {
                "default": {"name": stack["project"] + "_default"}}
        outsider = {"Mounts": [], "NetworkSettings": {
            "Networks": {checkpoint.NC_PROJECT + "_default": {}}}}
        with mock.patch.object(checkpoint, "run", return_value=b"foreign\n"), \
             mock.patch.object(checkpoint, "inspect", return_value=outsider):
            with self.assertRaisesRegex(checkpoint.CheckpointError, "network"):
                checkpoint.reject_foreign_volume_users(stacks, Path("/tmp"))

    def fake_stacks(self):
        stacks = []
        for project, services, volumes, cwd in (
                (checkpoint.NC_PROJECT, checkpoint.NC_SERVICES,
                 checkpoint.NC_VOLUMES, Path("/tmp/nc")),
                (checkpoint.WK_PROJECT, checkpoint.WK_SERVICES,
                 checkpoint.WK_VOLUMES, Path("/tmp/wk"))):
            stacks.append({
                "project": project, "cwd": cwd,
                "command": ["docker", "compose", "-p", project],
                "config_bytes": b"{}", "config": {"services": {}},
                "containers": {s: {"id": project + "-" + s,
                                   "image_id": "sha256:" + "a" * 64,
                                   "image_tag": "example"} for s in services},
                "volumes": {role: project + "_" + role for role in volumes},
            })
        return stacks

    def test_checkpoint_order_and_complete_manifest_without_docker(self):
        stacks = self.fake_stacks()
        calls = []

        def fake_run(args, **kwargs):
            calls.append(("run", args))
            return b"{}" if args[-3:] == ["config", "--format", "json"] else b""

        def fake_private_file(path, **kwargs):
            calls.append(("file", path.name, kwargs.get("command")))
            path.write_bytes(kwargs.get("data") or b"fixture")
            path.chmod(0o600)

        def fake_host_archive(path, sources):
            calls.append(("host", path.name))
            path.write_bytes(b"fixture")
            path.chmod(0o600)

        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(checkpoint, "run", side_effect=fake_run), \
             mock.patch.object(checkpoint, "private_file", side_effect=fake_private_file), \
             mock.patch.object(checkpoint, "host_archive", side_effect=fake_host_archive), \
             mock.patch.object(checkpoint, "verify_tar"), \
             mock.patch.object(checkpoint, "estimate_volume_bytes", return_value=1024), \
             mock.patch.object(checkpoint, "free_bytes", return_value=10 * 1024 ** 3), \
             mock.patch.object(checkpoint, "ensure_state"), \
             mock.patch.object(checkpoint, "database_has_no_other_clients"), \
             mock.patch.object(checkpoint, "git_identity", return_value="b" * 40), \
             mock.patch.object(checkpoint, "reject_foreign_volume_users"), \
             mock.patch.object(checkpoint, "input_paths", return_value={}), \
             mock.patch.object(checkpoint, "source_fingerprint", return_value="stable"):
            evidence = Path(directory)
            result = checkpoint.checkpoint(stacks, Path("/tmp/nc"), Path("/tmp/wk"),
                                           evidence, "b" * 40)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertTrue((evidence / "manifest.json").is_file())
            self.assertFalse((evidence / "INCOMPLETE").exists())

        writer_stops = [i for i, call in enumerate(calls)
                        if call[0] == "run" and "stop" in call[1] and
                        "postgres" not in call[1] and "db" not in call[1]]
        dump_indices = [i for i, call in enumerate(calls)
                        if call[0] == "file" and call[2] and "pg_dump" in call[2]]
        db_stops = [i for i, call in enumerate(calls)
                    if call[0] == "run" and "stop" in call[1] and
                    ("postgres" in call[1] or "db" in call[1])]
        volume_indices = [i for i, call in enumerate(calls)
                          if call[0] == "file" and call[1].endswith(".tar")]
        self.assertEqual(len(writer_stops), 2)
        self.assertEqual(len(db_stops), 2)
        self.assertEqual(len(dump_indices), 2)
        self.assertEqual(len(volume_indices), 8)
        self.assertLess(max(writer_stops), min(dump_indices))
        self.assertLess(max(dump_indices), min(db_stops))
        self.assertLess(max(db_stops), min(volume_indices))
        self.assertFalse(any(call[0] == "run" and "up" in call[1] for call in calls))

    def test_failure_keeps_incomplete_and_never_starts_services(self):
        stacks = self.fake_stacks()
        calls = []

        def failed_run(args, **kwargs):
            calls.append(args)
            if "pg_dump" in args:
                raise checkpoint.CheckpointError("synthetic dump failure")
            return b"{}" if args[-3:] == ["config", "--format", "json"] else b""

        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(checkpoint, "run", side_effect=failed_run), \
             mock.patch.object(checkpoint, "estimate_volume_bytes", return_value=0), \
             mock.patch.object(checkpoint, "free_bytes", return_value=10 * 1024 ** 3), \
             mock.patch.object(checkpoint, "ensure_state"), \
             mock.patch.object(checkpoint, "database_has_no_other_clients"), \
             mock.patch.object(checkpoint, "reject_foreign_volume_users"), \
             mock.patch.object(checkpoint, "input_paths", return_value={}), \
             mock.patch.object(checkpoint, "source_fingerprint", return_value="stable"), \
             mock.patch.object(checkpoint, "git_identity", return_value="b" * 40):
            evidence = Path(directory)
            with self.assertRaisesRegex(checkpoint.CheckpointError, "dump failure"):
                checkpoint.checkpoint(stacks, Path("/tmp/nc"), Path("/tmp/wk"),
                                      evidence, "b" * 40)
            self.assertTrue((evidence / "INCOMPLETE").is_file())
            self.assertTrue((evidence / "failure.json").is_file())
            self.assertFalse((evidence / "manifest.json").exists())
        self.assertFalse(any("up" in command or "start" in command for command in calls))


if __name__ == "__main__":
    unittest.main()
