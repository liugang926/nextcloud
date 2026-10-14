#!/usr/bin/env python3
"""Fixture-free method regressions. No full-app runtime acceptance is produced."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import tempfile
import threading
import unittest
from unittest import mock
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


class ClosedReceiptReuseTest(unittest.TestCase):
    def receipt(self):
        return {'candidate':{'profile':'rag','app':{'image_id':'sha256:'+'a'*64}},
                'checkpoint_sha256':'b'*64,'original':C['pending_receipt'](sample(),uid(7)),'accepted':False}

    def test_private_receipt_is_created_once_then_reused_without_rewriting(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root).resolve()/'closed-pending-gate.json';value=self.receipt()
            self.assertFalse(P['keep_exact_closed_receipt'](path,value))
            before=path.read_bytes();stat=path.stat()
            self.assertTrue(P['keep_exact_closed_receipt'](path,copy.deepcopy(value)))
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual((path.stat().st_ino,path.stat().st_mtime_ns),(stat.st_ino,stat.st_mtime_ns))

    def test_changed_candidate_checkpoint_original_heads_and_extra_fields_are_refused(self):
        for change in ('candidate','checkpoint','original','source','head','generation','accepted','extra'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as root:
                path=Path(root).resolve()/'closed-pending-gate.json';value=self.receipt()
                P['keep_exact_closed_receipt'](path,value);before=path.read_bytes();inode=path.stat().st_ino
                changed=copy.deepcopy(value)
                if change=='candidate':changed['candidate']['app']['image_id']='sha256:'+'c'*64
                elif change=='checkpoint':changed['checkpoint_sha256']='d'*64
                elif change=='original':changed['original']['payload_sha256']='e'*64
                elif change=='source':changed['original']['knowledge_id']=uid(90)
                elif change=='head':changed['original']['manifests'][0]['head']['body_id']=uid(91)
                elif change=='generation':changed['original']['build_generation']+=1
                elif change=='accepted':changed['accepted']=True
                else:changed['forged_bypass']=True
                with self.assertRaisesRegex(RuntimeError,'pending_closed_receipt_changed'):
                    P['keep_exact_closed_receipt'](path,changed)
                self.assertEqual(path.read_bytes(),before);self.assertEqual(path.stat().st_ino,inode)

    def test_semantically_equal_tampered_bytes_and_duplicate_json_keys_are_refused(self):
        for change in ('whitespace','duplicate-key'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as root:
                path=Path(root).resolve()/'closed-pending-gate.json';value=self.receipt()
                P['keep_exact_closed_receipt'](path,value)
                raw=json.dumps(value).encode() if change=='whitespace' else path.read_bytes().replace(b'{',b'{"accepted": false,',1)
                path.write_bytes(raw);inode=path.stat().st_ino
                with self.assertRaisesRegex(RuntimeError,'pending_closed_receipt_changed'):
                    P['keep_exact_closed_receipt'](path,value)
                self.assertEqual(path.read_bytes(),raw);self.assertEqual(path.stat().st_ino,inode)

    def test_symlink_hardlink_and_unsafe_mode_are_not_adopted(self):
        for change in ('symlink','broken-symlink','hardlink','mode'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as root:
                directory=Path(root).resolve();path=directory/'closed-pending-gate.json';value=self.receipt()
                P['keep_exact_closed_receipt'](path,value)
                before=path.read_bytes()
                if change in ('symlink','broken-symlink'):
                    moved=directory/'other-receipt';path.rename(moved)
                    path.symlink_to(moved if change=='symlink' else directory/'missing')
                elif change=='hardlink':os.link(path,directory/'other-receipt')
                else:path.chmod(0o644)
                with self.assertRaises(RuntimeError):P['keep_exact_closed_receipt'](path,value)
                if change!='broken-symlink':self.assertEqual(path.read_bytes(),before)
                if change in ('symlink','broken-symlink'):self.assertTrue(path.is_symlink())

    def closed_flow(self,directory):
        # These are method ports recording which live boundaries the driver
        # invokes. They attest ordering only; no SQL/CLI/runtime is simulated
        # into an acceptance receipt or claim.
        probe=object.__new__(P['PendingRestore']);probe.evidence=directory;probe.provenance={'profile':'rag','app':{'image_id':'sha256:'+'a'*64}}
        probe.directory=directory;probe.project='nc-synldap-a1b2c3d4';probe.state={'project':probe.project}
        probe.calls=[];probe.failed_gate=None
        row=sample();original=C['pending_receipt'](row,uid(7))
        inventory=directory/'original-inventory.json';P['write_json'](inventory,{'scope':'original-private-test-metadata'})
        checkpoint={'identity':{'scope':'same-original'},'pending_auto':{'receipt':original},
            'external_recovery_anchor':{'instance_id':uid(20),'stream_id':uid(21),'record_sha256':'c'*64,
                'sequence':1,'database_chain_sha256':'d'*64},'original_inventory':{'path':str(inventory)}}
        P['write_json'](directory/'checkpoint.json',checkpoint)
        restored={'checkpoint_sha256':P['sha'](directory/'checkpoint.json'),'actual_dual_full_pg_restore':True,
            'original_pending_intent_restored':True,'actual_nc_configuration_restored':True,
            'configuration_controls':{'actual_checkpoint_controls_restored':True,'fault_absent_after_restore':True},
            'current_body_or_independent_publication_anchors_restored':False,
            'actual_original_data_volumes_restored':sorted(P['N']['DATA_VOLUMES'])}
        P['write_json'](directory/'actual-data-restore.json',restored)
        P['write_json'](directory/'actual-fault.json',{'fault_file_id':77,'fault_file_name':'pending-full-restore-fault-test.txt'})
        def gate(name):
            probe.calls.append(name)
            if probe.failed_gate==name:raise RuntimeError('method_gate_refused_'+name)
        probe.pending_checkpoint=lambda:checkpoint
        probe.actors=lambda:['wk-app','nextcloud']
        probe.stopped=lambda service:gate('stopped_'+service)
        probe.no_external_workers=lambda:gate('workers')
        probe.restored_configuration=lambda *args:gate('configuration')
        probe.packaged_tools=lambda:gate('tools')
        probe.body_cli=lambda mode:gate('body_'+mode)
        probe.identity=lambda:(gate('identity') or checkpoint['identity'])
        probe.target_snapshot=lambda knowledge:(gate('original_snapshot') or copy.deepcopy(row))
        probe.publication_cli=lambda *args:(gate('source_inventory') or json.loads(inventory.read_text()))
        probe.sql=lambda *args,**kwargs:(gate('filecache') or 0)
        probe.owned=lambda:gate('owned_resource')
        fake=dict(P['N']);fake['pinned_publication']=lambda *args:gate('publication_current')
        fake['command']=lambda *args,**kwargs:(gate('physical_file') or b'{"fault_file_absent": true}')
        return probe,fake

    def test_every_closed_call_rechecks_all_boundaries_and_keeps_exact_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root).resolve();probe,fake=self.closed_flow(directory)
            with mock.patch.dict(P['PendingRestore'].closed.__globals__,{'N':fake}):
                probe.closed();path=directory/'closed-pending-gate.json';before=path.read_bytes();inode=path.stat().st_ino
                first=list(probe.calls);probe.calls=[];probe.closed()
            self.assertEqual(probe.calls,first)
            for gate in ('configuration','body_reconcile','body_verify','identity','publication_current',
                         'original_snapshot','source_inventory','filecache','physical_file','owned_resource'):
                self.assertIn(gate,first)
            self.assertEqual(path.read_bytes(),before);self.assertEqual(path.stat().st_ino,inode)

    def test_reopen_repeats_closed_gates_before_first_start_without_rewriting_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root).resolve();probe,fake=self.closed_flow(directory)
            probe.compose=mock.Mock(side_effect=RuntimeError('method_reached_reopen_start_boundary'))
            with mock.patch.dict(P['PendingRestore'].closed.__globals__,{'N':fake}):
                probe.closed();path=directory/'closed-pending-gate.json';before=path.read_bytes();inode=path.stat().st_ino
                first=list(probe.calls);probe.calls=[]
                with self.assertRaisesRegex(RuntimeError,'method_reached_reopen_start_boundary'):probe.reopen()
            self.assertEqual(probe.calls,first)
            probe.compose.assert_called_once_with('start','nextcloud','mock-embedding','docreader')
            self.assertEqual(path.read_bytes(),before);self.assertEqual(path.stat().st_ino,inode)

    def test_existing_receipt_never_bypasses_a_changed_live_gate_on_reopen(self):
        for failed in ('configuration','body_verify','publication_current','source_inventory','filecache','physical_file','owned_resource'):
            with self.subTest(failed=failed),tempfile.TemporaryDirectory() as root:
                directory=Path(root).resolve();probe,fake=self.closed_flow(directory)
                probe.compose=mock.Mock(side_effect=AssertionError('must_not_open_after_failed_current_gate'))
                with mock.patch.dict(P['PendingRestore'].closed.__globals__,{'N':fake}):
                    probe.closed();path=directory/'closed-pending-gate.json';before=path.read_bytes();inode=path.stat().st_ino
                    probe.failed_gate=failed;probe.calls=[]
                    with self.assertRaisesRegex(RuntimeError,'method_gate_refused_'+failed):probe.reopen()
                probe.compose.assert_not_called();self.assertIn(failed,probe.calls)
                self.assertEqual(path.read_bytes(),before);self.assertEqual(path.stat().st_ino,inode)

    def test_reopen_rechecks_all_gates_but_preserves_and_refuses_tampered_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root).resolve();probe,fake=self.closed_flow(directory)
            probe.compose=mock.Mock(side_effect=AssertionError('must_not_open_after_changed_receipt'))
            with mock.patch.dict(P['PendingRestore'].closed.__globals__,{'N':fake}):
                probe.closed();path=directory/'closed-pending-gate.json';first=list(probe.calls)
                value=json.loads(path.read_text());value['accepted']=True
                changed=json.dumps(value,sort_keys=True,indent=2).encode()+b'\n';path.write_bytes(changed)
                inode=path.stat().st_ino;probe.calls=[]
                with self.assertRaisesRegex(RuntimeError,'pending_closed_receipt_changed'):probe.reopen()
            self.assertEqual(probe.calls,first);probe.compose.assert_not_called()
            self.assertEqual(path.read_bytes(),changed);self.assertEqual(path.stat().st_ino,inode)


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
