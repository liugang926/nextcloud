#!/usr/bin/env python3
"""Offline refusal/inventory tests; opt-in tiny real-volume rehearsal."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("cold_drill", HERE / "shared-cold-restore-drill.py")
drill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drill)
FIXTURE_SPEC = importlib.util.spec_from_file_location("restore_fixture", HERE / "test_shared_cold_restore_plan.py")
fixture = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(fixture)
IMAGE = "sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416"


def sample_inventory():
    file = {"kind": "file", "uid": 77, "gid": 88, "mode": 0o640, "size": 1,
            "sha256": hashlib.sha256(b"x").hexdigest()}
    return {"entries": {"": {"kind": "directory", "uid": 121, "gid": 122, "mode": 0o700, "size": 0},
            "dir": {"kind": "directory", "uid": 77, "gid": 88, "mode": 0o750, "size": 0},
            "dir/payload": file, "copy": {"kind": "hardlink", "uid": 77, "gid": 88, "mode": 0o640,
                "target": "dir/payload", "linkname": "./dir/payload", "size": 0},
            "pointer": {"kind": "symlink", "uid": 77, "gid": 88, "mode": 0o777,
                "target": "dir/payload", "linkname": "dir/payload", "size": 0}}, "payload_bytes": 1}


class InventoryTests(unittest.TestCase):
    def test_sticky_bit_loss_is_rejected_from_actual_archive_inventories(self):
        with tempfile.TemporaryDirectory() as temp:
            inventories = []
            for index, mode in enumerate((0o1777, 0o777)):
                path = Path(temp) / f"volume-{index}.tar"
                with tarfile.open(path, "w") as archive:
                    member = tarfile.TarInfo(".")
                    member.type, member.mode = tarfile.DIRTYPE, mode
                    archive.addfile(member)
                inventories.append(drill.plan.archive_inventory(path))
            with self.assertRaisesRegex(drill.DrillError, "mode"):
                drill.compare_inventory(*inventories)

    def test_exact_inventory_and_reordered_hardlink_members_pass(self):
        original = sample_inventory()
        reversed_links = copy.deepcopy(original)
        reversed_links["entries"]["copy"] = copy.deepcopy(original["entries"]["dir/payload"])
        reversed_links["entries"]["dir/payload"] = {"kind": "hardlink", "uid": 77, "gid": 88, "mode": 0o640,
                                                  "target": "copy", "linkname": "copy", "size": 0}
        self.assertEqual(drill.compare_inventory(original, original), drill.compare_inventory(original, reversed_links))

    def test_extra_or_missing_member_is_not_hidden_by_tar_compare(self):
        for change in ("extra", "missing"):
            actual = copy.deepcopy(sample_inventory())
            if change == "extra":
                actual["entries"]["unarchived"] = actual["entries"]["dir"]
            else:
                actual["entries"].pop("pointer")
            with self.subTest(change=change), self.assertRaises(drill.DrillError):
                drill.compare_inventory(sample_inventory(), actual)

    def test_byte_owner_mode_symlink_and_hardlink_tamper_rejected(self):
        changes = [("dir/payload", "sha256", "0" * 64), ("dir", "mode", 0o777),
                   ("pointer", "uid", 0), ("dir/payload", "gid", 0),
                   ("pointer", "linkname", "./dir/payload")]
        for name, field, value in changes:
            actual = copy.deepcopy(sample_inventory())
            actual["entries"][name][field] = value
            with self.subTest(field=field), self.assertRaises(drill.DrillError):
                drill.compare_inventory(sample_inventory(), actual)
        actual = copy.deepcopy(sample_inventory())
        actual["entries"]["copy"] = copy.deepcopy(actual["entries"]["dir/payload"])
        with self.assertRaises(drill.DrillError):
            drill.compare_inventory(sample_inventory(), actual)

    def test_unknown_member_kind_rejected(self):
        actual = copy.deepcopy(sample_inventory())
        actual["entries"]["device"] = {"kind": "character_device", "uid": 0, "gid": 0, "mode": 0o600}
        with self.assertRaises(drill.DrillError):
            drill.compare_inventory(sample_inventory(), actual)

    def test_unrepresentable_uid_symlink_mode_or_hardlink_ownership_rejected(self):
        for member, field, value in [("dir", "uid", -1), ("pointer", "mode", 0o644), ("copy", "gid", 99)]:
            actual = copy.deepcopy(sample_inventory())
            actual["entries"][member][field] = value
            with self.subTest(field=field), self.assertRaises(drill.DrillError):
                drill.require_representable(actual)

    def test_symlink_owner_script_quotes_every_archive_path(self):
        inventory = copy.deepcopy(sample_inventory())
        item = inventory["entries"].pop("pointer")
        inventory["entries"]["link'; touch /tmp/not-authorized; '"] = item
        script = drill.symlink_owner_script(inventory)
        self.assertIn("chown -h 77:88 -- './link'", script)
        self.assertNotIn("chown -h 77:88 -- ./link", script)

    def test_private_staging_rejects_hash_symlink_and_hardlink_tamper(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / "archive"
            source.write_bytes(b"private archive")
            source.chmod(0o600)
            with self.assertRaises(drill.DrillError):
                drill.staged_archive(source, root / "wrong", "0" * 64)
            symlink = root / "symlink"
            symlink.symlink_to(source)
            with self.assertRaises(drill.plan.RestorePlanError):
                drill.staged_archive(symlink, root / "symlink-copy", hashlib.sha256(source.read_bytes()).hexdigest())
            os.link(source, root / "hardlink")
            with self.assertRaises(drill.plan.RestorePlanError):
                drill.staged_archive(source, root / "hardlink-copy", hashlib.sha256(source.read_bytes()).hexdigest())


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.owner = drill.OwnedDrill(Path("/private"), "a" * 24)
        name = self.owner.prefix + "-nextcloud-html"
        cid = "c" * 64
        self.owner.volumes = {"nextcloud-html": {"name": name, "created_at": "recorded", "mountpoint": "/docker/owned"}}
        self.owner.containers = {"audit": {"id": cid, "name": self.owner.prefix + "-audit", "image": IMAGE,
                    "volume": name, "target": drill.DATA, "readonly_volume": True, "database": False,
                    "network": "none", "entrypoint": ["tar"], "command": ["-C", drill.DATA, "-cf", "-", "."],
                    "caps": ["DAC_OVERRIDE"], "user": "0:0"}}
        self.item = {"Id": cid, "Name": "/" + self.owner.prefix + "-audit", "Image": IMAGE,
            "Config": {"Image": IMAGE, "Entrypoint": ["tar"], "Cmd": ["-C", drill.DATA, "-cf", "-", "."], "User": "0:0",
                       "Labels": {drill.LABEL: self.owner.token, drill.ROLE_LABEL: "audit"}},
            "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "RestartPolicy": {"Name": "no"},
                           "CapDrop": ["ALL"], "CapAdd": ["CAP_DAC_OVERRIDE"],
                           "LogConfig": {"Type": "none"}, "SecurityOpt": ["no-new-privileges"]},
            "Mounts": [{"Type": "volume", "Name": name, "Destination": drill.DATA, "RW": False}],
            "NetworkSettings": {"Networks": {"none": {}}}}
        self.volume = {"Name": name, "Driver": "local", "Options": None, "CreatedAt": "recorded", "Mountpoint": "/docker/owned",
                       "Labels": {drill.LABEL: self.owner.token, drill.ROLE_LABEL: "nextcloud-html"}}
        self.items, self.calls = [self.item], []
        self.owner.inspect = lambda kind, target: copy.deepcopy(self.volume)
        self.owner.listed = lambda kind, owned=False: ([name] if kind == "volume" else
            [cid] if kind == "container" and owned else [v["Id"] for v in self.items] if kind == "container" else [])
        def run(args, **kwargs):
            self.calls.append(args)
            if args[:2] == ["docker", "inspect"]:
                return json.dumps(self.items).encode()
            raise AssertionError("unexpected Docker mutation")
        self.owner.run = run

    def test_exact_owned_resources_pass(self):
        self.owner.verify()

    def test_identity_image_mount_port_peer_and_capability_drift_refuse_before_cleanup(self):
        mutations = [lambda: self.item.update(Image="sha256:" + "d" * 64),
            lambda: self.item["Config"]["Labels"].update({drill.LABEL: "foreign"}),
            lambda: self.item["HostConfig"].update(PortBindings={"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"}]}),
            lambda: self.item["Mounts"][0].update(Name="shared-existing-volume"),
            lambda: self.item["HostConfig"].update(CapAdd=["SYS_ADMIN"]),
            lambda: self.item["NetworkSettings"]["Networks"].update({"shared-existing-network": {}}),
            lambda: self.volume.update(CreatedAt="replacement"),
            lambda: self.items.append({"Id": "f" * 64, "Mounts": copy.deepcopy(self.item["Mounts"]), "NetworkSettings": {}})]
        for mutate in mutations:
            self.setUp()
            mutate()
            with self.assertRaises(drill.DrillError):
                self.owner.cleanup()
            self.assertTrue(all(command[:2] == ["docker", "inspect"] for command in self.calls))

    def test_internal_network_identity_or_foreign_peer_prevents_cleanup(self):
        network = {"id": "b" * 64, "name": self.owner.prefix + "-internal"}
        self.owner.network = network
        original_listed = self.owner.listed
        self.owner.listed = lambda kind, owned=False: [network["id"]] if kind == "network" else original_listed(kind, owned=owned)
        inspected = {"Id": network["id"], "Name": network["name"], "Internal": True, "Driver": "bridge",
                     "Labels": {drill.LABEL: self.owner.token, drill.ROLE_LABEL: "internal"}, "Containers": {}}
        self.owner.inspect = lambda kind, target: copy.deepcopy(inspected if kind == "network" else self.volume)
        self.owner.verify()
        for value in [False, True]:
            inspected["Internal"] = value
            inspected["Containers"] = {} if not value else {"f" * 64: {}}
            with self.assertRaises(drill.DrillError):
                self.owner.cleanup()
        self.assertTrue(all(command[:2] == ["docker", "inspect"] for command in self.calls))

    def test_unknown_image_default_volume_refused_before_create(self):
        self.owner.run = lambda args, **kwargs: self.calls.append(args) or b""
        self.owner.inspect = lambda kind, target: {"Id": IMAGE, "Os": "linux",
            "Config": {"Volumes": {drill.DATA: {}, "/anonymous-extra": {}}}}
        with self.assertRaisesRegex(drill.DrillError, "anonymous"):
            self.owner.create_container("new-helper", IMAGE, "nextcloud-html", drill.DATA, "tar", ["--help"])
        self.assertFalse(self.owner.attempted_resources)
        self.assertTrue(all(command[:2] == ["docker", "ps"] for command in self.calls))

    def test_failed_ownership_prevents_start(self):
        self.item["HostConfig"]["ReadonlyRootfs"] = False
        with self.assertRaises(drill.DrillError):
            self.owner.start_database(self.item["Id"])
        self.assertTrue(all(command[:2] == ["docker", "inspect"] for command in self.calls))


class SourceNoWriteTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.RestoreTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.pin = drill.capture.sha256(self.fixture.evidence / "manifest.json")
        self.args = ["--checkpoint-dir", str(self.fixture.evidence), "--nextcloud-dir", str(self.fixture.nc),
                     "--weknora-dir", str(self.fixture.wk), "--expected-manifest-sha256", self.pin]

    def test_default_is_read_only_and_has_no_docker_calls_or_evidence_write(self):
        output = self.fixture.root / "should-not-exist"
        with (mock.patch.object(drill, "OwnedDrill") as owned, mock.patch.object(drill.capture, "local_docker") as docker,
              mock.patch("sys.stdout", new=io.StringIO()) as stdout):
            self.assertEqual(0, drill.main(self.args + ["--evidence-dir", str(output)]))
        owned.assert_not_called(); docker.assert_not_called()
        self.assertFalse(output.exists())
        self.assertFalse(json.loads(stdout.getvalue())["application_runtime_restored"])

    def test_manifest_or_payload_tamper_cannot_create_resources_even_with_apply(self):
        for tamper in ("manifest", "payload"):
            if tamper == "manifest":
                self.fixture.manifest["status"] = "INCOMPLETE"
                self.fixture.save_manifest()
            else:
                (self.fixture.evidence / "nextcloud.dump").write_bytes(b"tamper")
            with (mock.patch.object(drill, "OwnedDrill") as owned, mock.patch.object(drill.capture, "local_docker") as docker,
                  mock.patch("sys.stderr", new=io.StringIO())):
                self.assertEqual(1, drill.main(self.args + ["--apply"]))
            owned.assert_not_called(); docker.assert_not_called()


@unittest.skipUnless(os.environ.get("SHARED_COLD_DRILL_DOCKER_TEST") == "1", "opt-in tiny owned Docker volume rehearsal")
class TinyDockerRehearsal(unittest.TestCase):
    def test_all_eight_new_volumes_match_complex_archive_and_cleanup(self):
        source = fixture.RestoreTests()
        source.setUp()
        self.addCleanup(source.tearDown)
        for project in drill.plan.PROJECTS:
            for service, identity in source.manifest["projects"][project]["containers"].items():
                identity["image_id"] = IMAGE
                identity["image_tag"] = IMAGE
                source.configs[project]["services"][service]["image"] = IMAGE
            config_file = source.evidence / (drill.plan.SHORT[project] + "-resolved-compose.json")
            source.write_private(config_file, json.dumps(source.configs[project]).encode())
            source.manifest["projects"][project]["resolved_compose_sha256"] = drill.capture.sha256(config_file)
            for role in drill.plan.VOLUMES[project]:
                path = source.evidence / f"{drill.plan.SHORT[project]}-{role}.tar"
                with tarfile.open(path, "w") as archive:
                    for name, entry in sample_inventory()["entries"].items():
                        member = tarfile.TarInfo("." if not name else "./" + name)
                        member.uid, member.gid, member.mode = entry["uid"], entry["gid"], entry["mode"]
                        if entry["kind"] == "directory": member.type = tarfile.DIRTYPE
                        elif entry["kind"] == "file": member.size = 1
                        elif entry["kind"] == "symlink": member.type, member.linkname = tarfile.SYMTYPE, entry["linkname"]
                        else: member.type, member.linkname = tarfile.LNKTYPE, entry["linkname"]
                        archive.addfile(member, io.BytesIO(b"x") if entry["kind"] == "file" else None)
                path.chmod(0o600)
        source.manifest["artifact_sha256"] = {name: drill.capture.sha256(source.evidence / name) for name in drill.plan.expected_artifacts()}
        source.save_manifest()
        pin = drill.capture.sha256(source.evidence / "manifest.json")
        checked = drill.plan.verify_checkpoint(source.evidence, source.nc, source.wk, pin)
        drill.capture.local_docker(source.nc)
        destination = Path(os.environ["SHARED_COLD_DRILL_DOCKER_EVIDENCE"]).absolute()
        report = drill.apply_drill(checked, source.evidence, source.nc, destination, volumes_only=True)
        self.assertEqual("COMPLETE", report["status"])
        self.assertEqual(8, len(report["volumes"]))
        self.assertTrue(report["cleanup_verified"])
        self.assertFalse(report["database_validation_performed"])


if __name__ == "__main__":
    unittest.main()
