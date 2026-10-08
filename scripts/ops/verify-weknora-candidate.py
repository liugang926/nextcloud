#!/usr/bin/env python3
"""Verify a development patch in a temporary Git index without changing a checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('c6', 'rag'), required=True)
    parser.add_argument('--source-repo', type=Path, required=True,
                        help='local Git repository containing the exact upstream baseline')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    manifest = json.loads((project / 'integration/candidates/manifest.json').read_text())
    entry = manifest['profiles'][args.profile]
    patch = project / entry['path']
    if hashlib.sha256(patch.read_bytes()).hexdigest() != entry['patch_sha256']:
        raise ValueError('candidate patch SHA-256 mismatch')

    with tempfile.TemporaryDirectory(prefix='weknora-candidate-index-') as private:
        env = os.environ.copy()
        env['GIT_INDEX_FILE'] = str(Path(private) / 'index')

        def git(*arguments):
            return subprocess.check_output(['git', '-C', str(args.source_repo), *arguments],
                                           env=env, text=True).strip()

        if git('rev-parse', entry['base'] + '^{tree}') != entry['base_tree']:
            raise ValueError('baseline Git tree mismatch')
        git('read-tree', entry['base'])
        git('apply', '--cached', '--check', str(patch))
        git('apply', '--cached', str(patch))
        if git('write-tree') != entry['candidate_tree']:
            raise ValueError('applied candidate Git tree mismatch')
        git('apply', '--cached', '--reverse', str(patch))
        if git('write-tree') != entry['base_tree']:
            raise ValueError('reverse application changed the baseline tree')

    print(json.dumps({'profile': args.profile, 'base': entry['base'],
                      'candidate_tree': entry['candidate_tree'],
                      'patch_sha256': entry['patch_sha256'],
                      'apply_and_reverse_exact': True}, sort_keys=True))


if __name__ == '__main__':
    main()
