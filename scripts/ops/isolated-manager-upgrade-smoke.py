#!/usr/bin/env python3
"""Upgrade the production app baseline in owned, offline NC/PostgreSQL containers.

No host ports, bind mounts, external routes, GPU devices or production credentials.
Images must already be cached. All owned containers/volumes are removed in finally.
"""
import argparse
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
BASELINE = 'a8e955bd80d00f33d48054b0878f8cb3b153906d'
NC = 'nextcloud:34.0.4-apache@sha256:a5ace30c695afe48c2c406e940ee7886a81e13fa382e57cd68b2416d1a66914c'
PG = 'postgres:16-alpine@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'


def run(*args, data=None, timeout=240, check=True):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, cwd=ROOT)
    if check and result.returncode:
        # Arguments may contain disposable credentials; never include the command.
        raise RuntimeError('Fixture command failed: ' + result.stderr.decode(errors='replace')[-2000:])
    return result.stdout.decode(errors='replace')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hold-for-probe', action='store_true', help='Keep owned fixture until stdin newline, for a separate gate probe')
    parser.add_argument('--gate-probe', action='store_true', help='Also execute the real LDAP/signed recovery CLI probe')
    args = parser.parse_args()
    if args.hold_for_probe and not sys.stdin.isatty():
        parser.error('--hold-for-probe requires an interactive terminal')
    label = 'manager-upgrade-' + uuid.uuid4().hex[:12]
    pg, nc = label + '-pg', label + '-nc'
    containers = []
    # Resolve cached immutable references; this fixture never pulls from a registry.
    for image in (NC, PG): run('docker', 'image', 'inspect', image)
    try:
        containers.append(pg)
        run('docker', 'run', '-d', '--pull=never', '--name', pg, '--label', 'codex.task=' + label,
            '--network=none', '--add-host=prd-ad:127.0.0.1', '--cpus=.5', '--memory=512m', '--memory-swap=512m', '--pids-limit=128',
            '--security-opt=no-new-privileges', '--tmpfs', '/var/lib/postgresql/data:rw,size=512m',
            '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_USER=nextcloud', '-e', 'POSTGRES_DB=nextcloud', PG)
        for _ in range(90):
            if subprocess.run(['docker', 'exec', pg, 'pg_isready', '-U', 'nextcloud'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0: break
            time.sleep(1)
        else: raise RuntimeError('Private PostgreSQL did not become ready')
        containers.append(nc)
        run('docker', 'run', '-d', '--pull=never', '--name', nc, '--label', 'codex.task=' + label,
            '--network=container:' + pg, '--cpus=1', '--memory=1536m', '--memory-swap=1536m',
            '--pids-limit=256', '--security-opt=no-new-privileges', '--tmpfs', '/tmp:rw,size=192m',
            '--entrypoint=/bin/sh', NC, '-c',
            'cp -a /usr/src/nextcloud/. /var/www/html/ && chown -R 33:33 /var/www/html && touch /tmp/fixture-ready && exec sleep infinity')
        for _ in range(180):
            if subprocess.run(['docker', 'exec', nc, 'test', '-e', '/tmp/fixture-ready'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0: break
            time.sleep(1)
        else: raise RuntimeError('Private Nextcloud source copy did not complete')
        for name in containers:
            detail = json.loads(run('docker', 'inspect', name))[0]
            assert detail['Config']['Labels']['codex.task'] == label
            assert not any((detail['HostConfig'].get('PortBindings') or {}).values())
            assert not detail['HostConfig'].get('DeviceRequests')
            assert not any(m['Type'] == 'bind' for m in detail['Mounts'])
        def occ(*values):
            return run('docker', 'exec', '-u', '33:33', nc, 'php', 'occ', *values, timeout=360)
        def php(path, *values):
            return run('docker', 'exec', '-u', '33:33', '-e', 'WEKNORA_PRD_TEST_RUNTIME=1', nc, 'php', path, *values)
        def install_app(ref):
            archive = subprocess.check_output(['git', 'archive', ref, '--', 'apps/integration_weknora'], cwd=ROOT)
            run('docker', 'exec', '-i', nc, 'tar', '-xf', '-', '-C', '/var/www/html/custom_apps', '--strip-components=1', data=archive)
            run('docker', 'exec', nc, 'chown', '-R', '33:33', '/var/www/html/custom_apps/integration_weknora')
        password = secrets.token_urlsafe(32)
        occ('maintenance:install', '--database=pgsql', '--database-host=127.0.0.1', '--database-name=nextcloud',
            '--database-user=nextcloud', '--database-pass=', '--admin-user=auto-prd-admin', '--admin-pass=' + password,
            '--data-dir=/var/www/html/data')
        install_app(BASELINE)
        occ('app:enable', 'integration_weknora')
        # This witness did not exist in the baseline tree; copy only the test itself.
        witness = ROOT / 'apps/integration_weknora/tests/manager_upgrade_runtime.php'
        run('docker', 'exec', '-i', nc, 'tee', '/tmp/manager-upgrade.php', data=witness.read_bytes())
        print(php('/tmp/manager-upgrade.php', 'baseline').strip(), flush=True)
        occ('maintenance:mode', '--on')
        install_app('HEAD')
        occ('upgrade', '--no-interaction')
        occ('maintenance:mode', '--off')
        print(php('/tmp/manager-upgrade.php', 'candidate').strip(), flush=True)
        occ('upgrade', '--no-interaction')
        print(php('/tmp/manager-upgrade.php', 'candidate').strip(), flush=True)
        print(php('custom_apps/integration_weknora/tests/manager_authorization_metadata_runtime.php').strip(), flush=True)
        print(json.dumps({'fixture_ready': True, 'pg': pg, 'nc': nc, 'label': label,
            'no_host_ports': True, 'no_external_route': True, 'no_host_data': True, 'production_modified': False}), flush=True)
        if args.gate_probe:
            run('python3', str(ROOT / 'scripts/ops/isolated-manager-gate-probe.py'), '--nc', nc, '--pg', pg, '--label', label, timeout=900)
            print(json.dumps({'trusted_gate_probe': 'PASS'}), flush=True)
        if args.hold_for_probe:
            sys.stdin.readline()
    finally:
        for name in reversed(containers):
            inspected = run('docker', 'inspect', name, check=False)
            if not inspected or json.loads(inspected) == []:
                continue
            detail = json.loads(inspected)[0]
            if detail['Config']['Labels'].get('codex.task') != label: raise RuntimeError('Refusing unowned fixture cleanup')
            run('docker', 'rm', '-fv', name)
        print(json.dumps({'owned_fixture_cleanup': 'PASS', 'remaining_owned_containers': json.loads(run('docker', 'ps', '-aq', '--filter', 'label=codex.task=' + label, '--format', '{{json .Names}}') or '[]')}), flush=True)


if __name__ == '__main__': main()
