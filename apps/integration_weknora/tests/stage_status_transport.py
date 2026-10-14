#!/usr/bin/env python3
"""Real HTTP push/signature/replay tests against the owned disposable NC/PG runtime."""
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time

if os.environ.get('WEKNORA_PRD_TEST_RUNTIME') != '1':
    raise SystemExit('Disposable test runtime required')
container = 'nextcloud-auto-space-prd-test-20261010'
fixture = json.loads(subprocess.check_output([
    'docker', 'exec', container, 'cat', '/tmp/weknora-stage-status-fixture.json']))
state = fixture['state']
path = '/index.php/apps/integration_weknora/api/v1/status'
key = hmac.new(fixture['secret'].encode(), b'weknora-nextcloud-state-key-v2', hashlib.sha256).digest()

def push(body, nonce=None, sign_body=None):
    raw = json.dumps(body, separators=(',', ':')).encode()
    signed_raw = raw if sign_body is None else json.dumps(sign_body, separators=(',', ':')).encode()
    stamp = str(int(time.time()))
    nonce = nonce or secrets.token_hex(16)
    canonical = '\n'.join(['weknora-state-push-hmac-sha256-v2', 'POST', path, '',
        hashlib.sha256(signed_raw).hexdigest(), stamp, nonce, state['connection_id'], fixture['key_id']])
    signature = hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()
    args = ['docker', 'exec', '-i', container, 'curl', '-sS', '--max-time', '15',
        '-w', '\n%{http_code}', '-H', 'Content-Type: application/json']
    for name, value in [('Connection-Id', state['connection_id']), ('Key-Id', fixture['key_id']),
        ('Timestamp', stamp), ('Nonce', nonce), ('Signature', signature)]:
        args += ['-H', 'X-WeKnora-' + name + ': ' + value]
    args += ['--data-binary', '@-', 'http://localhost' + path]
    output = subprocess.check_output(args, input=raw).decode()
    payload, code = output.rsplit('\n', 1)
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AssertionError(f'Expected JSON response, received HTTP {code}') from exc
    return int(code), parsed

nonce = secrets.token_hex(16)
code, result = push(state, nonce)
assert code == 200 and result['status'] == 'accepted' and result['persisted'] is True
assert result['event_id'] == state['event_id']
code, result = push(state, nonce)
assert code == 401, 'Replayed nonce was accepted'
code, result = push(state)
assert code == 200 and result['duplicate'] is True, 'Lost ACK retry was not idempotent'
tampered = dict(state, source_etag='tampered-etag', published_source_etag='tampered-etag')
code, result = push(tampered, sign_body=state)
assert code == 401, 'Tampered signed metadata was accepted'
other_kb = dict(state, event_id='cross-kb-' + secrets.token_hex(8), knowledge_base_id='other-kb')
code, result = push(other_kb)
assert code == 409, 'Authenticated cross-KB metadata escaped pairing scope'
collision = dict(state, status_revision=str(int(state['status_revision']) + 1))
code, result = push(collision)
assert code == 409, 'Same event ID with different metadata was accepted'
print('Real Nextcloud HTTP signed push, durable ACK retry, replay, tamper and cross-KB rejection passed')
