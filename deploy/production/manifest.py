#!/usr/bin/env python3
"""Build an amd64, content-addressed production release (CI only)."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import time

IMAGES = {
    'nextcloud': 'nextcloud:34.0.4-apache',
    'postgres': 'postgres:16-alpine',
    'redis': 'redis:7-alpine',
    'office': 'collabora/code:26.04.2.2.1',
    'proxy': 'nginx:1.30.3-alpine',
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


def build(args):
    if not re.fullmatch(r'prod-[A-Za-z0-9][A-Za-z0-9._-]{0,100}', args.tag):
        raise ValueError('Invalid production tag')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo):
        raise ValueError('Invalid repository')
    commit = command('git', 'rev-parse', 'HEAD')
    if command('git', 'rev-parse', args.tag + '^{commit}') != commit:
        raise ValueError('Tag is not the checked-out commit')
    subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'origin/main'], check=True)
    root = Path(command('git', 'rev-parse', '--show-toplevel'))
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Output must be empty')
    images = {}
    for key, source in IMAGES.items():
        subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', source], check=True)
        data = json.loads(command('docker', 'image', 'inspect', source))[0]
        if (data.get('Os'), data.get('Architecture')) != ('linux', 'amd64'):
            raise ValueError('Refusing non-amd64 image: ' + source)
        digests = data.get('RepoDigests', [])
        if not digests or not re.fullmatch(r'.+@sha256:[0-9a-f]{64}', digests[0]):
            raise ValueError('Registry digest missing: ' + source)
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', data['Id']):
            raise ValueError('Invalid config digest')
        images[key] = {'source': source, 'registry_digest': digests[0],
                       'id': data['Id'], 'platform': 'linux/amd64'}
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
    parser.add_argument('--output', default='dist/production')
    build(parser.parse_args())
