#!/usr/bin/env python3
"""Offline fail-closed checks for synthetic fixture state/Compose bindings."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "synthetic_ldap_fixture", Path(__file__).with_name("synthetic-ldap-fixture.py"))
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


def sample(directory, *, ui):
    project = "nc-synldap-a1b2c3d4"
    ports = {"nextcloud": 50001, "weknora": 50002, "ldap": 50003}
    if ui:
        ports["weknora_ui"] = 50004
    services = {name: {} for name in fixture.SERVICES}
    for name, key, target in (("nextcloud", "nextcloud", 80),
                              ("wk-app", "weknora", 8080),
                              ("openldap", "ldap", 1636)):
        services[name]["ports"] = [f"127.0.0.1:{ports[key]}:{target}"]
    services["wk-app"]["image"] = "synthetic-backend:test"
    if ui:
        services["wk-ui"] = {"image": "synthetic-ui:test",
                             "ports": [f"127.0.0.1:{ports['weknora_ui']}:80"]}
    compose = {"name": project, "services": services,
               "volumes": {name: {} for name in fixture.VOLUMES}}
    state = {"marker": fixture.MARKER, "project": project,
             "scratch_dir": str(directory), "owner_token": "1" * 32,
             "ports": ports, "weknora_image": "synthetic-backend:test",
             "weknora_image_id": "sha256:" + "a" * 64}
    if ui:
        state.update({"weknora_ui_image": "synthetic-ui:test",
                      "weknora_ui_image_id": "sha256:" + "b" * 64})
    state["compose_fingerprint"] = fixture.compose_fingerprint(compose)
    return state, compose


def write(directory, state, compose):
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (directory / "compose.yaml").write_text(json.dumps(compose), encoding="utf-8")


class FixtureStateTest(unittest.TestCase):
    def check_mutation(self, *, ui, mutate):
        with tempfile.TemporaryDirectory(prefix="nc-fixture-state-test-") as scratch:
            directory = Path(scratch).resolve()
            state, compose = sample(directory, ui=ui)
            mutate(state, compose)
            write(directory, state, compose)
            with self.assertRaises(RuntimeError):
                fixture.owned_state(directory)

    def test_matching_state_is_accepted_with_and_without_ui(self):
        for ui in (False, True):
            with self.subTest(ui=ui), tempfile.TemporaryDirectory(
                    prefix="nc-fixture-state-test-") as scratch:
                directory = Path(scratch).resolve()
                state, compose = sample(directory, ui=ui)
                write(directory, state, compose)
                self.assertEqual(fixture.owned_state(directory)[1], state)

    def body_sample(self, directory):
        state, compose = sample(directory, ui=True)
        state['body_journal'] = True
        compose['volumes'].update({name: {} for name in fixture.BODY_VOLUMES})
        app = compose['services']['wk-app']
        app['volumes'] = ['wk-body-journal:/var/lib/weknora-body-ledger',
                          'wk-body-key:/var/lib/weknora-body-key',
                          'wk-body-pin:/var/lib/weknora-body-pin']
        app['environment'] = {
            'WEKNORA_ORIGINAL_BODY_JOURNAL_DIRECTORY': '/var/lib/weknora-body-ledger',
            'WEKNORA_ORIGINAL_BODY_JOURNAL_KEY': '/var/lib/weknora-body-key/hmac.key',
            'WEKNORA_ORIGINAL_BODY_JOURNAL_PIN': '/var/lib/weknora-body-pin/pin.json',
        }
        state['compose_fingerprint'] = fixture.compose_fingerprint(compose)
        return state, compose

    def test_body_anchor_requires_three_exact_owned_mounts(self):
        for mutation in ('none', 'db-volume', 'extra-shadow', 'wrong-key', 'hidden-mode'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix='nc-fixture-body-') as scratch:
                directory = Path(scratch).resolve()
                state, compose = self.body_sample(directory)
                app = compose['services']['wk-app']
                if mutation == 'db-volume':
                    app['volumes'][0] = 'wk-postgres:/var/lib/weknora-body-ledger'
                elif mutation == 'extra-shadow':
                    app['volumes'].append('/tmp/other:/var/lib/weknora-body-pin')
                elif mutation == 'wrong-key':
                    app['environment']['WEKNORA_ORIGINAL_BODY_JOURNAL_KEY'] = '/data/files/key'
                elif mutation == 'hidden-mode':
                    state.pop('body_journal')
                state['compose_fingerprint'] = fixture.compose_fingerprint(compose)
                write(directory, state, compose)
                if mutation == 'none':
                    self.assertTrue(fixture.owned_state(directory)[1]['body_journal'])
                else:
                    with self.assertRaises(RuntimeError):
                        fixture.owned_state(directory)

    def test_changed_state_ports_images_and_ids_are_rejected(self):
        for ui in (False, True):
            for port in ("nextcloud", "weknora", "ldap") + (("weknora_ui",) if ui else ()):
                with self.subTest(ui=ui, port=port):
                    self.check_mutation(ui=ui, mutate=lambda s, c, key=port:
                                        s["ports"].__setitem__(key, 51000))
            with self.subTest(ui=ui, field="backend image"):
                self.check_mutation(ui=ui, mutate=lambda s, c:
                                    s.__setitem__("weknora_image", "other-backend:test"))
            with self.subTest(ui=ui, field="backend ID"):
                self.check_mutation(ui=ui, mutate=lambda s, c:
                                    s.__setitem__("weknora_image_id", "invalid"))
        for field, value in (("weknora_ui_image", "other-ui:test"),
                             ("weknora_ui_image_id", "invalid")):
            with self.subTest(field=field):
                self.check_mutation(ui=True, mutate=lambda s, c, k=field, v=value:
                                    s.__setitem__(k, v))
        self.check_mutation(ui=False, mutate=lambda s, c:
                            s["ports"].__setitem__("weknora_ui", 50004))
        self.check_mutation(ui=False, mutate=lambda s, c:
                            s.__setitem__("weknora_ui_image_id", "sha256:" + "b" * 64))

    def test_changed_compose_target_is_rejected_with_matching_fingerprint(self):
        for service, field, value in (("nextcloud", "ports", ["0.0.0.0:50001:80"]),
                                      ("wk-app", "image", "other-backend:test"),
                                      ("wk-ui", "ports", ["127.0.0.1:50005:80"]),
                                      ("wk-ui", "image", "other-ui:test")):
            with self.subTest(service=service, field=field):
                def mutate(state, compose):
                    compose["services"][service][field] = value
                    state["compose_fingerprint"] = fixture.compose_fingerprint(compose)
                self.check_mutation(ui=True, mutate=mutate)


if __name__ == "__main__":
    unittest.main()
