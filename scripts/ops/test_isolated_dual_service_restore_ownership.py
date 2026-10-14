#!/usr/bin/env python3
"""Exercise restore mutation boundaries against real fixture ownership checks."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


restore = load("dual_restore_ownership", "isolated-dual-service-restore.py")
samples = load("fixture_state_samples", "test-synthetic-ldap-fixture-state.py")


class RestoreOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="nc-restore-owner-test-")
        self.addCleanup(self.scratch.cleanup)
        self.directory = Path(self.scratch.name).resolve()
        state, compose = samples.sample(self.directory, ui=False)
        samples.write(self.directory, state, compose)
        self.state = {**state, "_compose_sha256": restore.sha256(
            self.directory / "compose.yaml")}
        self.project = state["project"]
        self.names = {
            "container": {self.project + "-wk-app-1"},
            "volume": {self.project + "_wk-data"},
            "network": {self.project + "_default"},
        }
        owner_label = restore.owner["OWNER_LABEL"]
        self.objects = {
            ("container", self.project + "-wk-app-1"): {
                "Image": state["weknora_image_id"],
                "Config": {"Labels": {"com.docker.compose.project": self.project,
                    "com.docker.compose.service": "wk-app",
                    owner_label: state["owner_token"]}}},
            ("volume", self.project + "_wk-data"): {"Labels": {
                "com.docker.compose.project": self.project,
                "com.docker.compose.volume": "wk-data",
                owner_label: state["owner_token"]}},
            ("network", self.project + "_default"): {"Labels": {
                "com.docker.compose.project": self.project,
                "com.docker.compose.network": "default",
                owner_label: state["owner_token"]}},
        }
        # Patch Docker reads beneath the actual marker/nonce validator; the
        # validator itself and the restore mutation boundary remain real.
        namespace = restore.owner["assert_owned_resources"].__globals__
        patch = mock.patch.dict(namespace, {
            "project_resources": lambda project: copy.deepcopy(self.names),
            "docker_names": lambda kind, project=None: set(self.names[kind]),
            "docker_inspect": lambda kind, name: copy.deepcopy(
                self.objects[(kind, name)]),
        })
        patch.start()
        self.addCleanup(patch.stop)

    def test_matching_owner_reaches_requested_compose_operation(self):
        with mock.patch.object(restore, "run", return_value=b"") as mutation:
            restore.compose(self.directory, self.state, "stop", "wk-app")
        self.assertEqual(1, mutation.call_count)
        self.assertEqual(["stop", "wk-app"], mutation.call_args.args[0][-2:])

    def test_same_project_labels_with_foreign_nonce_never_mutate(self):
        for kind in ("container", "volume", "network"):
            with self.subTest(kind=kind):
                key = next(key for key in self.objects if key[0] == kind)
                item = self.objects[key]
                labels = item["Config"]["Labels"] if kind == "container" else item["Labels"]
                previous = labels[restore.owner["OWNER_LABEL"]]
                labels[restore.owner["OWNER_LABEL"]] = "2" * 32
                try:
                    with mock.patch.object(restore, "run") as mutation:
                        with self.assertRaises(RuntimeError):
                            restore.compose(self.directory, self.state,
                                            "down", "--volumes")
                        mutation.assert_not_called()
                finally:
                    labels[restore.owner["OWNER_LABEL"]] = previous

    def test_changed_marker_cannot_authorize_old_state(self):
        state, compose = samples.sample(self.directory, ui=False)
        state["owner_token"] = "3" * 32
        samples.write(self.directory, state, compose)
        with mock.patch.object(restore, "run") as mutation:
            with self.assertRaises(RuntimeError):
                restore.compose(self.directory, self.state, "up", "-d")
            mutation.assert_not_called()

    def test_direct_volume_mount_requires_current_nonce_and_role(self):
        good = self.objects[("volume", self.project + "_wk-data")]
        for field, value in ((restore.owner["OWNER_LABEL"], "4" * 32),
                             ("com.docker.compose.volume", "wk-postgres")):
            item = copy.deepcopy(good)
            item["Labels"][field] = value
            with self.subTest(field=field), mock.patch.object(
                    restore, "inspect_json", return_value=item):
                with self.assertRaises(RuntimeError):
                    restore.volume_name(self.state, "wk-data")


if __name__ == "__main__":
    unittest.main()
