#!/usr/bin/env python3
"""Read-only public GitHub release updater. Run through update.sh (flock)."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request

TAG = re.compile(r'prod-[A-Za-z0-9][A-Za-z0-9._-]{0,100}\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')
ASSET = re.compile(r'(?:deployment\.tar\.gz|manifest\.json|images\.tar\.gz(?:\.part-[0-9]{3})?)\Z')
IMAGE_KEYS = {'nextcloud': 'NEXTCLOUD_IMAGE', 'postgres': 'POSTGRES_IMAGE',
              'redis': 'REDIS_IMAGE', 'office': 'OFFICE_IMAGE', 'proxy': 'PROXY_IMAGE'}
WORKERS = ['cron', 'event-worker', 'event-status-worker',
           'directory-discovery-worker', 'stage-status-worker']
MAX_ASSET = 1800 * 1024 * 1024
MAX_DEPLOYMENT = 512 * 1024 * 1024
MAX_BUNDLE = 16 * 1024 * 1024 * 1024


class ReleaseError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def timestamp(value):
    require(isinstance(value, str), 'Missing published_at')
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def digest(path):
    result = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    temp = path.with_name(path.name + '.new')
    with temp.open('w') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temp.chmod(mode)
    temp.replace(path)


def write_json(path, data):
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + '\n')


def safe_extract(archive, target):
    """No links/devices, traversal, duplicates, or unbounded uncompressed payload."""
    with tarfile.open(archive, 'r:gz') as source:
        names, total = set(), 0
        for member in source:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and path.parts and '..' not in path.parts,
                    'Unsafe deployment path')
            require(path.parts[0] in {'apps', 'scripts', 'deploy'}, 'Unexpected deployment root')
            require(member.isfile() or member.isdir(), 'Deployment links/devices are forbidden')
            normalized = path.as_posix()
            require(normalized not in names, 'Duplicate deployment path')
            names.add(normalized)
            require(len(names) <= 50000, 'Deployment has too many files')
            total += member.size
            require(total <= MAX_DEPLOYMENT, 'Deployment uncompressed size exceeded')
            require(not (target / member.name).is_symlink(), 'Symlink at deployment target')
    # Validate the complete directory before creating any file. Re-open to
    # stream the extraction without retaining an unbounded TarInfo list.
    def runtime_directory(path):
        path.mkdir(parents=True, exist_ok=True)
        # The updater deliberately uses umask 077 for secrets/state. Runtime
        # source is bind-mounted into containers running as unprivileged users;
        # both explicit and implicit archive directories must be traversable.
        while path != target:
            path.chmod(0o755)
            path = path.parent

    with tarfile.open(archive, 'r:gz') as source:
        for member in source:
            path = target / member.name
            if member.isdir():
                runtime_directory(path)
            else:
                runtime_directory(path.parent)
                with source.extractfile(member) as src, path.open('wb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                path.chmod(0o755 if member.mode & 0o111 else 0o644)


def parse_sums(text):
    checksums = {}
    for line in text.splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        require(match is not None, 'Invalid SHA256SUMS line')
        value, name = match.groups()
        require(ASSET.fullmatch(name) and name not in checksums, 'Unsafe/duplicate checksum filename')
        checksums[name] = value
    require({'deployment.tar.gz', 'manifest.json'} <= set(checksums), 'Required checksum missing')
    return checksums


def validate_manifest(manifest, repo, release, commit, state):
    tag = release['tag_name']
    require(manifest.get('schema') == 1, 'Unknown manifest schema')
    require(manifest.get('repo') == repo and manifest.get('tag') == tag,
            'Manifest repository/tag mismatch')
    require(SHA.fullmatch(commit) and manifest.get('commit') == commit, 'Manifest commit mismatch')
    require(manifest.get('platform') == 'linux/amd64' and manifest.get('nextcloud_major') == 34,
            'Only linux/amd64 and Nextcloud 34 are allowed')
    known = state.get('seen', {}).get(tag)
    require(not known or known['commit'] == commit, 'Tag commit was changed after first observation')
    success = state.get('success')
    require(not success or timestamp(release['published_at']) > timestamp(success['published_at']),
            'Refusing release downgrade by publication time')
    images = manifest.get('images', {})
    require(set(images) == set(IMAGE_KEYS), 'Unexpected image set')
    for name, image in images.items():
        require(image.get('platform') == 'linux/amd64' and DIGEST.fullmatch(image.get('id', '')),
                'Invalid image content ID/platform: ' + name)
        require(re.fullmatch(r'[a-z0-9./:_-]+@sha256:[0-9a-f]{64}', image.get('registry_digest', '')),
                'Image registry digest is missing: ' + name)
    require(images['nextcloud'].get('source') == 'nextcloud:34.0.4-apache', 'Nextcloud core release changed')
    require(images['postgres'].get('source') == 'postgres:16-alpine', 'PostgreSQL major changed')
    require(images['redis'].get('source') == 'redis:7-alpine', 'Redis major changed')
    require(images['office'].get('source') == 'collabora/code:26.04.2.4.1', 'Office release changed')
    require(images['proxy'].get('source') == 'nginx:1.30.3-alpine', 'Proxy release changed')
    # Automatic releases deploy this repository's plugin. A core-image change
    # requires a separately planned core upgrade; its filesystem cannot safely
    # be downgraded after a failed database migration.
    require(not success or success['images']['nextcloud']['id'] == images['nextcloud']['id'],
            'Nextcloud core image changed: manual core upgrade required')
    assets = manifest.get('image_assets', [])
    require(isinstance(assets, list) and 0 < len(assets) <= 20, 'Invalid image asset list')
    require(assets == ['images.tar.gz'] or assets == [f'images.tar.gz.part-{i:03d}' for i in range(len(assets))],
            'Image parts must be contiguous and ordered')


def select_release(releases, state):
    candidates = []
    for release in releases:
        tag = release.get('tag_name', '')
        if release.get('draft') or release.get('prerelease') or not TAG.fullmatch(tag):
            continue
        if tag in state.get('seen', {}):
            continue  # Failed tags require a new reviewed prod tag; no retry loop.
        if state.get('success') and timestamp(release['published_at']) <= timestamp(state['success']['published_at']):
            continue
        candidates.append(release)
    return max(candidates, key=lambda item: (timestamp(item['published_at']), item['tag_name'])) if candidates else None


class Updater:
    def __init__(self, root, data, repo):
        self.root, self.data, self.repo = Path(root), Path(data), repo
        require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo), 'Invalid repository')
        self.state_dir = self.root / 'state'
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / 'release-state.json'
        self.transaction_path = self.state_dir / 'transaction.json'
        self.env_path = self.root / 'secrets/images.env'
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {'seen': {}}
        self.env = dict(os.environ, NEXTCLOUD_PRODUCTION_ROOT=str(self.root),
                        NEXTCLOUD_DATA_ROOT=str(self.data), NEXTCLOUD_IMAGES_ENV=str(self.env_path))

    def api(self, path):
        request = urllib.request.Request('https://api.github.com/repos/' + self.repo + '/' + path,
                                         headers={'Accept': 'application/vnd.github+json',
                                                  'X-GitHub-Api-Version': '2022-11-28',
                                                  'User-Agent': 'nextcloud-production-release/1'})
        with urllib.request.urlopen(request, timeout=60) as response:
            require(int(response.headers.get('Content-Length', '0')) < 8 * 1024 * 1024, 'API reply too large')
            payload = response.read(8 * 1024 * 1024 + 1)
        require(len(payload) <= 8 * 1024 * 1024, 'API reply too large')
        return json.loads(payload)

    def tag_commit(self, tag):
        obj = self.api('git/ref/tags/' + urllib.parse.quote(tag, safe=''))['object']
        for _ in range(5):
            if obj['type'] == 'commit':
                require(SHA.fullmatch(obj['sha']), 'Invalid tag commit')
                observed = self.state.setdefault('observed_commits', {})
                require(tag not in observed or observed[tag] == obj['sha'], 'Tag commit changed after first observation')
                observed[tag] = obj['sha']
                write_json(self.state_path, self.state)
                return obj['sha']
            require(obj['type'] == 'tag', 'Tag does not resolve to commit')
            obj = self.api('git/tags/' + obj['sha'])['object']
        raise ReleaseError('Too many annotated tag indirections')

    def ancestor(self, before, after):
        compare = self.api('compare/' + before + '...' + after)
        require(compare.get('merge_base_commit', {}).get('sha') == before,
                'Commit is not an ancestor: refusing unmerged release/downgrade')

    def download(self, assets, tag, name, target, max_bytes=MAX_ASSET):
        require(name in assets, 'Release asset missing: ' + name)
        asset = assets[name]
        require(asset.get('state') == 'uploaded', 'Release asset not uploaded')
        require(0 < asset.get('size', 0) <= max_bytes, 'Release asset size invalid: ' + name)
        expected = f'https://github.com/{self.repo}/releases/download/{tag}/{name}'
        require(urllib.parse.unquote(asset['browser_download_url']) == expected, 'Asset URL outside expected release')
        request = urllib.request.Request(asset['browser_download_url'], headers={'User-Agent': 'nextcloud-production-release/1'})
        with urllib.request.urlopen(request, timeout=120) as response, target.open('wb') as dest:
            require(urllib.parse.urlsplit(response.url).hostname in {
                'github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com'}, 'Untrusted asset redirect')
            size = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                require(size <= max_bytes, 'Asset exceeds download limit')
                dest.write(chunk)
        require(size == asset['size'], 'Incomplete asset download')
        if asset.get('digest'):
            require(asset['digest'] == 'sha256:' + digest(target), 'GitHub asset digest mismatch')

    def run(self, command, **kwargs):
        # Never print environment contents, config files, or passwords.
        subprocess.run(command, env=self.env, check=True, **kwargs)

    def compose(self, *args, capture=False, **kwargs):
        command = ['docker', 'compose', '--project-name', 'nextcloud-production', '--env-file', str(self.env_path),
                   '-f', str(self.root / 'current/deploy/production/compose.yaml'), *args]
        if capture:
            return subprocess.check_output(command, env=self.env, **kwargs)
        self.run(command, **kwargs)

    def set_current(self, path):
        temporary = self.root / 'current.new'
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(Path(path))
        temporary.replace(self.root / 'current')

    def image_env(self, manifest):
        old = self.env_path.read_text().splitlines() if self.env_path.exists() else []
        keys = set(IMAGE_KEYS.values())
        lines = [line for line in old if line.split('=', 1)[0].strip() not in keys]
        lines.extend(key + '=' + manifest['images'][name]['id'] for name, key in IMAGE_KEYS.items())
        if not any(line.startswith('NC_ADMIN_USER=') for line in lines):
            lines.append('NC_ADMIN_USER=admin')
        atomic_write(self.env_path, '\n'.join(lines) + '\n')

    def mark(self, release, commit, status, **extra):
        self.state['seen'][release['tag_name']] = {'commit': commit, 'status': status,
            'published_at': release['published_at'], **extra}
        write_json(self.state_path, self.state)

    def recover(self, tx):
        """Recreate old bind mounts and restore DB+config as one checkpoint."""
        backup = Path(tx['backup'])
        require(tx.get('previous') and (backup / 'COMPLETE').exists(), 'No complete automatic recovery checkpoint')
        self.env.pop('NEXTCLOUD_SKIP_PROXY', None)
        self.env.pop('NEXTCLOUD_SKIP_WORKERS', None)
        self.run(['sha256sum', '--check', 'SHA256SUMS'], cwd=backup)
        # Stop web/office first. Do not let a partially migrated application
        # write into the checkpoint while PostgreSQL is restored.
        self.compose('stop', 'proxy', 'nextcloud', 'office', *WORKERS)
        self.set_current(tx['previous'])
        shutil.copyfile(backup / 'images.env', self.env_path)
        self.env_path.chmod(0o600)
        self.compose('up', '-d', '--wait', 'db', 'redis')
        self.compose('exec', '-T', 'db', 'sh', '-eu', '-c',
                     'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = \'nextcloud\' AND pid <> pg_backend_pid();"')
        self.compose('exec', '-T', 'db', 'sh', '-eu', '-c',
                     'dropdb --if-exists --force -U "$POSTGRES_USER" nextcloud && createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" nextcloud')
        with (backup / 'postgres.dump').open('rb') as source:
            self.compose('exec', '-T', 'db', 'sh', '-eu', '-c',
                         'exec pg_restore --exit-on-error --no-owner --no-privileges -U "$POSTGRES_USER" -d nextcloud', stdin=source)
        html = self.data / 'html'
        for directory in ('config', 'custom_apps'):
            path = html / directory
            require(path.is_dir() and not path.is_symlink(), 'Unexpected recovery target')
            shutil.rmtree(path)
        self.run(['tar', '--numeric-owner', '-xpf', str(backup / 'config-customapps.tar'), '-C', str(html)])
        self.compose('up', '-d', '--force-recreate', 'nextcloud', 'office')
        self.maintenance_off()
        self.compose('up', '-d', '--force-recreate', '--wait', *WORKERS, 'proxy')
        self.run(['bash', str(self.root / 'current/deploy/production/healthcheck.sh')])
        write_json(backup / 'recovery.json', {'status': 'restored', 'failed_tag': tx['tag']})

    def maintenance_off(self):
        import time
        for attempt in range(60):
            try:
                self.compose('exec', '-T', '--user', 'www-data', 'nextcloud', 'php', 'occ', 'maintenance:mode', '--off')
                return
            except subprocess.CalledProcessError:
                if attempt == 59:
                    raise
                time.sleep(2)

    def resume_previous(self, tx):
        self.env.pop('NEXTCLOUD_SKIP_PROXY', None)
        self.env.pop('NEXTCLOUD_SKIP_WORKERS', None)
        self.set_current(tx['previous'])
        self.compose('up', '-d', 'db', 'redis', 'nextcloud', 'office')
        self.maintenance_off()
        self.compose('up', '-d', '--wait', *WORKERS, 'proxy')

    def interrupted(self):
        if not self.transaction_path.exists():
            return
        tx = json.loads(self.transaction_path.read_text())
        release = {'tag_name': tx['tag'], 'published_at': tx['published_at']}
        # A completed success record is authoritative if the process died
        # immediately before deleting its transaction journal.
        if self.state.get('success', {}).get('tag') == tx['tag']:
            self.transaction_path.unlink()
            return
        if tx['phase'] in {'traffic_committed', 'manual-recovery'}:
            self.mark(release, tx['commit'], 'manual-recovery-required', backup=tx['backup'])
            raise ReleaseError('Update crossed the traffic boundary or needs manual recovery; no automatic DB rollback')
        if tx['phase'] == 'preparing':
            if tx.get('previous'):
                self.resume_previous(tx)
            self.mark(release, tx['commit'], 'interrupted-before-change')
        else:
            try:
                self.recover(tx)
                self.mark(release, tx['commit'], 'interrupted-restored', backup=tx['backup'])
            except Exception:
                self.mark(release, tx['commit'], 'manual-recovery-required', backup=tx['backup'])
                tx['phase'] = 'manual-recovery'
                write_json(self.transaction_path, tx)
                raise ReleaseError('Interrupted update requires manual recovery; see protected state/transaction.json')
        self.transaction_path.unlink()

    def apply(self, release):
        tag = release['tag_name']
        baseline = None
        commit = self.tag_commit(tag)
        self.ancestor(commit, 'main')
        if self.state.get('success'):
            self.ancestor(self.state['success']['commit'], commit)
        elif (self.root / 'current').exists():
            baseline_file = self.root / 'current/manifest.json'
            require(baseline_file.is_file(), 'Existing manual deployment has no manifest baseline; refusing automatic update')
            baseline = json.loads(baseline_file.read_text())
            require(baseline.get('repo') == self.repo and SHA.fullmatch(baseline.get('commit', '')),
                    'Invalid existing deployment baseline')
            self.ancestor(baseline['commit'], commit)
        assets = {}
        for asset in release.get('assets', []):
            require(asset['name'] not in assets, 'Duplicate release asset')
            assets[asset['name']] = asset
        require('SHA256SUMS' in assets and 'manifest.json' in assets, 'Release lacks required assets')
        downloads = self.state_dir / 'downloads'
        downloads.mkdir(exist_ok=True)
        tx = None
        with tempfile.TemporaryDirectory(prefix=tag + '-', dir=downloads) as temporary:
            cache = Path(temporary)
            self.download(assets, tag, 'SHA256SUMS', cache / 'SHA256SUMS', 65536)
            sums = parse_sums((cache / 'SHA256SUMS').read_text())
            self.download(assets, tag, 'manifest.json', cache / 'manifest.json', 1024 * 1024)
            require(digest(cache / 'manifest.json') == sums['manifest.json'], 'Manifest checksum mismatch')
            manifest = json.loads((cache / 'manifest.json').read_text())
            validate_manifest(manifest, self.repo, release, commit, self.state)
            if baseline:
                require(baseline.get('images', {}).get('nextcloud', {}).get('id') == manifest['images']['nextcloud']['id'],
                        'Existing manual deployment core differs; manual core upgrade required')
            expected = {'deployment.tar.gz', 'manifest.json', *manifest['image_assets']}
            require(set(sums) == expected, 'Checksum inventory differs from manifest')
            total = sum(assets[name]['size'] for name in expected)
            require(total <= MAX_BUNDLE, 'Release exceeds bundle size limit')
            require(shutil.disk_usage(self.root).free > total * 3 + 2 * 1024 ** 3, 'Insufficient release staging disk space')
            for name in sorted(expected - {'manifest.json'}):
                self.download(assets, tag, name, cache / name,
                              MAX_DEPLOYMENT if name == 'deployment.tar.gz' else MAX_ASSET)
                require(digest(cache / name) == sums[name], 'Release checksum mismatch: ' + name)
            destination = self.root / 'releases' / tag
            require(not destination.exists(), 'Release directory already exists; investigate before reusing tag')
            destination.mkdir(parents=True)
            try:
                safe_extract(cache / 'deployment.tar.gz', destination)
                for required in ('compose.yaml', 'bootstrap.sh', 'healthcheck.sh', 'release.py', 'backup.sh', 'update.sh'):
                    require((destination / 'deploy/production' / required).is_file(), 'Deployment file missing: ' + required)
                write_json(destination / 'manifest.json', manifest)
                # Combine split compressed files without shell expansion, then
                # let Docker import its own supported archive format.
                combined = cache / 'docker-images.tar.gz'
                with combined.open('wb') as dst:
                    for name in manifest['image_assets']:
                        with (cache / name).open('rb') as src:
                            shutil.copyfileobj(src, dst, 1024 * 1024)
                self.run(['docker', 'load', '--input', str(combined)])
                for image in manifest['images'].values():
                    local = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image['id']], text=True))[0]
                    require(local['Id'] == image['id'] and local['Os'] == 'linux' and local['Architecture'] == 'amd64',
                            'Loaded image does not match content ID/platform')
            except Exception:
                # Staging is not a live release. Keep failed staging for evidence
                # only after mutation begins; otherwise allow the fixed next tag.
                shutil.rmtree(destination)
                raise
            current = self.root / 'current'
            previous = str(current.resolve()) if current.exists() else None
            backup = self.data / 'backups' / (tag + '-preupdate')
            tx = {'tag': tag, 'commit': commit, 'published_at': release['published_at'],
                  'previous': previous, 'backup': str(backup), 'phase': 'preparing'}
            write_json(self.transaction_path, tx)
            self.mark(release, commit, 'applying')
            try:
                if previous:
                    running = self.compose('ps', '-q', 'nextcloud', capture=True).decode().strip()
                    if running:
                        current_core = subprocess.check_output(['docker', 'inspect', '--format', '{{.Image}}', running], text=True).strip()
                        require(current_core == manifest['images']['nextcloud']['id'],
                                'Running Nextcloud core image differs; manual core upgrade required')
                    self.compose('exec', '-T', '--user', 'www-data', 'nextcloud', 'php', 'occ', 'maintenance:mode', '--on')
                    # Fence the ingress, then drain Office/web requests and jobs
                    # before the DB/config checkpoint. No application writes
                    # are allowed until the upgrade has passed internal health.
                    self.compose('stop', 'proxy')
                    self.compose('stop', '--timeout', '60', 'office', 'nextcloud', *WORKERS)
                    self.run(['bash', str(Path(previous) / 'deploy/production/backup.sh'), str(backup), '--quiesced'])
                tx['phase'] = 'changing'
                write_json(self.transaction_path, tx)
                self.set_current(destination)
                self.image_env(manifest)
                # Recreate is essential: Docker bind mounts retain the previous
                # release inode even when the host's current symlink changes.
                if previous:
                    self.compose('stop', 'proxy', 'nextcloud', 'office')
                    self.compose('up', '-d', '--force-recreate', 'db', 'redis', 'nextcloud', 'office')
                self.env['NEXTCLOUD_SKIP_PROXY'] = '1'
                self.env['NEXTCLOUD_SKIP_WORKERS'] = '1'
                self.run(['bash', str(destination / 'deploy/production/bootstrap.sh')])
                self.run(['bash', str(destination / 'deploy/production/healthcheck.sh')])
                # From this point real user writes may arrive. A later failure
                # must preserve the new database/data instead of rolling them
                # back to a checkpoint that predates accepted requests.
                tx['phase'] = 'traffic_committed'
                write_json(self.transaction_path, tx)
                self.env.pop('NEXTCLOUD_SKIP_PROXY', None)
                self.env.pop('NEXTCLOUD_SKIP_WORKERS', None)
                self.compose('up', '-d', '--force-recreate', '--wait', *WORKERS, 'proxy')
                self.run(['bash', str(destination / 'deploy/production/healthcheck.sh')])
                for name in ('release.py', 'update.sh', 'backup.sh'):
                    source = destination / 'deploy/production' / name
                    atomic_write(self.root / 'bin' / name, source.read_text(), 0o750)
                self.state['success'] = {'tag': tag, 'commit': commit, 'published_at': release['published_at'], 'images': manifest['images']}
                self.mark(release, commit, 'success', backup=str(backup) if previous else None)
                self.transaction_path.unlink()
                print('Production release healthy: ' + tag + ' ' + commit)
            except Exception:
                if tx['phase'] == 'traffic_committed':
                    self.mark(release, commit, 'manual-recovery-required', backup=str(backup))
                    tx['phase'] = 'manual-recovery'
                    write_json(self.transaction_path, tx)
                    raise ReleaseError('Release passed internal health; post-traffic failure needs manual repair without DB rollback')
                if previous and (backup / 'COMPLETE').exists():
                    try:
                        self.recover(tx)
                        self.mark(release, commit, 'failed-restored', backup=str(backup))
                        self.transaction_path.unlink()
                    except Exception:
                        self.mark(release, commit, 'manual-recovery-required', backup=str(backup))
                        tx['phase'] = 'manual-recovery'
                        write_json(self.transaction_path, tx)
                        raise ReleaseError('Update and automatic recovery failed; transaction and backups retained')
                elif tx['phase'] == 'preparing' and previous:
                    self.resume_previous(tx)
                    self.mark(release, commit, 'failed-before-change')
                    self.transaction_path.unlink()
                else:
                    self.mark(release, commit, 'manual-recovery-required', backup=str(backup))
                    tx['phase'] = 'manual-recovery'
                    write_json(self.transaction_path, tx)
                raise

    def check(self):
        self.interrupted()
        release = select_release(self.api('releases?per_page=100'), self.state)
        if not release:
            return
        try:
            self.apply(release)
        except Exception as error:
            if release['tag_name'] not in self.state['seen']:
                if isinstance(error, (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError)):
                    attempts = self.state.setdefault('download_attempts', {})
                    attempts[release['tag_name']] = attempts.get(release['tag_name'], 0) + 1
                    write_json(self.state_path, self.state)
                    if attempts[release['tag_name']] < 3:
                        raise ReleaseError('Transient release download failure; retrying on the next timer (maximum 3 attempts)') from None
                # Validation/download failures are also bounded to one attempt
                # per reviewed release tag. A new tag is the recovery mechanism.
                self.mark(release, self.state.get('observed_commits', {}).get(release['tag_name'], 'unresolved'), 'rejected')
            raise error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=os.environ.get('NEXTCLOUD_PRODUCTION_ROOT', '/srv/nextcloud-production'))
    parser.add_argument('--data', default=os.environ.get('NEXTCLOUD_DATA_ROOT', '/data/nextcloud-production'))
    parser.add_argument('--repo', default=os.environ.get('NEXTCLOUD_RELEASE_REPOSITORY', 'liugang926/nextcloud'))
    parser.add_argument('--recover-only', action='store_true', help='Recover an interrupted transaction without checking for new releases')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        updater = Updater(args.root, args.data, args.repo)
        if args.recover_only:
            updater.interrupted()
        else:
            updater.check()
    except Exception as error:
        # Do not emit subprocess command output or environment values here.
        print('Production release update failed: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
