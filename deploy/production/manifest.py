#!/usr/bin/env python3
"""Build an amd64, content-addressed production release (CI only)."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import tarfile
import time
import xml.etree.ElementTree as ET

IMAGES = {
    'postgres': 'postgres:16-alpine',
    'redis': 'redis:7-alpine',
    'office': 'collabora/code:26.04.2.4.1',
    'proxy': 'nginx:1.30.3-alpine',
}
NEXTCLOUD_CORE_UPDATE = {
    'from_source': 'nextcloud:34.0.4-apache',
    'to_source': 'ghcr.io/liugang926/nextcloud-production-core:34.0.4-php8.5.12-r1',
    'from_php': '8.5.11',
    'to_php': '8.5.12',
    'nextcloud_version': '34.0.4',
    'dockerfile_commit': 'a1206d2467689ecb8597b7e5b753b49dda29b773',
}
RICHDOCUMENTS_SHA256 = '96766a94cf417a716e47ed89c6ff2411c54954caa916afcdddc4d9668dffb557'
PART_BYTES = 1700 * 1024 * 1024  # Below GitHub's per-asset size limit.


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def runtime_file(path):
    parts = path.parts
    if any(p in {'.git', 'node_modules', 'tests', '__pycache__', '.venv'} for p in parts):
        return False
    if path.name.startswith('test_') or path.name.startswith('test-') or path.suffix in {'.pyc', '.map'}:
        return False
    return parts[0] in {'apps', 'scripts', 'deploy'}


def verify_tag(tag, commit, allow_missing, cwd=None):
    """Resolve a local tag when present and require it to identify the release commit."""
    resolved = subprocess.run(
        ['git', 'rev-parse', tag + '^{commit}'],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if resolved.returncode != 0:
        if allow_missing:
            return None
        raise ValueError('Tag is not the checked-out commit')
    target = resolved.stdout.strip()
    if target != commit:
        raise ValueError('Tag is not the checked-out commit')
    return target


def verify_office_image(image, helper_image):
    """Check the exact Office runtime contract before publishing its image."""
    # Both CI and production use these helpers; the Office image itself is
    # distroless and provides neither a shell nor diagnostic command-line tools.
    contract = runpy.run_path(str(Path(__file__).with_name('verify-api.py')))
    owner = contract['office_data_owner'](image)
    if not re.fullmatch(r'[0-9]+:[0-9]+', owner):
        raise ValueError('Office image has no numeric data-directory owner')
    domain = 'cloud.tianjizn.com'
    container = command('docker', 'create', '--pull=never', '--network', 'none', '--cpus', '1',
                        '--memory', '2g', '--pids-limit', '512', '--shm-size', '512m', '--cap-add', 'MKNOD',
                        '-e', 'aliasgroup1=https://' + domain + ':443',
                        '-e', 'aliasgroup2=http://nextcloud:80', '-e', 'server_name=' + domain,
                        '-e', 'dictionaries=en_US zh_CN',
                        '-e', 'extra_params=--o:ssl.enable=false --o:ssl.termination=true --o:logging.level=warning --o:net.proto=IPv4 --o:num_prespawn_children=1', image)
    try:
        subprocess.run(['docker', 'start', container], check=True, stdout=subprocess.DEVNULL)
        for _ in range(90):
            state = json.loads(command('docker', 'inspect', '--format', '{{json .State}}', container))
            if not state.get('Running'):
                raise RuntimeError('Office image exited during the production startup contract')
            if state.get('Health', {}).get('Status') == 'healthy':
                break
            time.sleep(2)
        else:
            raise RuntimeError('Office image failed its native production health check')
        response = contract['office_curl'](
            container, helper_image,
            ['--fail', '--silent', '--show-error', '--max-time', '10',
             'http://127.0.0.1:9980/hosting/discovery'])
        if response.returncode != 0:
            raise RuntimeError('Office image did not serve HTTP discovery')
        discovery = ET.fromstring(response.stdout)
        actions = discovery.findall('.//action')
        if discovery.tag != 'wopi-discovery' or not actions or not all(
                action.get('urlsrc', '').startswith('https://' + domain + '/') for action in actions):
            raise ValueError('Office image does not honor the production HTTPS origin')
        contract['convert_docx'](container, helper_image, contract['synthetic_docx'](), '生产环境 Office 中文验证.docx')
        print('Office native health, data ownership, HTTP discovery, HTTPS origin and Unicode DOCX conversion verified')
    finally:
        subprocess.run(['docker', 'rm', '--force', container], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def verify_nextcloud_image(image):
    php = command('docker', 'run', '--rm', '--entrypoint', 'php', image, '-r',
                  'echo PHP_VERSION;')
    if php != NEXTCLOUD_CORE_UPDATE['to_php']:
        raise ValueError('Nextcloud image PHP version is not 8.5.12: ' + php)
    nextcloud = command(
        'docker', 'run', '--rm', '--entrypoint', 'php', image, '-r',
        '$OC_Version=array();$OC_VersionString="";'
        'require "/usr/src/nextcloud/version.php";echo $OC_VersionString;',
    )
    if nextcloud != NEXTCLOUD_CORE_UPDATE['nextcloud_version']:
        raise ValueError('Nextcloud core version changed: ' + nextcloud)


def build(args):
    if not re.fullmatch(r'prod-[A-Za-z0-9][A-Za-z0-9._-]{0,100}', args.tag):
        raise ValueError('Invalid production tag')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo):
        raise ValueError('Invalid repository')
    commit = command('git', 'rev-parse', 'HEAD')
    verify_tag(args.tag, commit, args.allow_missing_tag)
    subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'origin/main'], check=True)
    root = Path(command('git', 'rev-parse', '--show-toplevel'))
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Output must be empty')
    if args.nextcloud_image != NEXTCLOUD_CORE_UPDATE['to_source']:
        raise ValueError('Unexpected hardened Nextcloud image reference')
    images = {}
    sources = {'nextcloud': args.nextcloud_image, **IMAGES}
    for key, source in sources.items():
        subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', source], check=True)
        data = json.loads(command('docker', 'image', 'inspect', source))[0]
        if data.get('Descriptor') is not None:
            raise ValueError('CI requires classic Docker config IDs; refusing to label an OCI runtime digest as a config ID')
        if (data.get('Os'), data.get('Architecture')) != ('linux', 'amd64'):
            raise ValueError('Refusing non-amd64 image: ' + source)
        digests = data.get('RepoDigests', [])
        if not digests or not re.fullmatch(r'.+@sha256:[0-9a-f]{64}', digests[0]):
            raise ValueError('Registry digest missing: ' + source)
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', data['Id']):
            raise ValueError('Invalid config digest')
        images[key] = {'source': source, 'registry_digest': digests[0],
                       'id': data['Id'], 'platform': 'linux/amd64'}
    verify_nextcloud_image(images['nextcloud']['id'])
    verify_office_image(images['office']['id'], images['nextcloud']['id'])
    office_app = root / 'deploy/production/richdocuments-11.1.2.tar.gz'
    if not office_app.is_file() or sha256(office_app) != RICHDOCUMENTS_SHA256:
        raise ValueError('Missing or corrupt pinned richdocuments 11.1.2 asset')
    files = [Path(p) for p in command('git', 'ls-files').splitlines() if runtime_file(Path(p))]
    if office_app.relative_to(root) not in files:
        files.append(office_app.relative_to(root))
    with tarfile.open(output / 'deployment.tar.gz', 'w:gz') as archive:
        for relative in sorted(files):
            source = root / relative
            if source.is_symlink() or not source.is_file():
                raise ValueError('Only regular deployment files allowed: ' + str(relative))
            archive.add(source, arcname=relative.as_posix(), recursive=False)
    # docker save keeps config IDs and tags, not registry RepoDigests. The server
    # uses verified config IDs in Compose and never performs an implicit pull.
    archive = output / 'images.tar.gz'
    process = subprocess.Popen(['docker', 'save', *[i['source'] for i in images.values()]], stdout=subprocess.PIPE)
    with archive.open('wb') as dest, gzip.GzipFile(fileobj=dest, mode='wb', mtime=0) as compressed:
        while True:
            chunk = process.stdout.read(1024 * 1024)
            if not chunk:
                break
            compressed.write(chunk)
    if process.wait() != 0:
        raise RuntimeError('docker save failed')
    image_assets = ['images.tar.gz']
    if archive.stat().st_size > PART_BYTES:
        image_assets = []
        with archive.open('rb') as source:
            index = 0
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                name = 'images.tar.gz.part-' + str(index).zfill(3)
                with (output / name).open('wb') as part:
                    part.write(chunk)
                    remaining = PART_BYTES - len(chunk)
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        part.write(chunk)
                        remaining -= len(chunk)
                image_assets.append(name)
                index += 1
        archive.unlink()
    manifest = {
        'schema': 1, 'repo': args.repo, 'tag': args.tag, 'commit': commit,
        'platform': 'linux/amd64', 'nextcloud_major': 34,
        'built_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'images': images, 'image_assets': image_assets,
        'core_update': NEXTCLOUD_CORE_UPDATE,
        'office_app': {'version': '11.1.2', 'sha256': RICHDOCUMENTS_SHA256},
    }
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    entries = sorted(p for p in output.iterdir() if p.is_file())
    (output / 'SHA256SUMS').write_text(''.join(sha256(p) + '  ' + p.name + '\n' for p in entries))
    print(json.dumps({'tag': args.tag, 'commit': commit, 'assets': len(entries) + 1}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--nextcloud-image', required=True)
    parser.add_argument('--output', default='dist/production')
    parser.add_argument(
        '--allow-missing-tag',
        action='store_true',
        help='Allow the release tag to be created atomically by the automatic publication step',
    )
    build(parser.parse_args())
