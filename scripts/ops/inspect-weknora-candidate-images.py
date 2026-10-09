#!/usr/bin/env python3
"""Read-only candidate app/UI image provenance check before an owned fixture.

This checks immutable local image IDs and build labels against the exact source
manifest. It does not start services or attest runtime behavior.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def check_image(image, entry):
    if not isinstance(image, dict):
        raise ValueError('invalid image inspection')
    identity = image.get('Id', '')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', identity):
        raise ValueError('image digest is unavailable')
    labels = (image.get('Config') or {}).get('Labels') or {}
    expected = {
        'org.opencontainers.image.revision': entry['base'],
        'io.github.liugang926.weknora.nextcloud-patch-sha256': entry['patch_sha256'],
        'io.github.liugang926.weknora.candidate-tree': entry['candidate_tree'],
        'io.github.liugang926.weknora.candidate-commit': entry['candidate_commit'],
    }
    if any(labels.get(key) != value for key, value in expected.items()):
        raise ValueError('candidate image source labels do not match the manifest')
    return {'image_id': identity, 'source_labels': expected}


def inspect(reference):
    result = subprocess.run(['docker', 'image', 'inspect', reference],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('required candidate image is absent')
    images = json.loads(result.stdout)
    if len(images) != 1:
        raise ValueError('ambiguous candidate image')
    return images[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('c6', 'rag'), default='rag')
    parser.add_argument('--manifest', type=Path,
                        default=ROOT / 'integration/candidates/manifest.json')
    parser.add_argument('--app-image', required=True)
    parser.add_argument('--ui-image', required=True)
    args = parser.parse_args()
    raw = args.manifest.read_bytes()
    entry = json.loads(raw)['profiles'][args.profile]
    app = check_image(inspect(args.app_image), entry)
    ui = check_image(inspect(args.ui_image), entry)
    if app['image_id'] == ui['image_id']:
        raise ValueError('backend and UI must be distinct images')
    print(json.dumps({'profile': args.profile,
                      'manifest_sha256': hashlib.sha256(raw).hexdigest(),
                      'app': app, 'ui': ui, 'services_started': False,
                      'runtime_acceptance': False}, sort_keys=True))


if __name__ == '__main__':
    main()
