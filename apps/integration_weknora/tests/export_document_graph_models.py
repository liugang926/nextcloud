#!/usr/bin/env python3
"""Export two existing model rows for the isolated live pipeline; never print secrets."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-container', required=True)
    parser.add_argument('--app-container', required=True)
    parser.add_argument('--embedding-id', required=True)
    parser.add_argument('--chat-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    ids = [args.embedding_id, args.chat_id]
    if len(set(ids)) != 2 or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value) for value in ids):
        raise ValueError('Two distinct model IDs containing letters/digits/_/- are required')
    # Parameters stay encrypted until the isolated Go fixture decrypts them
    # with the app's source key. SQL and docker outputs are captured privately.
    sql = ("SELECT json_agg(row_to_json(m)) FROM (SELECT id,name,type,source,status,parameters "
           "FROM models WHERE deleted_at IS NULL AND id IN ('" + "','".join(ids) + "')) m")
    result = subprocess.run(['docker', 'exec', args.database_container, 'sh', '-c',
                             'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$1"',
                             'read-model-config', sql], capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('Model database export failed')
    rows = json.loads(result.stdout)
    if not isinstance(rows, list) or len(rows) != 2:
        raise ValueError('Both configured model rows must exist')
    by_id = {row['id']: row for row in rows}
    embedding, chat = by_id[ids[0]], by_id[ids[1]]
    if embedding['type'] != 'Embedding' or chat['type'] != 'KnowledgeQA':
        raise ValueError('Selected model types do not match embedding/chat')
    if any(row['status'] != 'active' for row in rows):
        raise ValueError('Both models must be active')
    inspected = subprocess.run(['docker', 'inspect', args.app_container], capture_output=True, timeout=20)
    if inspected.returncode:
        raise RuntimeError('App configuration inspection failed')
    env = dict(value.split('=', 1) for value in json.loads(inspected.stdout)[0]['Config']['Env'] if '=' in value)
    key = env.get('SYSTEM_AES_KEY', '')
    if len(key.encode()) != 32:
        raise ValueError('Source app AES key unavailable')
    os.umask(0o077)
    # Refuse replacement and require a private parent; callers own cleanup.
    if args.output.parent.stat().st_mode & 0o077:
        raise ValueError('Output directory must have mode 0700')
    with args.output.open('x') as output:
        json.dump({'embedding': embedding, 'chat': chat, 'source_aes_key': key}, output)
    print('Private model export created; source app/database unchanged')


if __name__ == '__main__':
    main()
