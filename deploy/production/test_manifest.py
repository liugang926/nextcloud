#!/usr/bin/env python3
"""Offline tests for automatic production tag publication."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('production_manifest', HERE / 'manifest.py')
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


class TagTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.repo = Path(self.folder.name)

        def git(*args):
            return subprocess.check_output(
                ['git', *args], cwd=self.repo, text=True
            ).strip()

        git('init', '-q')
        git('config', 'user.email', 'release-test@example.invalid')
        git('config', 'user.name', 'Release Test')
        git('commit', '--allow-empty', '-qm', 'first')
        self.first = git('rev-parse', 'HEAD')
        git('commit', '--allow-empty', '-qm', 'second')
        self.second = git('rev-parse', 'HEAD')

    def tearDown(self):
        self.folder.cleanup()

    def test_missing_tag_can_be_reserved_for_atomic_release(self):
        self.assertIsNone(
            manifest.verify_tag('prod-auto', self.second, True, cwd=self.repo)
        )
        with self.assertRaises(ValueError):
            manifest.verify_tag('prod-auto', self.second, False, cwd=self.repo)

    def test_existing_tag_must_match_checkout(self):
        subprocess.run(
            ['git', 'tag', 'prod-auto', self.first], cwd=self.repo, check=True
        )
        self.assertEqual(
            manifest.verify_tag('prod-auto', self.first, True, cwd=self.repo),
            self.first,
        )
        with self.assertRaises(ValueError):
            manifest.verify_tag('prod-auto', self.second, True, cwd=self.repo)


if __name__ == '__main__':
    unittest.main()
