#!/usr/bin/env python3
"""Offline archive tamper, Docker ownership and recovery ordering checks."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).with_name("shared-cold-restore-plan.py")
SPEC = importlib.util.spec_from_file_location("shared_cold_restore_plan", SOURCE)
restore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restore)
capture = restore.capture


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.nc = self.root / "nextcloud"
        self.wk = self.root / "weknora-ldap-local"
        self.nc.mkdir()
        self.wk.mkdir()
        self.app = self.nc / "apps/integration_weknora"
        self.app.mkdir(parents=True)
        (self.app / "app.php").write_text("<?php // captured code\n")
        git(self.nc, "init", "-q")
        git(self.nc, "add", "apps")
        git(self.nc, "-c", "user.name=Restore fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "captured app")
        self.commit = git(self.nc, "rev-parse", "HEAD").decode().strip()
        self.evidence = self.root / "checkpoint"
        self.evidence.mkdir(mode=0o700)
        self.configs = {}
        self.projects = {}
        counter = 0
        for project in restore.PROJECTS:
            services = {name: {"image": "fixture:old", "volumes": [],
                               "environment": {"FIXTURE": "private"}, "networks": {"default": None}}
                        for name in restore.SERVICES[project]}
            services["redis"]["command"] = ["redis-server", "--appendonly", "yes"]
            if project == capture.NC_PROJECT:
                for name in ("nextcloud", "cron", "event-worker", "event-status-worker"):
                    services[name]["volumes"] = [{
                        "type": "bind", "source": str(self.app),
                        "target": "/var/www/html/custom_apps/integration_weknora", "read_only": True}]
            else:
                services["app"]["volumes"] = [{"type": "volume", "source": "app-data",
                                                "target": "/data/files"}]
            config = {"name": project, "services": services,
                      "volumes": {role: {"name": f"{project}_{role}"} for role in restore.VOLUMES[project]},
                      "networks": {"default": {"name": f"{project}_default"}}}
            self.configs[project] = config
            containers = {}
            for service in sorted(restore.SERVICES[project]):
                counter += 1
                containers[service] = {"id": f"{counter:064x}", "image_id": "sha256:" + "a" * 64,
                                       "image_tag": "fixture:old", "running_port_bindings": {},
                                       "compose_config_hash": hashlib.sha256(json.dumps(services[service], sort_keys=True).encode()).hexdigest()}
            config_file = self.evidence / f"{restore.SHORT[project]}-resolved-compose.json"
            self.write_private(config_file, json.dumps(config).encode())
            self.projects[project] = {"containers": containers,
                                      "volumes": {role: f"{project}_{role}" for role in restore.VOLUMES[project]},
                                      "resolved_compose_sha256": capture.sha256(config_file)}
            for role in restore.VOLUMES[project]:
                self.tar(self.evidence / f"{restore.SHORT[project]}-{role}.tar",
                         [(".", "dir", ""), ("./payload", "file", b"synthetic volume bytes")])
            self.write_private(self.evidence / f"{restore.SHORT[project]}.dump", b"PGDMP synthetic dump")
            self.write_private(self.evidence / f"{restore.SHORT[project]}-globals.sql", b"-- synthetic globals\n")
        input_paths = [self.nc / ".env", self.nc / "compose.yaml",
                       self.nc / "integration/nextcloud.lan.yaml", self.nc / "integration/nextcloud.https.yaml",
                       self.nc / "integration/weknora.override.yaml", self.nc / "integration/weknora-rag-local.override.yaml",
                       self.wk / ".env", self.wk / "compose.yaml", self.wk / "rag-model.env"]
        for source in input_paths:
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("synthetic private input\n")
        mapping = restore.required_inputs(self.configs, self.nc, self.wk, set())
        capture.host_archive(self.evidence / "runtime-inputs.tar", mapping)
        capture.host_archive(self.evidence / "nextcloud-app-code.tar", {"apps/integration_weknora": self.app})
        self.manifest = {"status": "COMPLETE", "scope": "shared_local_cold_pair", "created_at_unix": 1,
                         "nextcloud_git_commit": self.commit, "all_services_stopped_at_completion": True,
                         "restore_or_runtime_switch_performed": False, "projects": self.projects,
                         "artifact_sha256": {name: capture.sha256(self.evidence / name)
                                             for name in restore.expected_artifacts()}}
        self.manifest["nextcloud_app_code_sha256"] = self.manifest["artifact_sha256"]["nextcloud-app-code.tar"]
        self.save_manifest()
        self.checked = {"configs": self.configs, "manifest": self.manifest}
        self.objects = self.target_objects()
        self.calls = []

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def write_private(path, data):
        path.write_bytes(data)
        path.chmod(0o600)

    def save_manifest(self):
        self.write_private(self.evidence / "manifest.json", json.dumps(self.manifest).encode())

    @staticmethod
    def tar(path, entries):
        with tarfile.open(path, "w") as archive:
            for name, kind, payload in entries:
                entry = tarfile.TarInfo(name)
                entry.mode = 0o755 if kind == "dir" else 0o644
                if kind == "dir":
                    entry.type = tarfile.DIRTYPE
                elif kind in {"sym", "hard"}:
                    entry.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
                    entry.linkname = payload
                elif kind == "fifo":
                    entry.type = tarfile.FIFOTYPE
                else:
                    entry.size = len(payload)
                archive.addfile(entry, io.BytesIO(payload) if kind == "file" else None)
        path.chmod(0o600)

    def verify(self, **kwargs):
        return restore.verify_checkpoint(self.evidence, self.nc, self.wk, **kwargs)

    def target_objects(self):
        result = {}
        for project, config in self.configs.items():
            for service, definition in config["services"].items():
                identity = self.projects[project]["containers"][service]
                item = {"Id": identity["id"], "Image": identity["image_id"],
                        "Config": {"Image": definition["image"], "Labels": {
                            "com.docker.compose.project": project, "com.docker.compose.service": service,
                            "com.docker.compose.container-number": "1", "com.docker.compose.oneoff": "False",
                            "com.docker.compose.config-hash": identity["compose_config_hash"]},
                            "Env": [key + "=" + value for key, value in definition["environment"].items()]},
                        "State": {"Running": False, "Status": "exited"}, "Mounts": [],
                        "HostConfig": {"PortBindings": {}}, "NetworkSettings": {"Networks": {}}}
                for mount in definition["volumes"]:
                    item["Mounts"].append({"Type": mount["type"], "Destination": mount["target"],
                                           "RW": not mount.get("read_only", False),
                                           "Source": mount["source"],
                                           "Name": config["volumes"][mount["source"]]["name"]
                                           if mount["type"] == "volume" else None})
                result[("container", f"{project}-{service}-1")] = item
                result[("container", identity["id"])] = item
            for role in restore.VOLUMES[project]:
                name = f"{project}_{role}"
                result[("volume", name)] = {"Name": name, "Driver": "local", "Options": None,
                                            "Labels": {"com.docker.compose.project": project,
                                                       "com.docker.compose.volume": role}}
            result[("network", f"{project}_default")] = {"Labels": {
                "com.docker.compose.project": project, "com.docker.compose.network": "default"}}
        result[("image", "sha256:" + "a" * 64)] = {"Id": "sha256:" + "a" * 64, "Os": "linux"}
        return result

    def fake_run(self, args, **kwargs):
        self.calls.append(args)
        if args[:3] == ["docker", "context", "inspect"]:
            return json.dumps([{"Endpoints": {"docker": {"Host": "unix:///synthetic.sock"}}}]).encode()
        if args[-3:] == ["config", "--format", "json"]:
            project = capture.NC_PROJECT if "--env-file" in args else capture.WK_PROJECT
            return json.dumps(self.configs[project]).encode()
        if args[-3:] == ["config", "--hash", "*"]:
            project = capture.NC_PROJECT if "--env-file" in args else capture.WK_PROJECT
            return "\n".join(service + " " + hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
                             for service, definition in self.configs[project]["services"].items()).encode()
        if args[:3] == ["docker", "ps", "-aq"]:
            ids = {obj["Id"] for (kind, name), obj in self.objects.items() if kind == "container"}
            for argument in args:
                if argument.startswith("label=com.docker.compose.project="):
                    project = argument.split("=", 2)[-1]
                    ids = {obj["Id"] for (kind, name), obj in self.objects.items() if kind == "container" and
                           obj["Config"]["Labels"].get("com.docker.compose.project") == project}
            return ("\n".join(sorted(ids)) + "\n").encode()
        if args[:4] == ["docker", "volume", "ls", "-q"]:
            project = args[-1].split("=", 2)[-1]
            return "\n".join(name for (kind, name), obj in self.objects.items() if kind == "volume" and
                             obj["Labels"].get("com.docker.compose.project") == project).encode()
        raise AssertionError("unexpected Docker command: " + str(args))

    def target(self):
        with mock.patch.dict(os.environ, {"DOCKER_HOST": ""}), \
             mock.patch.object(capture, "run", side_effect=self.fake_run), \
             mock.patch.object(capture, "inspect", side_effect=lambda kind, name, cwd: self.objects[(kind, name)]):
            return restore.verify_stopped_target(self.checked, self.nc, self.wk)

    def test_complete_checkpoint_and_external_manifest_pin(self):
        digest = capture.sha256(self.evidence / "manifest.json")
        checked = self.verify(expected_manifest=digest)
        self.assertEqual(checked["manifest_sha256"], digest)
        self.assertEqual(len(checked["archives"]), 8)
        with self.assertRaisesRegex(restore.RestorePlanError, "external pin"):
            self.verify(expected_manifest="0" * 64)

    def test_digest_detects_modified_archive(self):
        with (self.evidence / "weknora-app-data.tar").open("ab") as output:
            output.write(b"tampered")
        with self.assertRaisesRegex(restore.RestorePlanError, "SHA-256"):
            self.verify()

    def test_incomplete_marker_and_extra_file_refused(self):
        for name in ("INCOMPLETE", "failure.json", "unexpected"):
            self.write_private(self.evidence / name, b"blocked")
            with self.assertRaisesRegex(restore.RestorePlanError, "marker"):
                self.verify()
            (self.evidence / name).unlink()

    def test_manifest_duplicate_json_key_refused(self):
        self.write_private(self.evidence / "manifest.json", b'{"status":"COMPLETE","status":"INCOMPLETE"}')
        with self.assertRaisesRegex(restore.RestorePlanError, "duplicate JSON"):
            self.verify()

    def test_private_modes_owner_hardlinks_and_symlink_refused(self):
        artifact = self.evidence / "weknora.dump"
        artifact.chmod(0o644)
        with self.assertRaisesRegex(restore.RestorePlanError, "private mode"):
            self.verify()
        artifact.chmod(0o600)
        self.evidence.chmod(0o755)
        with self.assertRaisesRegex(restore.RestorePlanError, "private mode"):
            self.verify()
        self.evidence.chmod(0o700)
        external = self.root / "hardlink"
        os.link(artifact, external)
        with self.assertRaisesRegex(restore.RestorePlanError, "hardlinked"):
            self.verify()
        external.unlink()
        original = artifact.read_bytes()
        artifact.unlink()
        self.write_private(external, original)
        artifact.symlink_to(external)
        with self.assertRaisesRegex(restore.RestorePlanError, "symlink"):
            self.verify()
        with mock.patch.object(os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaisesRegex(restore.RestorePlanError, "owner"):
                restore.plain_owned(self.evidence, 0o700, directory=True)

    def test_archive_paths_links_and_special_nodes_refused(self):
        cases = [
            [("../escape", "file", b"x")],
            [("/escape", "file", b"x")],
            [("dir\\escape", "file", b"x")],
            [("pivot", "sym", "/tmp"), ("pivot/payload", "file", b"x")],
            [("pivot", "sym", "../../tmp")],
            [("dir", "sym", "safe"), ("dir/payload", "file", b"x")],
            [("hard", "hard", "missing")],
            [("first", "hard", "second"), ("second", "hard", "first")],
            [("pivot", "sym", "."), ("escape", "sym", "pivot/../outside")],
            [("first", "sym", "second"), ("second", "sym", "first")],
            [("pipe", "fifo", "")],
            [("same", "file", b"x"), ("./same", "file", b"x")],
        ]
        path = self.root / "bad.tar"
        for entries in cases:
            with self.subTest(entries=entries):
                self.tar(path, entries)
                with self.assertRaises(restore.RestorePlanError):
                    restore.archive_inventory(path)

    def test_safe_internal_symlink_and_hardlink_allowed(self):
        path = self.root / "safe.tar"
        self.tar(path, [("dir", "dir", ""), ("payload", "file", b"x"),
                        ("dir/link", "sym", "../payload"), ("copy", "hard", "payload")])
        self.assertEqual(restore.archive_inventory(path)["payload_bytes"], 1)
        with self.assertRaisesRegex(restore.RestorePlanError, "crosses"):
            restore.archive_inventory(path, allowed_roots={"dir", "payload", "copy"})

    def test_archive_inventory_preserves_sticky_permissions(self):
        path = self.root / "sticky.tar"
        with tarfile.open(path, "w") as archive:
            for name, kind, mode in [(".", tarfile.DIRTYPE, 0o1777),
                                      ("restricted", tarfile.DIRTYPE, 0o1700),
                                      ("restricted/payload", tarfile.REGTYPE, 0o1640)]:
                member = tarfile.TarInfo(name)
                member.type, member.mode = kind, mode
                member.size = 1 if kind == tarfile.REGTYPE else 0
                archive.addfile(member, io.BytesIO(b"x") if member.isfile() else None)
        modes = {name: entry["mode"] for name, entry in restore.archive_inventory(path)["entries"].items()}
        self.assertEqual({"": 0o1777, "restricted": 0o1700, "restricted/payload": 0o1640}, modes)

    def test_sticky_permission_preservation_does_not_allow_setuid_or_setgid(self):
        path = self.root / "privileged.tar"
        for mode in (0o4640, 0o2640, 0o6640, 0o7640):
            with self.subTest(mode=oct(mode)):
                with tarfile.open(path, "w") as archive:
                    member = tarfile.TarInfo("payload")
                    member.mode, member.size = mode, 1
                    archive.addfile(member, io.BytesIO(b"x"))
                with self.assertRaisesRegex(restore.RestorePlanError, "privileged"):
                    restore.archive_inventory(path)

    def test_runtime_archive_missing_extra_and_cross_root_refused(self):
        path = self.evidence / "runtime-inputs.tar"
        self.tar(path, [("weknora-local/.env", "file", b"x")])
        self.manifest["artifact_sha256"][path.name] = capture.sha256(path)
        self.save_manifest()
        with self.assertRaisesRegex(restore.RestorePlanError, "missing a required"):
            self.verify()
        self.tar(path, [("nextcloud/unapproved-secret", "file", b"x")])
        self.manifest["artifact_sha256"][path.name] = capture.sha256(path)
        self.save_manifest()
        with self.assertRaisesRegex(restore.RestorePlanError, "unexpected runtime"):
            self.verify()

    def test_app_bytes_must_match_git_even_if_manifest_digest_rewritten(self):
        path = self.evidence / "nextcloud-app-code.tar"
        self.tar(path, [("apps/integration_weknora", "dir", ""),
                        ("apps/integration_weknora/app.php", "file", b"modified source")])
        digest = capture.sha256(path)
        self.manifest["artifact_sha256"][path.name] = digest
        self.manifest["nextcloud_app_code_sha256"] = digest
        self.save_manifest()
        with self.assertRaisesRegex(restore.RestorePlanError, "differs from the captured Git"):
            self.verify()

    def test_captured_ignored_build_extras_recorded_unknown_php_refused(self):
        module = self.app / "node_modules/synthetic-module"
        module.mkdir(parents=True)
        (module / "index.js").write_text("synthetic build dependency")
        (self.app / "tests/__pycache__").mkdir(parents=True)
        (self.app / "tests/__pycache__/fixture.pyc").write_bytes(b"synthetic cache")
        archive = self.evidence / "nextcloud-app-code.tar"
        archive.unlink()
        capture.host_archive(archive, {"apps/integration_weknora": self.app})
        digest = capture.sha256(archive)
        self.manifest["artifact_sha256"][archive.name] = digest
        self.manifest["nextcloud_app_code_sha256"] = digest
        self.save_manifest()
        checked = self.verify()
        self.assertEqual(checked["app_code_verification"]["git_tracked_files_verified"], 1)
        self.assertEqual(checked["app_code_verification"]["captured_build_extra_entries"], 2)
        plan = restore.restore_plan(checked, target_verified=False)
        self.assertIn("operator review of captured Git-ignored build extras", plan["open_gates"])
        (self.app / "untracked.php").write_text("<?php // unexpected")
        archive.unlink()
        capture.host_archive(archive, {"apps/integration_weknora": self.app})
        digest = capture.sha256(archive)
        self.manifest["artifact_sha256"][archive.name] = digest
        self.manifest["nextcloud_app_code_sha256"] = digest
        self.save_manifest()
        with self.assertRaisesRegex(restore.RestorePlanError, "unknown untracked"):
            self.verify()

    def test_saved_container_topology_and_image_tag_must_match(self):
        self.manifest["projects"][capture.NC_PROJECT]["containers"]["db"]["image_tag"] = "changed"
        self.save_manifest()
        with self.assertRaisesRegex(restore.RestorePlanError, "image reference"):
            self.verify()
        del self.manifest["projects"][capture.NC_PROJECT]["containers"]["cron"]
        self.save_manifest()
        with self.assertRaisesRegex(restore.RestorePlanError, "topology"):
            self.verify()

    def test_stopped_owned_target_uses_only_read_commands(self):
        target = self.target()
        self.assertEqual(sum(len(items) for items in target["containers"].values()), 15)
        self.assertTrue(self.calls)
        for command in self.calls:
            self.assertFalse(any(word in command for word in ("up", "start", "stop", "run", "rm", "create", "exec")))

    def test_new_candidate_ids_images_allowed_with_old_image_retained(self):
        key = ("container", f"{capture.WK_PROJECT}-app-1")
        item = self.objects[key]
        old_id = item["Id"]
        item["Id"] = "e" * 64
        item["Image"] = "sha256:" + "b" * 64
        item["Config"]["Image"] = "fixture:new"
        self.configs[capture.WK_PROJECT]["services"]["app"]["image"] = "fixture:new"
        item["Config"]["Labels"]["com.docker.compose.config-hash"] = hashlib.sha256(
            json.dumps(self.configs[capture.WK_PROJECT]["services"]["app"], sort_keys=True).encode()).hexdigest()
        self.objects.pop(("container", old_id))
        self.objects[("container", item["Id"])] = item
        self.objects[("image", item["Image"])] = {"Id": item["Image"], "Os": "linux"}
        # Saved checkpoint configuration stays on the old image reference.
        checked = copy.deepcopy(self.checked)
        checked["configs"][capture.WK_PROJECT]["services"]["app"]["image"] = "fixture:old"
        self.checked = checked
        target = self.target()
        self.assertEqual(target["containers"][capture.WK_PROJECT]["app"]["id"], "e" * 64)
        self.assertIn("sha256:" + "a" * 64, target["old_images_present"])
        self.assertIn("sha256:" + "b" * 64, target["current_images_present"])
        checked["manifest_sha256"] = "0" * 64
        plan = restore.restore_plan(checked, target_verified=True, current_target=target)
        self.assertEqual(plan["current_stopped_inventory"], target)
        self.assertFalse(plan["restore_verified"])

    def test_changed_restore_mount_destination_refused(self):
        self.checked = copy.deepcopy(self.checked)
        self.configs[capture.NC_PROJECT]["services"]["nextcloud"]["volumes"][0]["read_only"] = False
        with self.assertRaisesRegex(restore.RestorePlanError, "mounts differ"):
            self.target()

    def test_exact_desktop_stopped_port_loss_requires_matching_current_hash(self):
        self.checked = copy.deepcopy(self.checked)
        service = self.configs[capture.NC_PROJECT]["services"]["nextcloud"]
        ports = [{"target": 80, "protocol": "tcp", "host_ip": "127.0.0.1", "published": "18082"},
                 {"target": 80, "protocol": "tcp", "host_ip": "10.106.105.121", "published": "18082"}]
        service["ports"] = ports
        self.checked["configs"][capture.NC_PROJECT]["services"]["nextcloud"]["ports"] = ports
        item = self.objects[("container", f"{capture.NC_PROJECT}-nextcloud-1")]
        item["HostConfig"]["PortBindings"] = {"80/tcp": [
            {"HostIp": "127.0.0.1", "HostPort": "18082"}, {"HostIp": "127.0.0.1", "HostPort": ""}]}
        item["Config"]["Labels"]["com.docker.compose.config-hash"] = hashlib.sha256(
            json.dumps(service, sort_keys=True).encode()).hexdigest()
        self.target()
        item["Config"]["Labels"]["com.docker.compose.config-hash"] = "0" * 64
        with self.assertRaisesRegex(restore.RestorePlanError, "creation-time Compose hash"):
            self.target()
        item["Config"]["Labels"]["com.docker.compose.config-hash"] = hashlib.sha256(
            json.dumps(service, sort_keys=True).encode()).hexdigest()
        item["HostConfig"]["PortBindings"]["80/tcp"][1]["HostPort"] = "28082"
        with self.assertRaisesRegex(restore.RestorePlanError, "ports"):
            self.target()

    def set_stopped_ports(self, ports, bindings):
        self.checked = copy.deepcopy(self.checked)
        service = self.configs[capture.NC_PROJECT]["services"]["nextcloud"]
        service["ports"] = ports
        self.checked["configs"][capture.NC_PROJECT]["services"]["nextcloud"]["ports"] = copy.deepcopy(ports)
        item = self.objects[("container", f"{capture.NC_PROJECT}-nextcloud-1")]
        item["HostConfig"]["PortBindings"] = bindings
        item["Config"]["Labels"]["com.docker.compose.config-hash"] = hashlib.sha256(
            json.dumps(service, sort_keys=True).encode()).hexdigest()
        return item

    def test_single_rfc1918_stopped_lan_binding_accepted_via_helper(self):
        for address in ("10.106.105.121", "172.16.1.10", "192.168.1.10"):
            with self.subTest(address=address):
                self.set_stopped_ports(
                    [{"target": 80, "protocol": "tcp", "host_ip": address, "published": "18082"}],
                    {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]})
                self.target()

    def test_single_public_wildcard_loopback_ipv6_and_missing_ports_refused(self):
        for address in ("8.8.8.8", "0.0.0.0", "127.0.0.1", "169.254.1.1", "192.0.2.2", "::1", "fd00::1"):
            with self.subTest(address=address):
                self.set_stopped_ports(
                    [{"target": 80, "protocol": "tcp", "host_ip": address, "published": "18082"}],
                    {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]})
                with self.assertRaisesRegex(restore.RestorePlanError, "ports"):
                    self.target()
        ports = [{"target": 80, "protocol": "tcp", "host_ip": "10.106.105.121", "published": "18082"}]
        for bindings in ({}, {"80/tcp": [{"HostIp": "::1", "HostPort": ""}]},
                         {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "28082"}]}):
            with self.subTest(bindings=bindings):
                self.set_stopped_ports(ports, bindings)
                with self.assertRaisesRegex(restore.RestorePlanError, "ports"):
                    self.target()

    def test_duplicate_actual_stopped_and_duplicate_current_requested_ports_refused(self):
        lan = {"target": 80, "protocol": "tcp", "host_ip": "10.106.105.121", "published": "18082"}
        loopback = {"target": 80, "protocol": "tcp", "host_ip": "127.0.0.1", "published": "18082"}
        cases = [
            ([lan], {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}] * 2}),
            ([loopback, lan], {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18082"},
                                        {"HostIp": "127.0.0.1", "HostPort": ""},
                                        {"HostIp": "127.0.0.1", "HostPort": ""}]}),
            ([lan, lan], {"80/tcp": [{"HostIp": "10.106.105.121", "HostPort": "18082"}]}),
        ]
        for ports, bindings in cases:
            with self.subTest(ports=ports, bindings=bindings):
                self.set_stopped_ports(ports, bindings)
                with self.assertRaisesRegex(restore.RestorePlanError, "ports"):
                    self.target()

    def test_duplicate_saved_compose_and_frozen_running_ports_refused(self):
        port = {"target": 80, "protocol": "tcp", "host_ip": "10.106.105.121", "published": "18082"}
        with self.assertRaisesRegex(restore.RestorePlanError, "duplicate raw"):
            restore.expected_port_bindings({"ports": [port, port]})
        with self.assertRaisesRegex(restore.RestorePlanError, "duplicate entries"):
            restore.frozen_port_bindings({"80/tcp": [{"HostIp": "10.106.105.121", "HostPort": "18082"}] * 2})

    def test_frozen_running_bindings_and_creation_hash_required_in_checkpoint(self):
        identity = self.manifest["projects"][capture.NC_PROJECT]["containers"]["nextcloud"]
        identity["running_port_bindings"] = {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18082"}]}
        self.save_manifest()
        with self.assertRaisesRegex(restore.RestorePlanError, "running port bindings differ"):
            self.verify()
        identity["running_port_bindings"] = {}
        identity.pop("compose_config_hash")
        self.save_manifest()
        with self.assertRaisesRegex(restore.RestorePlanError, "creation-time Compose"):
            self.verify()

    def test_nonlocal_volume_driver_refused(self):
        self.objects[("volume", f"{capture.WK_PROJECT}_app-data")]["Driver"] = "nfs"
        with self.assertRaisesRegex(restore.RestorePlanError, "driver"):
            self.target()

    def test_running_extra_and_foreign_volume_ownership_refused(self):
        container = self.objects[("container", f"{capture.NC_PROJECT}-nextcloud-1")]
        container["State"]["Running"] = True
        with self.assertRaisesRegex(restore.RestorePlanError, "stopped"):
            self.target()
        container["State"]["Running"] = False
        volume = self.objects[("volume", f"{capture.WK_PROJECT}_app-data")]
        volume["Labels"]["com.docker.compose.volume"] = "foreign"
        with self.assertRaisesRegex(restore.RestorePlanError, "ownership"):
            self.target()
        volume["Labels"]["com.docker.compose.volume"] = "app-data"
        extra = copy.deepcopy(container)
        extra["Id"] = "f" * 64
        extra["Config"]["Labels"]["com.docker.compose.service"] = "unexpected"
        self.objects[("container", extra["Id"])] = extra
        with self.assertRaisesRegex(restore.RestorePlanError, "extra or missing"):
            self.target()

    def add_pair_bridge(self):
        wk = self.configs[capture.WK_PROJECT]
        wk["networks"]["nextcloud-dev"] = {"name": capture.NC_PROJECT + "_default", "external": True}
        wk["services"]["app"]["networks"]["nextcloud-dev"] = None
        identity = self.projects[capture.WK_PROJECT]["containers"]["app"]
        identity["compose_config_hash"] = hashlib.sha256(json.dumps(wk["services"]["app"], sort_keys=True).encode()).hexdigest()
        path = self.evidence / "weknora-resolved-compose.json"
        self.write_private(path, json.dumps(wk).encode())
        digest = capture.sha256(path)
        self.manifest["artifact_sha256"][path.name] = digest
        self.projects[capture.WK_PROJECT]["resolved_compose_sha256"] = digest
        self.save_manifest()
        self.objects = self.target_objects()

    def test_exact_approved_pair_bridge_source_and_target_allowed(self):
        self.add_pair_bridge()
        self.verify()
        owner = self.objects[("network", capture.NC_PROJECT + "_default")]
        owner["Containers"] = {self.projects[capture.WK_PROJECT]["containers"]["app"]["id"]: {}}
        self.target()

    def test_arbitrary_external_name_alias_or_reverse_bridge_refused(self):
        self.add_pair_bridge()
        for name, alias in (("foreign_default", "nextcloud-dev"),
                            (capture.NC_PROJECT + "_default", "unapproved")):
            with self.subTest(name=name, alias=alias):
                configs = copy.deepcopy(self.configs)
                configs[capture.WK_PROJECT]["networks"] = {"default": self.configs[capture.WK_PROJECT]["networks"]["default"],
                                                          alias: {"name": name, "external": True}}
                with self.assertRaisesRegex(restore.RestorePlanError, "unapproved external"):
                    restore.approved_network_owners(configs)
        configs = copy.deepcopy(self.configs)
        configs[capture.NC_PROJECT]["networks"]["weknora-dev"] = {"name": capture.WK_PROJECT + "_default", "external": True}
        with self.assertRaisesRegex(restore.RestorePlanError, "unapproved external"):
            restore.approved_network_owners(configs)

    def test_approved_bridge_requires_actual_nextcloud_owner_and_only_pair_members(self):
        self.add_pair_bridge()
        network = self.objects[("network", capture.NC_PROJECT + "_default")]
        network["Labels"]["com.docker.compose.project"] = capture.WK_PROJECT
        with self.assertRaisesRegex(restore.RestorePlanError, "network.*ownership"):
            self.target()
        network["Labels"]["com.docker.compose.project"] = capture.NC_PROJECT
        network["Containers"] = {"f" * 64: {}}
        with self.assertRaisesRegex(restore.RestorePlanError, "non-owned container member"):
            self.target()

    def test_foreign_stopped_volume_or_network_user_refused(self):
        outsider = {"Id": "f" * 64, "Config": {"Labels": {}}, "Mounts": [
            {"Type": "volume", "Name": f"{capture.WK_PROJECT}_app-data"}], "NetworkSettings": {"Networks": {}}}
        self.objects[("container", outsider["Id"])] = outsider
        with self.assertRaisesRegex(restore.RestorePlanError, "stopped container.*volume"):
            self.target()
        outsider["Mounts"] = []
        outsider["NetworkSettings"]["Networks"] = {f"{capture.NC_PROJECT}_default": {}}
        with self.assertRaisesRegex(restore.RestorePlanError, "stopped container.*network"):
            self.target()

    def test_network_and_image_identity_must_remain_available(self):
        network = self.objects[("network", f"{capture.NC_PROJECT}_default")]
        network["Labels"]["com.docker.compose.project"] = "foreign"
        with self.assertRaisesRegex(restore.RestorePlanError, "network.*ownership"):
            self.target()
        network["Labels"]["com.docker.compose.project"] = capture.NC_PROJECT
        image = self.objects[("image", "sha256:" + "a" * 64)]
        image["Id"] = "sha256:" + "b" * 64
        with self.assertRaisesRegex(restore.RestorePlanError, "immutable checkpoint image"):
            self.target()

    def test_plan_recovery_copy_before_overwrites_replay_before_reopen(self):
        checked = self.verify()
        plan = restore.restore_plan(checked, target_verified=False)
        actions = [item["action"] for item in plan["steps"]]
        self.assertLess(actions.index("capture_pre_restore_recovery_copy"),
                        actions.index("restore_captured_bind_inputs_and_app_code"))
        self.assertLess(actions.index("capture_pre_restore_recovery_copy"),
                        actions.index("replace_all_eight_volume_contents_as_one_cold_pair"))
        self.assertLess(actions.index("replay_external_post_checkpoint_withdrawals_and_reconcile"),
                        actions.index("operator_controlled_reopen"))
        self.assertEqual(len(plan["steps"][3]["volumes"]), 8)
        self.assertFalse(plan["restore_verified"])
        self.assertFalse(plan["automatic_reopen_permitted"])
        self.assertFalse(plan["external_replay_lower_bound_proven"])
        self.assertFalse(plan["apply_supported"])
        self.assertFalse(plan["current_stopped_target_verified"])
        self.assertIn("live stopped target, owned volumes/networks and old immutable images", plan["open_gates"])

    def test_apply_flag_unsupported(self):
        with self.assertRaises(SystemExit) as exit_code, mock.patch("sys.stderr", new=io.StringIO()):
            restore.main(["--checkpoint-dir", str(self.evidence), "--apply"])
        self.assertEqual(exit_code.exception.code, 2)

    def test_offline_cli_does_not_call_docker_or_claim_live_target(self):
        output = io.StringIO()
        with mock.patch.object(restore, "verify_stopped_target", side_effect=AssertionError("Docker forbidden")), \
             mock.patch("sys.stdout", new=output):
            result = restore.main(["--checkpoint-dir", str(self.evidence), "--nextcloud-dir", str(self.nc),
                                   "--weknora-dir", str(self.wk), "--offline"])
        self.assertEqual(result, 0)
        plan = json.loads(output.getvalue())
        self.assertFalse(plan["current_stopped_target_verified"])
        self.assertFalse(plan["restore_verified"])


if __name__ == "__main__":
    unittest.main()
