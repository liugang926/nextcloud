#!/usr/bin/env python3
"""Renew the owned legacy sleep-supervised AD fixture without reprovisioning it."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

if os.environ.get('WEKNORA_PRD_TEST_RUNTIME') != '1':
    raise SystemExit('Disposable test runtime opt-in required')
ad = 'weknora-auto-space-ad-20261010'
nc = 'nextcloud-auto-space-prd-test-20261010'
for name in (ad, nc):
    info = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
    if info['Config'].get('Labels', {}).get('codex.task') != 'auto-space-prd-20261010':
        raise SystemExit('Refusing unowned container')
    if any(info['HostConfig'].get('PortBindings', {}).values()):
        raise SystemExit('Refusing host-published fixture')
    if name == ad and (info['Config']['Entrypoint'] != ['sleep'] or info['Config']['Cmd'] != ['infinity']):
        raise SystemExit('This renewal requires the legacy sleep-supervised fixture')

def run(*args):
    return subprocess.check_output(args, timeout=45, stderr=subprocess.STDOUT)

def identities():
    result = {}
    for user in ('prd-alice', 'prd-bob', 'prd-sourceowner'):
        rows = run('docker', 'exec', ad, 'samba-tool', 'user', 'show', user).decode().splitlines()
        result[user] = next(row for row in rows if row.startswith('objectGUID:'))
    return result

before = identities()
# Private keys stay inside the owned container. Replace files before restarting
# Samba; PID 1 is sleep, so stopping Samba cannot recreate the AD database.
run('docker', 'exec', ad, 'bash', '-ceu', '''
cd /var/lib/samba/private/tls
umask 077
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key.new -out ca.pem.new -days 2 -subj /CN=Disposable-PRD-CA 2>/dev/null
openssl req -newkey rsa:2048 -nodes -keyout key.pem.new -out request.new -subj /CN=prd-ad 2>/dev/null
printf 'subjectAltName=DNS:prd-ad,DNS:prd-ad.prd.example.test\nextendedKeyUsage=serverAuth\n' > extensions.new
openssl x509 -req -in request.new -CA ca.pem.new -CAkey ca.key.new -CAcreateserial -out cert.pem.new -days 2 -extfile extensions.new 2>/dev/null
openssl verify -CAfile ca.pem.new cert.pem.new
for file in ca.key ca.pem key.pem cert.pem; do mv "$file.new" "$file"; done
chmod 644 ca.pem cert.pem
rm -f request.new extensions.new ca.pem.srl
pkill -TERM -x samba
''')
deadline = time.monotonic() + 15
while subprocess.run(['docker', 'exec', ad, 'pgrep', '-x', 'samba'], stdout=subprocess.DEVNULL).returncode == 0:
    if time.monotonic() > deadline:
        raise SystemExit('Samba has not stopped; refusing a second server')
    time.sleep(.2)
run('docker', 'exec', '-d', ad, 'samba', '--foreground', '--no-process-group')
with tempfile.TemporaryDirectory(prefix='prd-renew-public-ca-') as directory:
    public = str(Path(directory) / 'ca.crt')
    run('docker', 'cp', ad + ':/var/lib/samba/private/tls/ca.pem', public)
    run('docker', 'cp', public, nc + ':/usr/local/share/ca-certificates/prd-ad.crt')
run('docker', 'exec', nc, 'update-ca-certificates', '--fresh')
deadline = time.monotonic() + 30
while True:
    result = subprocess.run(['docker', 'exec', '--user', 'www-data', nc, 'php', 'occ', 'ldap:test-config', 's01'], capture_output=True, timeout=10)
    if result.returncode == 0:
        break
    if time.monotonic() > deadline:
        raise SystemExit('Renewed LDAPS configuration has not passed verification')
    time.sleep(.5)
if identities() != before:
    raise SystemExit('Directory identity changed unexpectedly')
print('Renewed isolated LDAPS certificate; verified NC trust and unchanged test user GUIDs')
