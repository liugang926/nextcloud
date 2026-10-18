#!/usr/bin/env python3
"""Offline release guardrail and crash-recovery tests. Never starts Docker."""
import copy
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import urllib.error
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('production_release', HERE / 'release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def sample_manifest():
    sources = {'nextcloud': 'nextcloud:34.0.4-apache', 'postgres': 'postgres:16-alpine',
               'redis': 'redis:7-alpine', 'office': 'collabora/code:26.04.2.2.1',
               'proxy': 'nginx:1.30.3-alpine'}
    return {'schema': 1, 'repo': 'owner/repo', 'tag': 'prod-new', 'commit': 'a' * 40,
            'platform': 'linux/amd64', 'nextcloud_major': 34,
            'images': {key: {'source': source, 'registry_digest': source.split(':')[0] + '@sha256:' + 'b' * 64,
                             'id': 'sha256:' + 'c' * 64, 'platform': 'linux/amd64'} for key, source in sources.items()},
            'image_assets': ['images.tar.gz']}


def sample_release(tag='prod-new', published='2026-10-18T10:00:00Z'):
    return {'tag_name': tag, 'published_at': published, 'draft': False, 'prerelease': False}


class ManifestTests(unittest.TestCase):
    def validate(self, manifest=None, state=None):
        release.validate_manifest(manifest or sample_manifest(), 'owner/repo', sample_release(), 'a' * 40, state or {})

    def test_valid_manifest_and_split_parts(self):
        self.validate()
        value = sample_manifest()
        value['image_assets'] = ['images.tar.gz.part-000', 'images.tar.gz.part-001']
        self.validate(value)

    def test_reject_wrong_repo_commit_tag_arch_and_major(self):
        for key, bad in [('repo', 'evil/repo'), ('commit', 'd' * 40), ('tag', 'prod-other'),
                         ('platform', 'linux/arm64'), ('nextcloud_major', 35)]:
            value = sample_manifest()
            value[key] = bad
            with self.subTest(key=key), self.assertRaises(release.ReleaseError):
                self.validate(value)

    def test_reject_missing_digest_or_incomplete_image_parts(self):
        for bad in ('images', 'digest', 'parts'):
            value = sample_manifest()
            if bad == 'images':
                del value['images']['proxy']
            elif bad == 'digest':
                del value['images']['nextcloud']['registry_digest']
            else:
                value['image_assets'] = ['images.tar.gz.part-001']
            with self.subTest(bad=bad), self.assertRaises(release.ReleaseError):
                self.validate(value)

    def test_reject_changed_tag_commit(self):
        with self.assertRaises(release.ReleaseError):
            self.validate(state={'seen': {'prod-new': {'commit': 'd' * 40}}})

    def test_reject_changed_core_content_even_within_same_major(self):
        success = {'published_at': '2026-10-17T10:00:00Z', 'images': copy.deepcopy(sample_manifest()['images'])}
        success['images']['nextcloud']['id'] = 'sha256:' + 'd' * 64
        with self.assertRaises(release.ReleaseError):
            self.validate(state={'success': success})

    def test_reject_old_or_equal_publication(self):
        success = {'published_at': sample_release()['published_at'], 'images': sample_manifest()['images']}
        with self.assertRaises(release.ReleaseError):
            self.validate(state={'success': success})


class ArchiveTests(unittest.TestCase):
    def run_archive(self, members):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            archive = base / 'package.tar.gz'
            with tarfile.open(archive, 'w:gz') as package:
                for member in members:
                    if isinstance(member, str):
                        info = tarfile.TarInfo(member)
                        info.size = 4
                        package.addfile(info, io.BytesIO(b'test'))
                    else:
                        package.addfile(member)
            target = base / 'target'
            target.mkdir()
            release.safe_extract(archive, target)
            return sorted(str(p.relative_to(target)) for p in target.rglob('*') if p.is_file())

    def test_regular_files_only(self):
        self.assertEqual(self.run_archive(['apps/plugin/appinfo/info.xml']), ['apps/plugin/appinfo/info.xml'])

    def test_reject_path_traversal_and_unexpected_roots(self):
        for name in ('../../etc/shadow', '/etc/shadow', 'apps/../../../tmp/a', 'secrets/admin'):
            with self.subTest(name=name), self.assertRaises(release.ReleaseError):
                self.run_archive([name])

    def test_reject_links_and_devices(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE):
            member = tarfile.TarInfo('apps/evil')
            member.type = kind
            member.linkname = '/etc/shadow'
            with self.subTest(kind=kind), self.assertRaises(release.ReleaseError):
                self.run_archive([member])

    def test_reject_normalized_duplicate_names(self):
        with self.assertRaises(release.ReleaseError):
            self.run_archive(['apps/a', 'apps/./a'])

    def test_reject_expansion_before_extracting(self):
        member = tarfile.TarInfo('apps/huge')
        member.type = tarfile.DIRTYPE
        member.size = release.MAX_DEPLOYMENT + 1
        with self.assertRaises(release.ReleaseError):
            self.run_archive([member])

    def test_checksum_filenames_cannot_escape_or_duplicate(self):
        good = 'a' * 64 + '  deployment.tar.gz\n' + 'b' * 64 + '  manifest.json\n'
        self.assertEqual(set(release.parse_sums(good)), {'deployment.tar.gz', 'manifest.json'})
        for bad in (good + 'c' * 64 + '  ../outside\n', good + 'c' * 64 + '  manifest.json\n'):
            with self.assertRaises(release.ReleaseError):
                release.parse_sums(bad)


class SelectionTests(unittest.TestCase):
    def test_only_new_published_production_release(self):
        old = sample_release('prod-old', '2026-10-17T10:00:00Z')
        failed = sample_release('prod-failed', '2026-10-19T10:00:00Z')
        draft = dict(sample_release('prod-draft', '2026-10-20T10:00:00Z'), draft=True)
        prerelease = dict(sample_release('prod-pre', '2026-10-21T10:00:00Z'), prerelease=True)
        nonproduction = sample_release('v9', '2026-10-22T10:00:00Z')
        state = {'seen': {'prod-failed': {'status': 'failed'}}, 'success': {'published_at': old['published_at']}}
        self.assertEqual(release.select_release([old, failed, draft, prerelease, nonproduction, sample_release()], state)['tag_name'], 'prod-new')

    def test_no_release_is_normal_noop(self):
        self.assertIsNone(release.select_release([], {}))


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.updater = release.Updater(self.root, self.root / 'data', 'owner/repo')
        self.tx = {'tag': 'prod-new', 'commit': 'a' * 40, 'published_at': sample_release()['published_at'],
                   'previous': '/old/release', 'backup': '/old/backup', 'phase': 'changing'}

    def tearDown(self):
        self.folder.cleanup()

    def write(self):
        release.write_json(self.updater.transaction_path, self.tx)

    def test_interrupted_change_restores_once_and_retains_failed_tag(self):
        self.write()
        with mock.patch.object(self.updater, 'recover') as recover:
            self.updater.interrupted()
            self.updater.interrupted()
        recover.assert_called_once()
        self.assertEqual(self.updater.state['seen']['prod-new']['status'], 'interrupted-restored')

    def test_traffic_boundary_never_restores_database(self):
        self.tx['phase'] = 'traffic_committed'
        self.write()
        with mock.patch.object(self.updater, 'recover') as recover, self.assertRaises(release.ReleaseError):
            self.updater.interrupted()
        recover.assert_not_called()
        self.assertTrue(self.updater.transaction_path.exists())

    def test_failed_recovery_becomes_manual_and_is_not_retried(self):
        self.write()
        with mock.patch.object(self.updater, 'recover', side_effect=RuntimeError('failure')) as recover:
            for _ in range(2):
                with self.assertRaises(release.ReleaseError):
                    self.updater.interrupted()
        recover.assert_called_once()
        self.assertEqual(json.loads(self.updater.transaction_path.read_text())['phase'], 'manual-recovery')

    def test_success_journal_is_removed_without_rollback(self):
        self.write()
        self.updater.state['success'] = {'tag': 'prod-new'}
        with mock.patch.object(self.updater, 'recover') as recover:
            self.updater.interrupted()
        recover.assert_not_called()
        self.assertFalse(self.updater.transaction_path.exists())

    def test_preparation_crash_restores_old_service_before_resuming(self):
        self.tx['phase'] = 'preparing'
        self.write()
        with mock.patch.object(self.updater, 'resume_previous') as resume:
            self.updater.interrupted()
        resume.assert_called_once()
        self.assertEqual(self.updater.state['seen']['prod-new']['status'], 'interrupted-before-change')

    def test_download_retries_three_times_then_stops(self):
        with mock.patch.object(self.updater, 'api', return_value=[sample_release()]), \
             mock.patch.object(self.updater, 'apply', side_effect=urllib.error.URLError('offline')) as apply:
            for _ in range(3):
                with self.assertRaises(Exception):
                    self.updater.check()
            self.updater.check()
        self.assertEqual(apply.call_count, 3)
        self.assertEqual(self.updater.state['seen']['prod-new']['status'], 'rejected')

    def test_tag_is_immutable_even_after_transient_download_error(self):
        with mock.patch.object(self.updater, 'api', return_value={'object': {'type': 'commit', 'sha': 'a' * 40}}):
            self.assertEqual(self.updater.tag_commit('prod-new'), 'a' * 40)
        with mock.patch.object(self.updater, 'api', return_value={'object': {'type': 'commit', 'sha': 'b' * 40}}):
            with self.assertRaises(release.ReleaseError):
                self.updater.tag_commit('prod-new')


if __name__ == '__main__':
    unittest.main(verbosity=2)
