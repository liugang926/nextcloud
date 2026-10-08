#!/usr/bin/env python3
"""Offline safety contracts for the closed captured-application rehearsal."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
def module(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value
app = module("application_drill", "shared-application-restore-drill.py")
fixture = module("source_fixture", "test_shared_cold_restore_plan.py")


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.archive = self.root / "private.tar"
        with tarfile.open(self.archive, "w") as tar:
            for name, kind, target in [("inputs", "dir", ""), ("inputs/key", "file", ""),
                                        ("inputs/key-copy", "hard", "inputs/key"), ("inputs/key-link", "sym", "key")]:
                member = tarfile.TarInfo(name)
                member.mode, member.uid, member.gid = 0o755 if kind == "dir" else 0o644, 33, 33
                if kind == "dir": member.type = tarfile.DIRTYPE
                elif kind == "file": member.size = 7
                elif kind == "hard": member.type, member.linkname = tarfile.LNKTYPE, target
                else: member.type, member.linkname = tarfile.SYMTYPE, target
                tar.addfile(member, io.BytesIO(b"private") if kind == "file" else None)
        self.inventory = app.plan.archive_inventory(self.archive, allowed_roots={"inputs"})

    def test_private_bytes_links_and_permissions_are_restored(self):
        root = self.root / "stage"
        app.extract_private(self.archive, root, self.inventory)
        self.assertEqual(b"private", (root / "inputs/key").read_bytes())
        self.assertEqual(0o600, stat.S_IMODE((root / "inputs/key").stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE((root / "inputs").stat().st_mode))
        self.assertEqual((root / "inputs/key").stat().st_ino, (root / "inputs/key-copy").stat().st_ino)
        self.assertEqual("key", os.readlink(root / "inputs/key-link"))

    def test_drift_bytes_links_extra_files_and_modes_refused(self):
        for mutation in [lambda p: (p / "inputs/key").write_bytes(b"changed"),
                         lambda p: (p / "inputs/key").chmod(0o644),
                         lambda p: (p / "extra").write_bytes(b"x"),
                         lambda p: (p / "inputs/key-link").unlink()]:
            with self.subTest(mutation=mutation):
                root = self.root / next(tempfile._get_candidate_names())
                app.extract_private(self.archive, root, self.inventory)
                mutation(root)
                with self.assertRaises(app.drill.DrillError):
                    app.verify_staging(root, self.inventory)

    def test_preexisting_or_symlink_destination_refused_without_write(self):
        for root in [self.root, self.root / "link"]:
            if root != self.root:
                root.symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(app.drill.DrillError):
                app.extract_private(self.archive, root, self.inventory)
        self.assertFalse((self.root / "inputs").exists())

    def test_wrong_saved_payload_digest_refused(self):
        inventory = copy.deepcopy(self.inventory)
        inventory["entries"]["inputs/key"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(app.drill.DrillError, "bytes"):
            app.extract_private(self.archive, self.root / "stage", inventory)


class EnvironmentTests(unittest.TestCase):
    def test_saved_secret_inputs_preserved_separate_from_runtime_overlay(self):
        saved = {"JWT_SECRET": "private-jwt", "SYSTEM_AES_KEY": "private-aes", "DB_HOST": "old",
                 "DB_PASSWORD": "private-db", "HTTPS_PROXY": "outside", "no_proxy": "outside",
                 "RAG_MODEL_TOKEN": "paid", "AUTO_MIGRATE": "true"}
        before = copy.deepcopy(saved)
        hosts = {"weknora-postgres": "new-pg", "weknora-redis": "new-redis"}
        env = app.fenced_environment(saved, "weknora", hosts)
        self.assertEqual(before, saved)
        for key in ["JWT_SECRET", "SYSTEM_AES_KEY", "DB_PASSWORD"]:
            self.assertEqual(saved[key], env[key])
        self.assertEqual("false", env["AUTO_MIGRATE"])
        self.assertEqual("false", env["WEKNORA_NEXTCLOUD_SYNC_RECOVERY_ENABLED"])
        self.assertFalse(any(k.lower().endswith("proxy") for k in env))
        self.assertNotIn("RAG_MODEL_TOKEN", env)

    def test_env_file_injection_refused(self):
        with self.assertRaises(app.drill.DrillError):
            app.fenced_environment({"JWT_SECRET": "value\nDB_HOST=outside"}, "weknora", {})


class BindRootTests(unittest.TestCase):
    def test_folded_directory_bind_descendant_is_resolved_and_pivots_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "ca.crt").write_bytes(b"certificate")
            original = Path("/original/certs")
            roots = {original: root}
            self.assertEqual(root / "ca.crt", app.staged_bind_path(original / "ca.crt", roots))
            self.assertEqual(root, app.staged_bind_path(original, roots))
            for source in [Path("/outside/ca.crt"), original / "missing"]:
                with self.assertRaises(app.drill.DrillError):
                    app.staged_bind_path(source, roots)
            (root / "alias.crt").symlink_to("ca.crt")
            with self.assertRaises(app.drill.DrillError):
                app.staged_bind_path(original / "alias.crt", roots)


class RuntimeOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.owner = app.ApplicationDrill(Path("/private"), "a" * 24)
        self.saved = {"id": "b" * 64, "name": "new-runtime", "image": "sha256:" + "c" * 64,
                      "entrypoint": ["/app/WeKnora"], "command": [], "user": "0:0", "network": "owned-internal",
                      "caps": ["DAC_OVERRIDE"], "memory": 1024**3, "tmpfs": {"/tmp": "rw"},
                      "environment_sha256": app.digest({"JWT_SECRET": "private"}),
                      "mounts": [{"type": "volume", "source": "owned-data", "target": "/data/files", "readonly": True}]}
        self.item = {"Id": self.saved["id"], "Name": "/new-runtime", "Image": self.saved["image"],
                     "Config": {"Image": self.saved["image"], "Entrypoint": self.saved["entrypoint"], "Cmd": [], "User": "0:0",
                                "Env": ["JWT_SECRET=private"], "Labels": {app.drill.LABEL: "a" * 24, app.drill.ROLE_LABEL: "app"}},
                     "HostConfig": {"ReadonlyRootfs": True, "NetworkMode": "owned-internal", "RestartPolicy": {"Name": "no"},
                                    "CapDrop": ["ALL"], "CapAdd": ["CAP_DAC_OVERRIDE"], "SecurityOpt": ["no-new-privileges"],
                                    "NanoCpus": 1_000_000_000, "Memory": 1024**3, "PidsLimit": 256, "Tmpfs": {"/tmp": "rw"},
                                    "LogConfig": {"Type": "json-file", "Config": {"max-size": "5m", "max-file": "2"}}},
                     "Mounts": [{"Type": "volume", "Name": "owned-data", "Destination": "/data/files", "RW": False}],
                     "NetworkSettings": {"Networks": {"owned-internal": {}}}}

    def test_closed_exact_runtime_passes(self):
        self.owner.verify_runtime_container("app", self.saved, self.item)

    def test_empty_command_null_representation_passes_but_new_command_refused(self):
        self.item["Config"]["Cmd"] = None
        self.owner.verify_runtime_container("app", self.saved, self.item)
        self.item["Config"]["Cmd"] = ["unexpected"]
        with self.assertRaises(app.drill.DrillError):
            self.owner.verify_runtime_container("app", self.saved, self.item)

    def test_same_volume_name_with_changed_physical_subpath_is_refused(self):
        self.saved["mounts"][0].update(subpath="", volume_path="/docker/owned/_data")
        self.item["Mounts"][0]["Source"] = "/docker/owned/_data/foreign"
        with self.assertRaises(app.drill.DrillError):
            self.owner.verify_runtime_container("app", self.saved, self.item)

    def test_desktop_host_bind_alias_matches_only_exact_mount_specification(self):
        mount = {"type": "bind", "source": "/Users/private/input", "target": "/config", "readonly": True}
        self.saved["mounts"].append(mount)
        self.item["Mounts"].append({"Type": "bind", "Source": "/host_mnt/Users/private/input", "Destination": "/config", "RW": False})
        self.item["HostConfig"]["Mounts"] = [{"Type": "volume", "Source": "owned-data", "Target": "/data/files", "ReadOnly": True},
                                               {"Type": "bind", "Source": "/host_mnt/Users/private/input", "Target": "/config", "ReadOnly": True}]
        self.owner.verify_runtime_container("app", self.saved, self.item)
        self.item["HostConfig"]["Mounts"][1]["Source"] += "-foreign"
        with self.assertRaises(app.drill.DrillError):
            self.owner.verify_runtime_container("app", self.saved, self.item)

    def test_env_image_ports_data_write_mount_resources_or_peer_drift_refused(self):
        original = copy.deepcopy(self.item)
        mutations = [lambda d: d["Config"].update(Env=["JWT_SECRET=changed"]),
                     lambda d: d.update(Image="sha256:" + "e" * 64),
                     lambda d: d["HostConfig"].update(PortBindings={"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}]}),
                     lambda d: d["Mounts"][0].update(RW=True),
                     lambda d: d["Mounts"].append({"Type": "bind", "Source": "/shared", "Destination": "/unsafe", "RW": False}),
                     lambda d: d["HostConfig"].update(Memory=0),
                     lambda d: d["NetworkSettings"]["Networks"].update(external={})]
        for mutation in mutations:
            self.item = copy.deepcopy(original)
            mutation(self.item)
            with self.subTest(mutation=mutation), self.assertRaises(app.drill.DrillError):
                self.owner.verify_runtime_container("app", self.saved, self.item)


class DefaultTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.RestoreTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.args = ["--checkpoint-dir", str(self.fixture.evidence), "--nextcloud-dir", str(self.fixture.nc),
                     "--weknora-dir", str(self.fixture.wk), "--expected-manifest-sha256", app.capture.sha256(self.fixture.evidence / "manifest.json")]

    def test_default_has_no_docker_or_staging_and_cannot_claim_apps_restored(self):
        with mock.patch.object(app.drill, "apply_drill") as apply, mock.patch.object(app.capture, "local_docker") as docker, mock.patch("sys.stdout", new=io.StringIO()) as stdout:
            self.assertEqual(0, app.main(self.args))
        apply.assert_not_called()
        docker.assert_not_called()
        self.assertFalse(json.loads(stdout.getvalue())["application_runtime_restored"])

    def test_apply_evidence_cannot_modify_checkpoint_or_original_input_tree(self):
        checked = app.plan.verify_checkpoint(self.fixture.evidence, self.fixture.nc, self.fixture.wk,
                                             app.capture.sha256(self.fixture.evidence / "manifest.json"))
        for output in [self.fixture.evidence / "new-report", self.fixture.wk / "new-report",
                       self.fixture.nc / "apps/integration_weknora/new-report"]:
            with self.subTest(output=output), mock.patch.object(app.drill, "OwnedDrill") as owner:
                with self.assertRaises(app.drill.DrillError):
                    app.drill.apply_drill(checked, self.fixture.evidence, self.fixture.nc, output, owner_type=owner)
                self.assertFalse(output.exists())
                owner.assert_not_called()

    def test_external_manifest_or_payload_tamper_refused_before_resource_creation(self):
        (self.fixture.evidence / "nextcloud.dump").write_bytes(b"tamper")
        with mock.patch.object(app.drill, "apply_drill") as apply, mock.patch.object(app.capture, "local_docker") as docker, mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(1, app.main(self.args + ["--apply"]))
        apply.assert_not_called()
        docker.assert_not_called()


class LDAPCopyAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.original = self.root / "ldap.tar"
        self.make_tar(self.original)
        self.expected = app.plan.archive_inventory(self.original)

    def make_tar(self, path, *, lock=b"mutex", data=b"identity", config=b"config", mode=0o600, extra=False):
        with tarfile.open(path, "w") as tar:
            for name, value in [("data/lock.mdb", lock), ("data/data.mdb", data), ("slapd.d/config.ldif", config)] + ([("foreign", b"x")] if extra else []):
                member = tarfile.TarInfo(name)
                member.size, member.uid, member.gid, member.mode = len(value), 1001, 0, mode
                tar.addfile(member, io.BytesIO(value))

    def audit(self, **changes):
        actual = self.root / "actual.tar"
        self.make_tar(actual, **changes)
        owner = mock.Mock()
        owner.containers = {}
        owner.create_container.return_value = "owned-helper"
        owner.execute.side_effect = lambda container, output_file: output_file.write(actual.read_bytes())
        return app.audit_volume(owner, "ldap-runtime-cache", "immutable-image", self.root / "audit.tar", self.expected, ignore_lmdb_lock=True)

    def test_only_lmdb_mutex_bytes_and_size_may_change(self):
        self.assertEqual(64, len(self.audit(lock=b"different runtime mutex bytes")))

    def test_identity_data_config_mode_and_membership_drift_are_refused(self):
        for changes in [{"data": b"changed identity"}, {"config": b"changed config"}, {"mode": 0o644}, {"extra": True}]:
            with self.subTest(changes=changes), self.assertRaises(app.drill.DrillError):
                self.audit(**changes)
            (self.root / "audit.tar").unlink(missing_ok=True)


class NextcloudCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_new_marker_and_all_original_files_and_subtrees_mounted_readonly(self):
        checkpoint, evidence = self.root / "checkpoint", self.root / "evidence"
        checkpoint.mkdir(mode=0o700); evidence.mkdir(mode=0o700)
        path = checkpoint / "nextcloud-nextcloud-html.tar"
        with tarfile.open(path, "w") as tar:
            for name in [".", "data", "data/admin", "data/admin/files"]:
                member = tarfile.TarInfo(name)
                member.type, member.uid, member.gid, member.mode = tarfile.DIRTYPE, 33, 33, 0o750
                tar.addfile(member)
            for name, value in [("data/.ncdata", b"marker"), ("data/admin/files/original.txt", b"must remain on original")]:
                member = tarfile.TarInfo(name)
                member.uid, member.gid, member.mode, member.size = 33, 33, 0o640, len(value)
                tar.addfile(member, io.BytesIO(value))
        path.chmod(0o600)
        checked = {"archives": {path.name: app.plan.archive_inventory(path)},
                   "manifest": {"artifact_sha256": {path.name: app.capture.sha256(path)},
                                "projects": {app.capture.NC_PROJECT: {"containers": {"db": {"image_id": "sha256:" + "a" * 64}}}}}}
        owner = mock.Mock()
        owner.checkpoint, owner.containers = checkpoint, {}
        contents = {}
        def execute(cid, input_file=None, output_file=None):
            if input_file is not None: contents["tar"] = input_file.read_bytes()
            else: output_file.write(contents["tar"])
        owner.execute.side_effect = execute
        mounts, expected, names, image = app.prepare_nextcloud_data_cache(owner, checked, evidence)
        self.assertEqual({"", ".ncdata"}, set(expected["entries"]))
        self.assertEqual({"admin"}, names)
        self.assertEqual(33, expected["entries"][".ncdata"]["uid"])
        self.assertEqual(0o640, expected["entries"][".ncdata"]["mode"])
        self.assertEqual([False, True, True], [m["readonly"] for m in mounts])
        self.assertEqual(["data/.ncdata", "data/admin"], [m["subpath"] for m in mounts[1:]])

    def test_original_root_file_drift_or_unexpected_new_content_refused(self):
        archive = self.root / "before.tar"
        def tar_bytes(body, extra=False):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w") as tar:
                root = tarfile.TarInfo("."); root.type = tarfile.DIRTYPE; root.mode = 0o750; root.uid = root.gid = 33; tar.addfile(root)
                member = tarfile.TarInfo(".ncdata"); member.uid = member.gid = 33; member.mode = 0o640; member.size = len(body); tar.addfile(member, io.BytesIO(body))
                if extra:
                    member = tarfile.TarInfo("unexpected-body"); member.size = 1; tar.addfile(member, io.BytesIO(b"x"))
            return output.getvalue()
        archive.write_bytes(tar_bytes(b"marker"))
        expected = app.plan.archive_inventory(archive)
        for body, extra in [(b"changed", False), (b"marker", True)]:
            owner = mock.Mock()
            owner.execute.side_effect = lambda cid, output_file, data=tar_bytes(body, extra): output_file.write(data)
            with self.subTest(body=body, extra=extra), self.assertRaises(app.drill.DrillError):
                app.audit_nextcloud_data_cache(owner, expected, set(), "immutable-image", self.root)
            (self.root / "nextcloud-cache-after-start.tar").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
