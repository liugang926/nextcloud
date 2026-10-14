#!/usr/bin/env python3
"""Read-only SQL/telemetry contracts for a genuine Auto153 pending checkpoint.

These methods cannot produce a queue intent or attest runtime acceptance. The
caller must collect the rows from the actual owned application and run the
packaged body journal verification before accepting a checkpoint.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid

HASH = re.compile(r'[0-9a-f]{64}\Z')
KINDS = ('summary', 'question', 'auto_tag', 'other')


def require(value, code):
    if not value:
        raise RuntimeError(code)


def canonical_uuid(value):
    require(isinstance(value, str) and str(uuid.UUID(value)) == value, 'pending_auto_invalid_uuid')
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()


def pending_snapshot_query(tenant, kb, knowledge):
    require(type(tenant) is int and tenant > 0, 'pending_auto_invalid_tenant')
    canonical_uuid(kb); canonical_uuid(knowledge)
    # No model/original body is selected. ActualInputs is a body152 vault
    # reference; dereferencing it with ad-hoc SQL would bypass its reader.
    return f"""SELECT jsonb_build_object(
      'knowledge',(SELECT jsonb_build_object('id',id,'tenant_id',tenant_id,
        'knowledge_base_id',knowledge_base_id,'channel',channel,'parse_status',parse_status,
        'pending_subtasks_count',pending_subtasks_count,'enable_status',enable_status)
        FROM knowledges WHERE id='{knowledge}' AND tenant_id={tenant} AND knowledge_base_id='{kb}'),
      'intents',(SELECT COALESCE(jsonb_agg(jsonb_build_object('id',id,'tenant_id',tenant_id,
        'task_type',task_type,'scope',scope,'scope_id',scope_id,'op',op,'dedup_key',dedup_key,
        'payload_json',payload::text,'claimed_at',claimed_at,'fail_count',fail_count) ORDER BY id),'[]'::jsonb)
        FROM task_pending_ops WHERE tenant_id={tenant} AND task_type='knowledge:auto_tag'
        AND scope='knowledge' AND scope_id='{knowledge}'),
      'manifests',(SELECT COALESCE(jsonb_agg(jsonb_build_object('manifest_id',m.manifest_id,
        'tenant_id',m.tenant_id,'knowledge_base_id',m.knowledge_base_id,'knowledge_id',m.knowledge_id,
        'admission_id',m.admission_id,'input_digest',m.input_digest,
        'head',jsonb_build_object('body_id',h.body_id,'body_sha256',b.body_sha256,'state',b.state,
          'storage_kind',b.storage_kind,'revision',h.revision,'tenant_id',h.tenant_id,
          'revision_matches',EXISTS(SELECT 1 FROM original_body_field_revisions r
            WHERE r.slot_id=h.slot_id AND r.revision=h.revision AND r.body_id=h.body_id
            AND r.original_body_ref=h.original_body_ref))) ORDER BY m.manifest_id),'[]'::jsonb)
        FROM knowledge_auto_tag_input_manifests m LEFT JOIN original_body_field_heads h
          ON h.source_table='knowledge_auto_tag_input_manifests' AND h.row_key=m.manifest_id AND h.column_name='actual_inputs'
        LEFT JOIN original_body_payloads b ON b.id=h.body_id
        WHERE m.tenant_id={tenant} AND m.knowledge_base_id='{kb}' AND m.knowledge_id='{knowledge}'),
      'admissions',(SELECT COALESCE(jsonb_agg(to_jsonb(a) ORDER BY a.admission_id),'[]'::jsonb)
        FROM knowledge_input_admissions a JOIN knowledge_input_material_receipts r
          ON r.admission_id=a.admission_id AND r.row_kind='knowledge' AND r.row_id='{knowledge}'
        WHERE a.tenant_id={tenant} AND a.knowledge_base_id='{kb}' AND a.knowledge_id='{knowledge}'),
      'actors',(SELECT COALESCE(jsonb_agg(to_jsonb(c) ORDER BY c.admission_id),'[]'::jsonb)
        FROM knowledge_input_actor_selectors c JOIN knowledge_input_material_receipts r
          ON r.admission_id=c.admission_id AND r.row_kind='knowledge' AND r.row_id='{knowledge}'),
      'tag_ids',(SELECT COALESCE(jsonb_agg(tag_id ORDER BY tag_id),'[]'::jsonb)
        FROM knowledge_tag_relations WHERE knowledge_id='{knowledge}'),
      'candidate_ids',(SELECT COALESCE(jsonb_agg(id ORDER BY id),'[]'::jsonb)
        FROM knowledge_tags WHERE tenant_id={tenant} AND knowledge_base_id='{kb}'),
      'published',(SELECT count(*) FROM nextcloud_source_versions v WHERE v.tenant_id={tenant}
        AND v.candidate_knowledge_id='{knowledge}' AND v.state='published'),
      'stats',jsonb_build_object(
        'running_syncs',(SELECT count(*) FROM sync_logs WHERE tenant_id={tenant} AND status='running'),
        'other_processing',(SELECT count(*) FROM knowledges WHERE tenant_id={tenant}
          AND id<>'{knowledge}' AND parse_status IN ('pending','processing','finalizing')),
        'staged',(SELECT count(*) FROM nextcloud_source_versions WHERE tenant_id={tenant} AND state='staging'),
        'active_auto',(SELECT count(*) FROM task_pending_ops WHERE tenant_id={tenant}
          AND task_type='knowledge:auto_tag' AND op IN ('auto_tag_completion','auto_tag_running')),
        'content_leases',(SELECT count(*) FROM nextcloud_content_leases WHERE tenant_id={tenant} AND released_at_ms IS NULL),
        'body_leases',(SELECT count(DISTINCT l.lease_id) FROM original_body_leases l JOIN original_body_payloads b
          ON b.id=l.body_id WHERE b.tenant_id={tenant} AND l.released_at_ms IS NULL)))"""


def pending_receipt(snapshot, expected_tag_id):
    canonical_uuid(expected_tag_id)
    k = snapshot.get('knowledge') or {}
    require(k.get('channel') == 'nextcloud' and k.get('parse_status') == 'completed' and
            k.get('pending_subtasks_count') == 0 and k.get('enable_status') == 'enabled',
            'pending_auto_actual_completion_required')
    rows = snapshot.get('intents', [])
    require(len(rows) == 1, 'pending_auto_exact_one_intent_required')
    row = rows[0]
    require(type(row.get('id')) is int and row['id'] > 0 and row.get('op') == 'auto_tag_completion' and
            row.get('claimed_at') is None and row.get('fail_count') == 0 and
            row.get('task_type') == 'knowledge:auto_tag' and row.get('scope') == 'knowledge' and
            row.get('scope_id') == k.get('id') and row.get('tenant_id') == k.get('tenant_id'),
            'pending_auto_genuine_undelivered_intent_required')
    payload = json.loads(row['payload_json'])
    original = payload.get('_original_input') or {}
    task = payload.get('task_input') or {}
    require(all(payload.get(field) == k.get(field) for field in ('tenant_id', 'knowledge_base_id')) and
            payload.get('knowledge_id') == k.get('id'),
            'pending_auto_payload_scope_changed')
    require(all(original.get(field) == payload.get(field) for field in ('tenant_id', 'knowledge_base_id', 'knowledge_id')) and
            original.get('version') == 1 and type(original.get('build_generation')) is int and
            original['build_generation'] > 0 and HASH.fullmatch(original.get('input_digest', '')) and
            HASH.fullmatch(original.get('config_digest', '')) and HASH.fullmatch(original.get('body_digest', '')),
            'pending_auto_original_admission_required')
    canonical_uuid(original.get('admission_id')); canonical_uuid(task.get('ID'))
    canonical_uuid(payload.get('completion_input'))
    require(task['ID'] == row.get('dedup_key') and HASH.fullmatch(task.get('Digest', '')) and
            task['ID'] != payload['completion_input'], 'pending_auto_queue_model_identity_changed')
    manifests = snapshot.get('manifests', [])
    require(len(manifests) == 2 and {m.get('manifest_id') for m in manifests} == {task['ID'], payload['completion_input']},
            'pending_auto_double_actual_manifest_required')
    for m in manifests:
        require(m.get('tenant_id') == k.get('tenant_id') and m.get('knowledge_base_id') == k.get('knowledge_base_id') and
                m.get('knowledge_id') == k.get('id') and m.get('admission_id') == original['admission_id'] and
                HASH.fullmatch(m.get('input_digest', '')), 'pending_auto_manifest_scope_changed')
        head = m.get('head') or {}
        canonical_uuid(head.get('body_id'))
        require(head.get('state') == 'live' and head.get('storage_kind') == 'vault' and
                head.get('tenant_id') == k.get('tenant_id') and head.get('revision_matches') is True and
                type(head.get('revision')) is int and head['revision'] > 0 and HASH.fullmatch(head.get('body_sha256', '')),
                'pending_auto_original_body_head_required')
        if m['manifest_id'] == task['ID']:
            require(m['input_digest'] == task['Digest'], 'pending_auto_task_digest_changed')
    admissions, actors = snapshot.get('admissions', []), snapshot.get('actors', [])
    require(len(admissions) == len(actors) == 1 and admissions[0].get('admission_id') == actors[0].get('admission_id') == original['admission_id'] and
            admissions[0].get('origin_kind') == 'nextcloud' and admissions[0].get('build_generation') == original['build_generation'] and
            admissions[0].get('input_digest') == original['input_digest'] and admissions[0].get('config_digest') == original['config_digest'],
            'pending_auto_original_actor_or_generation_changed')
    require(snapshot.get('candidate_ids') == [expected_tag_id] and snapshot.get('tag_ids') == [],
            'pending_auto_exact_unassigned_candidate_required')
    require(snapshot.get('published') == 1, 'pending_auto_current_publication_required')
    return {'knowledge_id': k['id'], 'knowledge_base_id': k['knowledge_base_id'], 'tenant_id': k['tenant_id'],
            'intent_id': row['id'], 'queue_manifest_id': task['ID'], 'model_manifest_id': payload['completion_input'],
            'payload_sha256': hashlib.sha256(row['payload_json'].encode()).hexdigest(),
            'admission_id': original['admission_id'], 'build_generation': original['build_generation'],
            'actor_selector_sha256': digest(actors[0]), 'admission_sha256': digest(admissions[0]),
            'manifests': sorted(manifests, key=lambda m: m['manifest_id']), 'expected_tag_id': expected_tag_id}


def pending_quiescence(snapshot):
    stats = snapshot.get('stats') or {}
    required = {'running_syncs': 0, 'other_processing': 0, 'staged': 0, 'active_auto': 1,
                'content_leases': 0, 'body_leases': 0}
    require(set(stats) == set(required) and all(type(stats[k]) is int and stats[k] == v for k, v in required.items()),
            'pending_auto_full_quiescence_required')
    return dict(stats)


def released_model_window(status, operation):
    canonical_uuid(operation)
    item = status.get('operation') or {}
    require(status.get('enabled') is True and item.get('operation_id') == operation and
            item.get('kind') in ('summary', 'question') and item.get('state') == 'response_finished' and
            item.get('wait_result') == 'released', 'pending_auto_real_released_response_required')
    base = item.get('baseline_calls') or {}
    calls = status.get('calls') or {}
    require(set(base) == set(calls) == set(KINDS), 'pending_auto_call_telemetry_missing')
    for kind in KINDS:
        for field in ('started', 'completed', 'failed'):
            require(type(base[kind].get(field)) is int and type(calls[kind].get(field)) is int and
                    calls[kind][field] >= base[kind][field], 'pending_auto_call_telemetry_changed')
    require(calls['auto_tag'] == base['auto_tag'] and calls[item['kind']]['started'] - base[item['kind']]['started'] == 1 and
            calls[item['kind']]['completed'] - base[item['kind']]['completed'] == 1 and
            all(calls[k]['failed'] == base[k]['failed'] for k in KINDS), 'pending_auto_model_called_or_response_failed')
    return {'operation_id': operation, 'request_sequence': item['request_sequence'],
            'request_sha256': item['request_sha256'], 'calls': calls, 'baseline_calls': base}


def startup_result(snapshot, original, model_before, model_after, rejected=False):
    # Preserve the exact immutable payload/manifests/actor. Only the durable
    # state and a genuine generated relation may change at the consumer.
    normalized = json.loads(json.dumps(snapshot))
    rows = normalized.get('intents', [])
    require(len(rows) == 1, 'pending_auto_startup_intent_missing_or_recreated')
    row = rows[0]
    require(row.get('op') == ('auto_tag_rejected' if rejected else 'auto_tag_done') and
            row.get('claimed_at') is None and row.get('fail_count') == (1 if rejected else 0),
            'pending_auto_startup_not_terminal')
    actual_tags = normalized.get('tag_ids')
    require(actual_tags == ([] if rejected else [original['expected_tag_id']]), 'pending_auto_wrong_actual_tag_publication')
    normalized['tag_ids'] = []
    row.update(op='auto_tag_completion', fail_count=0)
    require(pending_receipt(normalized, original['expected_tag_id']) == original, 'pending_auto_original_intent_replaced')
    before, after = model_before.get('calls', {}), model_after.get('calls', {})
    require(set(before) == set(after) == set(KINDS), 'pending_auto_startup_telemetry_missing')
    for kind in KINDS:
        expected = {'started': 0, 'completed': 0, 'failed': 0}
        if kind == 'auto_tag' and not rejected:
            expected.update(started=1, completed=1)
        require(all(type(after[kind].get(f)) is int and type(before[kind].get(f)) is int and
                    after[kind][f] - before[kind][f] == n for f, n in expected.items()),
                'pending_auto_startup_call_count_changed')
    return {'terminal_op': 'auto_tag_rejected' if rejected else 'auto_tag_done',
            'actual_auto_model_calls': 0 if rejected else 1, 'actual_tag_relations': len(actual_tags),
            'immutable_original_intent_retained': True}


def model_control(owner, directory, state, payload=None):
    require(type(state.get('mock_postprocess_control_max_seconds')) is int and
            0 < state['mock_postprocess_control_max_seconds'] <= 60 and HASH.fullmatch(state.get('mock_model_code_sha256', '')),
            'fresh_pending_auto_fixture_opt_in_required')
    directory = Path(directory)
    actual_directory, actual_state = owner['owned_state'](directory)
    require(actual_directory == directory and actual_state == state, 'pending_auto_frozen_owner_changed')
    owner['assert_owned_resources'](directory, state)
    item = owner['docker_inspect']('container', state['project'] + '-mock-embedding-1')
    labels = item.get('Config', {}).get('Labels') or {}
    require(item.get('State', {}).get('Running') is True and
            re.fullmatch(r'[0-9a-f]{64}', item.get('Id', '')) and
            labels.get(owner['OWNER_LABEL']) == state['owner_token'] and
            labels.get('com.docker.compose.project') == state['project'] and
            labels.get('com.docker.compose.service') == 'mock-embedding' and
            item['Config'].get('Image') == owner['PYTHON_IMAGE'], 'pending_auto_exact_model_container_required')
    code = ('import json,sys,urllib.request; p=json.loads(sys.argv[1]); '
            'u="http://127.0.0.1:8000/_fixture/postprocess-control"; '
            'r=urllib.request.Request(u,data=None if p is None else json.dumps(p).encode(),'
            'headers={"Content-Type":"application/json"}); '
            'print(urllib.request.urlopen(r,timeout=5).read().decode())')
    result = subprocess.run(['docker', 'exec', item['Id'], 'python', '-c', code, json.dumps(payload)],
                            check=True, text=True, capture_output=True, timeout=10)
    owner['assert_owned_resources'](directory, state)
    require(owner['docker_inspect']('container', item['Id'])['State']['Running'] is True,
            'pending_auto_model_exited_during_control')
    return json.loads(result.stdout)
