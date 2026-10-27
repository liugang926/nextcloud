#!/usr/bin/env python3
"""Offline release guardrail and crash-recovery tests. Never starts Docker."""
import copy
import gzip
import hashlib
import http.client
import importlib.util
import io
import json
import os
import shutil
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
    sources = {'nextcloud': 'ghcr.io/liugang926/nextcloud-production-core:34.0.4-php8.5.12-r1',
               'postgres': 'postgres:16-alpine',
               'redis': 'redis:7-alpine', 'office': 'collabora/code:26.04.2.4.1',
               'proxy': 'nginx:1.30.3-alpine'}
    return {'schema': 1, 'repo': 'owner/repo', 'tag': 'prod-new', 'commit': 'a' * 40,
            'platform': 'linux/amd64', 'nextcloud_major': 34,
            'images': {key: {'source': source, 'registry_digest': source.split(':')[0] + '@sha256:' + 'b' * 64,
                             'id': 'sha256:' + 'c' * 64, 'platform': 'linux/amd64'} for key, source in sources.items()},
            'image_assets': ['images.tar.gz'], 'core_update': release.NEXTCLOUD_CORE_UPDATE}


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

    def test_accept_reviewed_php_security_core_transition(self):
        success = {'published_at': '2026-10-17T10:00:00Z', 'images': copy.deepcopy(sample_manifest()['images'])}
        success['images']['nextcloud']['source'] = 'nextcloud:34.0.4-apache'
        success['images']['nextcloud']['id'] = 'sha256:' + 'd' * 64
        self.validate(state={'success': success})

    def test_reject_altered_security_transition_metadata(self):
        for field, value in (
            ('to_php', '8.5.11'),
            ('nextcloud_version', '34.0.5'),
            ('dockerfile_commit', 'f' * 40),
        ):
            value_manifest = sample_manifest()
            value_manifest['core_update'] = copy.deepcopy(value_manifest['core_update'])
            value_manifest['core_update'][field] = value
            with self.subTest(field=field), self.assertRaises(release.ReleaseError):
                self.validate(value_manifest)

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

    def test_runtime_directories_are_readable_with_private_updater_umask(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            archive = base / 'package.tar.gz'
            with tarfile.open(archive, 'w:gz') as package:
                for name in ('apps/plugin/lib/Service.php', 'scripts/ops/worker.sh'):
                    info = tarfile.TarInfo(name)
                    info.size = 4
                    package.addfile(info, io.BytesIO(b'test'))
            target = base / 'release'
            target.mkdir(mode=0o700)
            private_umask = os.umask(0o077)
            try:
                release.safe_extract(archive, target)
            finally:
                os.umask(private_umask)
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            for directory in ('apps', 'apps/plugin', 'apps/plugin/lib', 'scripts', 'scripts/ops'):
                self.assertEqual((target / directory).stat().st_mode & 0o777, 0o755)
            self.assertEqual((target / 'apps/plugin/lib/Service.php').stat().st_mode & 0o777, 0o644)

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


class ApiTests(unittest.TestCase):
    class Response:
        def __init__(self, payload, declared=None):
            self.payload = payload
            self.url = "https://github.com/owner/repo/releases/download/prod-new/manifest.json"
            self.headers = {'Content-Length': str(len(payload) if declared is None else declared)}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return self.payload

    def test_incomplete_release_api_reply_is_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            updater = release.Updater(folder, Path(folder) / 'data', 'owner/repo')
        with mock.patch.object(release.urllib.request, 'urlopen',
                               side_effect=[http.client.IncompleteRead(b'', 2),
                                            self.Response(b'[]')]), \
             mock.patch.object(release.time, 'sleep'):
            self.assertEqual(updater.api('releases?per_page=30'), [])

    def test_truncated_release_api_payload_is_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            updater = release.Updater(folder, Path(folder) / 'data', 'owner/repo')
        with mock.patch.object(release.urllib.request, 'urlopen',
                               side_effect=[self.Response(b'[]', declared=4),
                                            self.Response(b'[]')]), \
             mock.patch.object(release.time, 'sleep'):
            self.assertEqual(updater.api('releases?per_page=30'), [])


    def test_small_asset_transport_failure_is_retried(self):
        folder = tempfile.mkdtemp()
        try:
            updater = release.Updater(folder, Path(folder) / 'data', 'owner/repo')
            target = Path(folder) / 'manifest.json'
            asset = {'state': 'uploaded', 'size': 2,
                     'browser_download_url': 'https://github.com/owner/repo/releases/download/prod-new/manifest.json'}
            with mock.patch.object(updater, 'validate_asset', return_value=asset), \
                 mock.patch.object(release.urllib.request, 'urlopen',
                                   side_effect=[ConnectionResetError(), self.Response(b'[]')]), \
                 mock.patch.object(release.time, 'sleep'):
                updater.download({}, 'prod-new', 'manifest.json', target)
            self.assertEqual(target.read_bytes(), b'[]')
        finally:
            shutil.rmtree(folder)


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

    def test_range_transport_failure_uses_bounded_network_retry(self):
        with mock.patch.object(self.updater, 'api', return_value=[sample_release()]), \
             mock.patch.object(self.updater, 'apply', side_effect=release.DownloadError('bounded network failure')):
            with self.assertRaises(release.ReleaseError):
                self.updater.check()
        self.assertEqual(self.updater.state['download_attempts']['prod-new'], 1)
        self.assertNotIn('prod-new', self.updater.state['seen'])


class DownloadCacheTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.updater = release.Updater(self.root / 'deployment', self.root / 'data', 'owner/repo')
        self.size = 32 * 1024 * 1024 + 1
        self.expected = hashlib.sha256(b'\0' * self.size).hexdigest()
        self.asset = {'state': 'uploaded', 'size': self.size,
                      'browser_download_url': 'https://github.com/owner/repo/releases/download/prod-new/images.tar.gz'}

    def tearDown(self):
        self.folder.cleanup()

    @staticmethod
    def sparse_download(url, destination, size, **kwargs):
        with destination.open('wb') as output:
            output.truncate(size)

    def test_complete_asset_is_verified_before_copying_from_persistent_cache(self):
        target = self.root / 'images.tar.gz'
        with mock.patch.object(release, 'download_parallel', side_effect=self.sparse_download):
            self.updater.download({'images.tar.gz': self.asset}, 'prod-new', 'images.tar.gz', target,
                                  expected_sha256=self.expected)
        self.assertEqual(release.digest(target), self.expected)
        self.assertTrue(list((self.updater.state_dir / 'asset-cache/prod-new').glob('*/images.tar.gz')))

    def test_bad_complete_asset_is_never_copied_and_cannot_seed_the_next_attempt(self):
        target = self.root / 'images.tar.gz'
        with mock.patch.object(release, 'download_parallel', side_effect=self.sparse_download):
            with self.assertRaises(release.ReleaseError):
                self.updater.download({'images.tar.gz': self.asset}, 'prod-new', 'images.tar.gz', target,
                                      expected_sha256='1' * 64)
        self.assertFalse(target.exists())
        self.assertEqual(list((self.updater.state_dir / 'asset-cache/prod-new').iterdir()), [])


class DatabaseRoleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.updater = release.Updater(self.root / 'deployment', self.root / 'data', 'owner/repo')
        self.backup = self.root / 'checkpoint'
        self.backup.mkdir()
        with tarfile.open(self.backup / 'config-customapps.tar', 'w:') as archive:
            content = b'<?php $CONFIG=["dbuser"=>"oc_admin","dbpassword"=>"private-value"];'
            member = tarfile.TarInfo('config/config.php')
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        (self.backup / 'SHA256SUMS').write_text('')

    def tearDown(self):
        self.folder.cleanup()

    def test_legacy_checkpoint_uses_its_protected_config_and_existing_login_role(self):
        def inspect_config(directory):
            config = directory / 'config.php'
            self.assertTrue(config.is_file())
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            return 'oc_admin'
        with mock.patch.object(self.updater, 'config_database_role', side_effect=inspect_config), \
             mock.patch.object(self.updater, 'validate_database_role') as validate:
            self.assertEqual(self.updater.backup_database_role(self.backup), 'oc_admin')
        validate.assert_called_once_with('oc_admin')
        self.assertEqual(list(self.updater.state_dir.glob('database-role-*')), [])

    def test_metadata_must_match_config_and_be_checksummed(self):
        role_file = self.backup / 'database-role.json'
        for metadata, checksummed in [({'schema': 1, 'app_role': 'other_role'}, True),
                                      ({'schema': 1, 'app_role': 'oc_admin'}, False)]:
            release.write_json(role_file, metadata)
            (self.backup / 'SHA256SUMS').write_text(('a' * 64 + '  database-role.json\n') if checksummed else '')
            with self.subTest(metadata=metadata, checksummed=checksummed), \
                 mock.patch.object(self.updater, 'config_database_role', return_value='oc_admin'), \
                 mock.patch.object(self.updater, 'validate_database_role') as validate, \
                 self.assertRaises(release.ReleaseError):
                self.updater.backup_database_role(self.backup)
            validate.assert_not_called()

    def test_new_checkpoint_records_only_validated_application_role(self):
        with mock.patch.object(self.updater, 'config_database_role', return_value='oc_admin'), \
             mock.patch.object(self.updater, 'validate_database_role') as validate:
            self.updater.record_database_role(self.backup)
        validate.assert_called_once_with('oc_admin')
        self.assertEqual(json.loads((self.backup / 'database-role.json').read_text()),
                         {'schema': 1, 'app_role': 'oc_admin'})
        self.assertEqual((self.backup / 'database-role.json').stat().st_mode & 0o777, 0o600)

    def test_invalid_roles_are_rejected_before_any_database_command(self):
        for role in ('bad-role', 'role"; DROP DATABASE postgres;--', '', 'a' * 64):
            with self.subTest(role=role), mock.patch.object(self.updater, 'compose') as compose, \
                 self.assertRaises(release.ReleaseError):
                self.updater.validate_database_role(role)
            compose.assert_not_called()

    def test_non_login_role_is_rejected(self):
        with mock.patch.object(self.updater, 'compose', return_value=b'0\n'), self.assertRaises(release.ReleaseError):
            self.updater.validate_database_role('oc_admin')

    def test_config_reader_is_isolated_and_sanitizes_errors(self):
        configuration = json.dumps({'services': {'nextcloud': {'image': 'sha256:' + 'a' * 64}}}).encode()
        with mock.patch.object(self.updater, 'compose', return_value=configuration), \
             mock.patch.object(release.subprocess, 'check_output', return_value=b'{"dbuser":"oc_admin"}') as reader:
            self.assertEqual(self.updater.config_database_role(self.root), 'oc_admin')
        command = reader.call_args.args[0]
        self.assertIn('--network=none', command)
        self.assertIn('--read-only', command)
        self.assertIn('--memory=64m', command)
        self.assertNotIn('dbpassword', command[-1])
        with mock.patch.object(self.updater, 'compose', return_value=configuration), \
             mock.patch.object(release.subprocess, 'check_output', side_effect=release.subprocess.CalledProcessError(
                 1, ['php'], output='private-value', stderr='private-value')):
            with self.assertRaises(release.ReleaseError) as error:
                self.updater.config_database_role(self.root)
        self.assertNotIn('private-value', str(error.exception))


class ImageReuseTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.updater = release.Updater(self.root / 'deployment', self.root / 'data', 'owner/repo')
        (self.updater.root / 'secrets').mkdir()
        self.manifest = sample_manifest()
        for index, image in enumerate(self.manifest['images'].values(), start=1):
            image['id'] = 'sha256:' + f'{index:064x}'
        self.local_ids = {image['id'] for image in self.manifest['images'].values()}
        self.runtime_ids = {name: image['id'] for name, image in self.manifest['images'].items()}
        self.loaded = False
        self.wrong_arch = False
        self.inspect_error = False
        deployment = io.BytesIO()
        with tarfile.open(fileobj=deployment, mode='w:gz') as archive:
            for name in ('compose.yaml', 'bootstrap.sh', 'healthcheck.sh', 'release.py', 'backup.sh', 'update.sh', 'parallel_download.py'):
                content = b'offline staging fixture\n'
                info = tarfile.TarInfo('deploy/production/' + name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        self.contents = {'deployment.tar.gz': deployment.getvalue(),
                         'manifest.json': json.dumps(self.manifest).encode(),
                         'images.tar.gz': b'archive passed to mocked Docker loader'}
        self.contents['SHA256SUMS'] = ''.join(
            hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
            for name, data in sorted(self.contents.items())).encode()
        self.candidate = dict(sample_release(), assets=[
            {'name': name, 'state': 'uploaded', 'size': len(data),
             'browser_download_url': 'https://github.com/owner/repo/releases/download/prod-new/' + name,
             'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}
            for name, data in self.contents.items()])

    def tearDown(self):
        self.folder.cleanup()

    def inspect(self, command, **kwargs):
        if command[:3] == ['docker', 'image', 'ls']:
            return '\n'.join(json.dumps({'ID': runtime_id,
                                         'Repository': self.manifest['images'][name]['source'].rsplit(':', 1)[0],
                                         'Tag': self.manifest['images'][name]['source'].rsplit(':', 1)[1]})
                             for name, runtime_id in self.runtime_ids.items() if runtime_id in self.local_ids)
        self.assertEqual(command[:3], ['docker', 'image', 'inspect'])
        if self.inspect_error:
            raise release.subprocess.CalledProcessError(1, command, stderr='daemon unavailable')
        image_id = command[3]
        self.assertTrue(self.loaded or image_id in self.local_ids)
        return json.dumps([{'Id': image_id, 'Os': 'linux',
                            'Architecture': 'arm64' if self.wrong_arch else 'amd64'}])

    def download(self, assets, tag, name, target, *args, **kwargs):
        target.write_bytes(self.contents[name])

    def run_command(self, command, **kwargs):
        if command[:2] == ['docker', 'load']:
            self.assertEqual(Path(command[3]).read_bytes(), self.contents['images.tar.gz'])
            self.loaded = True
            self.local_ids.update(self.runtime_ids.values())

    def apply(self):
        with mock.patch.object(self.updater, 'tag_commit', return_value='a' * 40), \
             mock.patch.object(self.updater, 'ancestor') as ancestor, \
             mock.patch.object(self.updater, 'download', side_effect=self.download) as download, \
             mock.patch.object(self.updater, 'run', side_effect=self.run_command) as run, \
             mock.patch.object(self.updater, 'compose'), \
             mock.patch.object(release.subprocess, 'check_output', side_effect=self.inspect):
            self.updater.apply(self.candidate)
        ancestor.assert_called_once_with('a' * 40, 'main')
        return download, run

    def test_all_images_present_skips_image_download_assembly_and_load(self):
        download, run = self.apply()
        self.assertEqual({call.args[2] for call in download.call_args_list},
                         {'SHA256SUMS', 'manifest.json', 'deployment.tar.gz'})
        self.assertFalse(any(call.args[0][:2] == ['docker', 'load'] for call in run.call_args_list))
        self.assertFalse(self.loaded)
        self.assertEqual(self.updater.state['seen']['prod-new']['status'], 'success')

    def test_any_missing_image_downloads_and_loads_complete_archive(self):
        self.local_ids.remove(self.manifest['images']['redis']['id'])
        download, run = self.apply()
        self.assertIn('images.tar.gz', {call.args[2] for call in download.call_args_list})
        self.assertEqual(sum(call.args[0][:2] == ['docker', 'load'] for call in run.call_args_list), 1)
        self.assertTrue(self.loaded)
        self.assertEqual(self.updater.state['seen']['prod-new']['status'], 'success')

    def test_containerd_runtime_ids_are_resolved_but_release_config_ids_remain_pinned(self):
        self.runtime_ids = {name: 'sha256:' + f'{index + 100:064x}'
                            for index, name in enumerate(self.manifest['images'])}
        self.local_ids = set(self.runtime_ids.values())

        def verify(expected, inspected):
            name = next(name for name, image in self.manifest['images'].items() if image['id'] == expected['id'])
            self.assertEqual(inspected['Id'], self.runtime_ids[name])
            return inspected['Id']

        with mock.patch.object(release, 'verify_image', side_effect=verify):
            download, run = self.apply()
        self.assertNotIn('images.tar.gz', {call.args[2] for call in download.call_args_list})
        environment = dict(line.split('=', 1) for line in self.updater.env_path.read_text().splitlines())
        for name, key in release.IMAGE_KEYS.items():
            self.assertEqual(environment[key], self.runtime_ids[name])
            self.assertEqual(self.updater.state['success']['images'][name]['id'], self.manifest['images'][name]['id'])
        self.assertEqual(self.updater.state['success']['runtime_images'], self.runtime_ids)
        # This fixture contains the range downloader but predates identity.py.
        # Activating it must retain the installed compatible updater programs.
        self.assertFalse((self.updater.root / 'bin/release.py').exists())

    def test_source_tag_with_another_verified_config_requires_archive_import(self):
        original_verify = release.verify_image
        calls = 0

        def verify(expected, inspected):
            nonlocal calls
            if expected['source'] == 'redis:7-alpine' and not self.loaded:
                calls += 1
                raise release.ConfigMismatch('Verified local image has a different config digest')
            return original_verify(expected, inspected)

        # Only a source tag locates this alternative candidate; its config ID
        # is not the approved config or a cached mapping for the approved one.
        self.runtime_ids['redis'] = 'sha256:' + 'f' * 64
        self.local_ids.remove(self.manifest['images']['redis']['id'])
        self.local_ids.add(self.runtime_ids['redis'])

        original_run = self.run_command

        def import_images(command, **kwargs):
            if command[:2] == ['docker', 'load']:
                self.runtime_ids['redis'] = self.manifest['images']['redis']['id']
            original_run(command, **kwargs)

        with mock.patch.object(release, 'verify_image', side_effect=verify), \
             mock.patch.object(self, 'run_command', side_effect=import_images):
            download, _ = self.apply()
        self.assertEqual(calls, 1)
        self.assertTrue(self.loaded)
        self.assertIn('images.tar.gz', {call.args[2] for call in download.call_args_list})


    def test_existing_wrong_architecture_is_rejected_without_downloading(self):
        self.wrong_arch = True
        with self.assertRaisesRegex(release.ReleaseError, 'content ID/platform'):
            self.apply()
        self.assertFalse(self.loaded)
        self.assertFalse(self.updater.transaction_path.exists())

    def test_inspect_error_is_rejected_instead_of_treated_as_missing(self):
        self.inspect_error = True
        with self.assertRaisesRegex(release.ReleaseError, 'Cannot inspect'):
            self.apply()
        self.assertFalse(self.loaded)
        self.assertFalse(self.updater.transaction_path.exists())

    def test_skipped_image_asset_metadata_is_still_validated(self):
        image_asset = next(asset for asset in self.candidate['assets'] if asset['name'] == 'images.tar.gz')
        for field, invalid in [('size', release.MAX_ASSET + 1), ('size', 0), ('state', 'pending'),
                               ('browser_download_url', 'https://evil.example/image'), ('digest', 'sha256:' + 'f' * 64)]:
            original = image_asset[field]
            image_asset[field] = invalid
            try:
                with self.subTest(field=field, value=invalid), self.assertRaises(release.ReleaseError):
                    self.apply()
            finally:
                image_asset[field] = original
        self.assertFalse(self.loaded)
        self.assertFalse(self.updater.transaction_path.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
