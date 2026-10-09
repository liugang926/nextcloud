#!/usr/bin/env python3
"""Fixture-free method regressions. No full-app runtime acceptance is produced."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import runpy
import tempfile
import threading
import unittest
import uuid

HERE = Path(__file__).resolve().parent
M = runpy.run_path(str(HERE.parents[1] / 'integration/mock_embedding.py'))
C = runpy.run_path(str(HERE / 'synthetic-ldap-pending-auto-contract.py'))
P = runpy.run_path(str(HERE / 'synthetic-ldap-pending-auto-restore.py'))
spec = importlib.util.spec_from_file_location('pending_auto_fixture_test', HERE / 'test-synthetic-ldap-fixture-state.py')
F = importlib.util.module_from_spec(spec); spec.loader.exec_module(F)


def uid(number):
    return str(uuid.UUID(int=number))


def sample():
    payload = {'tenant_id': 7, 'knowledge_base_id': uid(1), 'knowledge_id': uid(2),
               'completion_input': uid(3), 'task_input': {'ID': uid(4), 'Digest': 'a'*64},
               '_original_input': {'version': 1, 'tenant_id': 7, 'knowledge_base_id': uid(1),
                   'knowledge_id': uid(2), 'admission_id': uid(5), 'build_generation': 9,
                   'input_digest': 'b'*64, 'config_digest': 'c'*64, 'body_digest': 'd'*64}}
    manifests = [{'manifest_id': uid(n), 'tenant_id': 7, 'knowledge_base_id': uid(1),
                  'knowledge_id': uid(2), 'admission_id': uid(5), 'input_digest': 'a'*64,
                  'head': {'body_id': uid(n+20), 'body_sha256': 'e'*64, 'state': 'live',
                           'storage_kind': 'vault', 'revision': 1, 'tenant_id': 7, 'revision_matches': True}}
                 for n in (3, 4)]
    return {'knowledge': {'id': uid(2), 'tenant_id': 7, 'knowledge_base_id': uid(1), 'channel': 'nextcloud',
                         'parse_status': 'completed', 'pending_subtasks_count': 0, 'enable_status': 'enabled'},
            'intents': [{'id': 12, 'tenant_id': 7, 'task_type': 'knowledge:auto_tag', 'scope': 'knowledge',
                         'scope_id': uid(2), 'op': 'auto_tag_completion', 'dedup_key': uid(4),
                         'payload_json': json.dumps(payload), 'claimed_at': None, 'fail_count': 0}],
            'manifests': manifests, 'admissions': [{'admission_id': uid(5), 'origin_kind': 'nextcloud',
                         'build_generation': 9, 'input_digest': 'b'*64, 'config_digest': 'c'*64}],
            'actors': [{'admission_id': uid(5), 'principal_type': 'web_user', 'user_id': uid(6)}],
            'tag_ids': [], 'candidate_ids': [uid(7)], 'published': 1,
            'stats': {'running_syncs': 0, 'other_processing': 0, 'staged': 0, 'active_auto': 1,
                      'content_leases': 0, 'body_leases': 0}}


def calls():
    return {kind: {'started': 0, 'completed': 0, 'failed': 0} for kind in C['KINDS']}


SUMMARY = [{'role': 'system', 'content': 'You are a precise document profiling expert. Return "typical_question".'},
           {'role': 'user', 'content': 'ORCHID-QUARTZ-2749'}]
QUESTION = [{'role': 'user', 'content': 'You are a question generation assistant optimizing for search retrieval.\n<main_content>ORCHID-QUARTZ-2749</main_content>'}]
AUTO = [{'role': 'system', 'content': 'You classify one document using only the numbered tags supplied below.\nReturn strict JSON only.'},
        {'role': 'user', 'content': 'Candidate tags:\n1. Actual API Tag\n\n<document>\nORCHID-QUARTZ-2749\n</document>'}]


class PendingAutoContractTest(unittest.TestCase):
    def test_fault_requires_actual_last_enrichment_and_no_durable_intent_yet(self):
        row = sample(); row['knowledge'].update(parse_status='finalizing',pending_subtasks_count=1); row['intents']=[]
        baseline = calls(); current = calls(); current['question']['started']=1
        status={'operation':{'operation_id':uid(10),'kind':'question','state':'waiting_before_response','baseline_calls':baseline},'calls':current}
        P['last_real_enrichment'](row,status,uid(10))
        for change in ('completed','two-jobs','has-intent','other-work','timeout','auto-called','wrong-operation'):
            actual,model=copy.deepcopy(row),copy.deepcopy(status)
            if change=='completed':actual['knowledge']['parse_status']='completed'
            elif change=='two-jobs':actual['knowledge']['pending_subtasks_count']=2
            elif change=='has-intent':actual['intents']=sample()['intents']
            elif change=='other-work':actual['stats']['other_processing']=1
            elif change=='timeout':model['operation']['state']='response_finished'
            elif change=='auto-called':model['calls']['auto_tag']['started']=1
            else:model['operation']['operation_id']=uid(11)
            with self.subTest(change=change),self.assertRaises(RuntimeError):P['last_real_enrichment'](actual,model,uid(10))

    def test_pending_restore_requires_real_dual_db_all_files_and_original_payload(self):
        row=sample();receipt=C['pending_receipt'](row,uid(7))
        checkpoint={'pending_auto':{'receipt':receipt},'_actual_checkpoint_sha256':'f'*64}
        restored={'checkpoint_sha256':'f'*64,'actual_dual_full_pg_restore':True,
                  'original_pending_intent_restored':True,'actual_original_data_volumes_restored':sorted(P['N']['DATA_VOLUMES']),
                  'actual_nc_configuration_restored':True,'current_body_or_independent_publication_anchors_restored':False,
                  'configuration_controls':{'actual_checkpoint_controls_restored':True,'fault_absent_after_restore':True}}
        P['immutable_pending_restored'](checkpoint,restored,row)
        for change in ('no-dual-db','redis-only','wrong-checkpoint','new-payload','drained-idle'):
            actual,proof=copy.deepcopy(row),copy.deepcopy(restored)
            if change=='no-dual-db':proof['actual_dual_full_pg_restore']=False
            elif change=='redis-only':proof['actual_original_data_volumes_restored']=[]
            elif change=='wrong-checkpoint':proof['checkpoint_sha256']='a'*64
            elif change=='new-payload':actual['intents'][0]['payload_json']+=' '
            else:actual['stats']['active_auto']=0
            with self.subTest(change=change),self.assertRaises(RuntimeError):P['immutable_pending_restored'](checkpoint,proof,actual)

    def test_configuration_restoration_and_current_anchors_cannot_be_omitted_or_rolled_back(self):
        row=sample();receipt=C['pending_receipt'](row,uid(7))
        checkpoint={'pending_auto':{'receipt':receipt},'_actual_checkpoint_sha256':'f'*64}
        restored={'checkpoint_sha256':'f'*64,'actual_dual_full_pg_restore':True,
                  'original_pending_intent_restored':True,'actual_original_data_volumes_restored':sorted(P['N']['DATA_VOLUMES']),
                  'actual_nc_configuration_restored':True,'current_body_or_independent_publication_anchors_restored':False,
                  'configuration_controls':{'actual_checkpoint_controls_restored':True,'fault_absent_after_restore':True}}
        for change in ('config-not-restored','controls-not-restored','query-fault-survives','current-anchor-rolled-back'):
            proof=copy.deepcopy(restored)
            if change=='config-not-restored':proof['actual_nc_configuration_restored']=False
            elif change=='controls-not-restored':proof['configuration_controls']['actual_checkpoint_controls_restored']=False
            elif change=='query-fault-survives':proof['configuration_controls']['fault_absent_after_restore']=False
            else:proof['current_body_or_independent_publication_anchors_restored']=True
            with self.subTest(change=change),self.assertRaises(RuntimeError):P['immutable_pending_restored'](checkpoint,proof,row)

    def test_genuine_pending_shape_and_quiescence_are_distinct_from_idle(self):
        row = sample(); receipt = C['pending_receipt'](row, uid(7))
        self.assertEqual(receipt['queue_manifest_id'], uid(4))
        self.assertEqual(C['pending_quiescence'](row)['active_auto'], 1)
        row['stats']['active_auto'] = 0
        with self.assertRaises(RuntimeError): C['pending_quiescence'](row)

    def test_drained_or_running_or_delivered_or_failed_is_not_pending(self):
        for change in ({'op': 'auto_tag_done'}, {'op': 'auto_tag_running', 'claimed_at': 'old'},
                       {'claimed_at': 'delivery_attempt'}, {'fail_count': 1}, {'dedup_key': uid(90)}):
            with self.subTest(change=change):
                row = sample(); row['intents'][0].update(change)
                with self.assertRaises(RuntimeError): C['pending_receipt'](row, uid(7))
        row = sample(); row['intents'] = []
        with self.assertRaises(RuntimeError): C['pending_receipt'](row, uid(7))

    def test_original_double_manifest_and_live_body_heads_are_required(self):
        for field, value in (('state', 'purged'), ('revision_matches', False), ('storage_kind', 'inline'),
                             ('tenant_id', 8), ('body_sha256', ''), ('body_id', None)):
            with self.subTest(field=field):
                row = sample(); row['manifests'][0]['head'][field] = value
                with self.assertRaises((RuntimeError, ValueError, AttributeError)): C['pending_receipt'](row, uid(7))
        for mutation in ('missing', 'third', 'wrong-admission', 'wrong-queue-digest'):
            row = sample()
            if mutation == 'missing': row['manifests'].pop()
            elif mutation == 'third': row['manifests'].append(copy.deepcopy(row['manifests'][0]))
            elif mutation == 'wrong-admission': row['manifests'][0]['admission_id'] = uid(90)
            else: row['manifests'][1]['input_digest'] = 'f'*64
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError): C['pending_receipt'](row, uid(7))

    def test_true_completed_signed_generation_and_original_actor_are_required(self):
        for mutation in ('finalizing', 'pending-counter', 'unsigned', 'wrong-generation', 'missing-actor', 'wrong-origin'):
            row = sample()
            if mutation == 'finalizing': row['knowledge']['parse_status'] = 'finalizing'
            elif mutation == 'pending-counter': row['knowledge']['pending_subtasks_count'] = 1
            elif mutation == 'unsigned': row['admissions'] = []
            elif mutation == 'wrong-generation': row['admissions'][0]['build_generation'] = 10
            elif mutation == 'missing-actor': row['actors'] = []
            else: row['admissions'][0]['origin_kind'] = 'upload'
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError): C['pending_receipt'](row, uid(7))

    def test_candidate_is_exact_api_identity_and_has_no_old_relation(self):
        for candidates, tags, published in (([uid(7), uid(8)], [], 1), ([uid(8)], [], 1),
                                            ([uid(7)], [uid(7)], 1), ([uid(7)], [], 0)):
            row = sample(); row.update(candidate_ids=candidates, tag_ids=tags, published=published)
            with self.subTest(candidates=candidates, tags=tags, published=published), self.assertRaises(RuntimeError):
                C['pending_receipt'](row, uid(7))

    def test_every_other_work_and_unreleased_lease_prevents_checkpoint(self):
        for field in sample()['stats']:
            row = sample(); row['stats'][field] += 1
            with self.subTest(field=field), self.assertRaises(RuntimeError): C['pending_quiescence'](row)
        row = sample(); row['stats']['unknown'] = 0
        with self.assertRaises(RuntimeError): C['pending_quiescence'](row)

    def test_pending_sql_is_read_only_uses_payload_tenant_and_never_reads_vault_body(self):
        query = C['pending_snapshot_query'](7, uid(1), uid(2))
        self.assertIn('original_body_leases l JOIN original_body_payloads b', query)
        self.assertIn('WHERE b.tenant_id=7 AND l.released_at_ms IS NULL', query)
        self.assertNotIn('parent_refs', query)
        self.assertNotIn('expires_at_ms', query)
        self.assertNotIn('actual_inputs', query.replace("h.column_name='actual_inputs'", ''))
        self.assertNotRegex(query.upper(), r'\b(INSERT|UPDATE|DELETE|TRUNCATE)\b')
        for tenant, kb in ((True, uid(1)), (0, uid(1)), (7, "' OR TRUE--")):
            with self.assertRaises((RuntimeError, ValueError)): C['pending_snapshot_query'](tenant, kb, uid(2))

    def test_startup_exact_one_model_and_relation_keeps_original_intent(self):
        row = sample(); original = C['pending_receipt'](row, uid(7))
        row['intents'][0]['op'] = 'auto_tag_done'; row['tag_ids'] = [uid(7)]
        before = {'calls': calls()}; after = copy.deepcopy(before)
        after['calls']['auto_tag'].update(started=1, completed=1)
        self.assertEqual(C['startup_result'](row, original, before, after)['actual_auto_model_calls'], 1)
        self.assertEqual(row['intents'][0]['op'], 'auto_tag_done', 'validation must not rewrite the observation')
        for mutation in ('extra-call', 'recreated-row', 'actor-change', 'body-change', 'new-payload', 'wrong-tag'):
            changed, more = copy.deepcopy(row), copy.deepcopy(after)
            if mutation == 'extra-call': more['calls']['auto_tag'].update(started=2, completed=2)
            elif mutation == 'recreated-row': changed['intents'][0]['id'] += 1
            elif mutation == 'actor-change': changed['actors'][0]['user_id'] = uid(88)
            elif mutation == 'body-change': changed['manifests'][0]['head']['body_sha256'] = 'f'*64
            elif mutation == 'new-payload': changed['intents'][0]['payload_json'] += ' '
            else: changed['tag_ids'] = [uid(8)]
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError): C['startup_result'](changed, original, before, more)

    def test_rejected_startup_requires_zero_actual_model_calls_and_no_relation(self):
        row = sample(); original = C['pending_receipt'](row, uid(7))
        row['intents'][0].update(op='auto_tag_rejected', fail_count=1)
        status = {'calls': calls()}
        self.assertEqual(C['startup_result'](row, original, status, status, True)['actual_auto_model_calls'], 0)
        after = copy.deepcopy(status); after['calls']['auto_tag'].update(started=1, completed=1)
        with self.assertRaises(RuntimeError): C['startup_result'](row, original, status, after, True)
        row['tag_ids'] = [uid(7)]
        with self.assertRaises(RuntimeError): C['startup_result'](row, original, status, status, True)


class PostprocessControlTest(unittest.TestCase):
    def test_disabled_default_and_strict_bounded_opt_in(self):
        schedule = M['PostprocessSchedule']()
        self.assertFalse(schedule.status()['enabled'])
        with self.assertRaises(ValueError): schedule.arm(uid(10), 'question', 1, 'Actual API Tag')
        with self.assertRaises(ValueError): schedule.configure('Actual API Tag')
        for value in (True, -1, 61, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError): M['PostprocessSchedule'](value)

    def test_production_prompt_roles_and_decoder_schemas(self):
        schedule = M['PostprocessSchedule'](60); schedule.configure('Actual API Tag')
        for messages, expected in ((SUMMARY, 'summary'), (QUESTION, 'question'), (AUTO, 'auto_tag')):
            self.assertEqual(M['postprocess_kind'](messages), expected)
        self.assertEqual(set(json.loads(schedule.answer('summary', SUMMARY))),
                         {'summary', 'gist', 'topics', 'doc_type', 'typical_question'})
        self.assertNotIn('\n', schedule.answer('question', QUESTION))
        self.assertEqual(json.loads(schedule.answer('auto_tag', AUTO)), {'matches': [{'index': 1, 'confidence': .95}]})
        spoof = [{'role': 'user', 'content': AUTO[0]['content']}]
        self.assertEqual(M['postprocess_kind'](spoof), 'other')

    def test_auto_never_guesses_another_candidate_or_uses_uuid_in_decoder(self):
        schedule = M['PostprocessSchedule'](60); schedule.configure('Actual API Tag')
        for suffix in ('1. Different Tag', '1. Actual API Tag\n2. Other Tag', '2. Actual API Tag', ''):
            messages = copy.deepcopy(AUTO)
            messages[1]['content'] = 'Candidate tags:\n' + suffix + '\n\n<document>\nORCHID-QUARTZ-2749\n</document>'
            with self.subTest(candidates=suffix): self.assertEqual(json.loads(schedule.answer('auto_tag', messages)), {'matches': []})
        with self.assertRaises(ValueError): schedule.configure('Changed Actual Tag')

    def test_actual_nonstream_request_waits_before_response_and_telemetry_is_body_free(self):
        schedule = M['PostprocessSchedule'](60)
        schedule.arm(uid(10), 'question', 60, 'Actual API Tag')
        # Another genuine production call is counted but cannot claim the pause.
        summary = schedule.begin('summary', SUMMARY); schedule.finish(summary, True)
        call = schedule.begin('question', QUESTION)
        self.assertEqual(schedule.status()['operation']['state'], 'waiting_before_response')
        worker = threading.Thread(target=schedule.wait, args=(call,)); worker.start()
        worker.join(.01); self.assertTrue(worker.is_alive())
        schedule.release(uid(10)); worker.join(1); self.assertFalse(worker.is_alive())
        schedule.finish(call, True)
        status = schedule.status(); C['released_model_window'](status, uid(10))
        raw = json.dumps(status)
        self.assertNotIn('ORCHID', raw); self.assertNotIn('Actual API Tag', raw); self.assertNotIn('release"', raw)
        with self.assertRaises(ValueError): schedule.finish(call, True)

    def test_automatic_timeout_disconnection_or_actual_auto_call_is_not_fault_boundary(self):
        for mode in ('timeout', 'disconnected', 'auto-called'):
            schedule = M['PostprocessSchedule'](60); schedule.arm(uid(10), 'question', .002, 'Actual API Tag')
            call = schedule.begin('question', QUESTION)
            if mode != 'timeout': schedule.release(uid(10))
            schedule.wait(call); schedule.finish(call, mode != 'disconnected')
            if mode == 'auto-called':
                other = schedule.begin('auto_tag', AUTO); schedule.finish(other, True)
            with self.subTest(mode=mode), self.assertRaises(RuntimeError): C['released_model_window'](schedule.status(), uid(10))

    def test_only_exact_current_waiting_operation_can_release(self):
        schedule = M['PostprocessSchedule'](60); schedule.arm(uid(10), 'question', 60, 'Actual API Tag')
        with self.assertRaises(ValueError): schedule.release(uid(10))
        call = schedule.begin('question', QUESTION)
        with self.assertRaises(ValueError): schedule.release(uid(11))
        with self.assertRaises(ValueError): schedule.arm(uid(11), 'summary', 60, 'Actual API Tag')
        schedule.release(uid(10)); schedule.wait(call); schedule.finish(call, True)

    def test_fresh_fixture_freezes_exact_mock_code_and_postprocess_env(self):
        for mutation in ('none', 'maximum', 'env', 'no-hash', 'file-changed'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix='nc-pending-auto-method-') as scratch:
                directory = Path(scratch).resolve(); state, compose = F.sample(directory, ui=False)
                code = directory/'mock_embedding.py'; code.write_text('owned exact fixture source'); code.chmod(0o600)
                state.update(mock_model_code_sha256=hashlib.sha256(code.read_bytes()).hexdigest(), mock_postprocess_control_max_seconds=60)
                compose['services']['mock-embedding']['volumes'] = [f'{code}:/srv/mock_embedding.py:ro']
                compose['services']['mock-embedding']['environment'] = {'MOCK_POSTPROCESS_CONTROL_MAX_SECONDS': '60'}
                if mutation == 'maximum': state['mock_postprocess_control_max_seconds'] = True
                elif mutation == 'env': compose['services']['mock-embedding']['environment']['MOCK_POSTPROCESS_CONTROL_MAX_SECONDS'] = '59'
                elif mutation == 'no-hash': del state['mock_model_code_sha256']
                elif mutation == 'file-changed': code.write_text('changed exact fixture source')
                state['compose_fingerprint'] = F.fixture.compose_fingerprint(compose); F.write(directory, state, compose)
                if mutation == 'none': self.assertEqual(F.fixture.owned_state(directory)[1], state)
                else:
                    with self.assertRaises(RuntimeError): F.fixture.owned_state(directory)


if __name__ == '__main__': unittest.main()
