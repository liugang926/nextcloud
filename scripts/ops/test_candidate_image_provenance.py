#!/usr/bin/env python3
"""Reject wrong or incomplete build provenance without invoking Docker."""
import copy
import importlib.util
from pathlib import Path
import unittest

module_path = Path(__file__).with_name('inspect-weknora-candidate-images.py')
spec = importlib.util.spec_from_file_location('candidate_image', module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CandidateImageProvenance(unittest.TestCase):
    def setUp(self):
        self.entry = {'base': 'a' * 40, 'candidate_commit': 'b' * 40,
                      'candidate_tree': 'c' * 40, 'patch_sha256': 'd' * 64}
        self.image = {'Id': 'sha256:' + 'e' * 64, 'Config': {'Labels': {
            'org.opencontainers.image.revision': self.entry['base'],
            'io.github.liugang926.weknora.nextcloud-patch-sha256': self.entry['patch_sha256'],
            'io.github.liugang926.weknora.candidate-tree': self.entry['candidate_tree'],
            'io.github.liugang926.weknora.candidate-commit': self.entry['candidate_commit'],
        }}}

    def test_pinned_pair_member_keeps_immutable_id(self):
        self.assertEqual(module.check_image(self.image, self.entry)['image_id'], self.image['Id'])

    def test_an_old_default_build_cannot_pass_as_new_candidate(self):
        old = copy.deepcopy(self.image)
        labels = old['Config']['Labels']
        del labels['io.github.liugang926.weknora.candidate-tree']
        del labels['io.github.liugang926.weknora.candidate-commit']
        with self.assertRaises(ValueError):
            module.check_image(old, self.entry)

    def test_changed_manifest_rejects_the_existing_image(self):
        for field in ('base', 'candidate_commit', 'candidate_tree', 'patch_sha256'):
            with self.subTest(field=field):
                changed = dict(self.entry, **{field: 'f' * len(self.entry[field])})
                with self.assertRaises(ValueError):
                    module.check_image(self.image, changed)

    def test_absent_or_invalid_immutable_identity_is_refused(self):
        for identity in ('', 'latest', 'sha256:' + 'z' * 64):
            with self.subTest(identity=identity):
                invalid = dict(self.image, Id=identity)
                with self.assertRaises(ValueError):
                    module.check_image(invalid, self.entry)


if __name__ == '__main__':
    unittest.main()
