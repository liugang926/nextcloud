#!/usr/bin/env python3
"""Pure scheduling/parser/fixture-freeze tests; no normal app proof is produced."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
M=runpy.run_path(str(HERE.parents[1]/'integration/mock_embedding.py'))
R=runpy.run_path(str(HERE/'synthetic-ldap-running-replay-acceptance.py'))
C=runpy.run_path(str(HERE/'synthetic-ldap-normal-restore.py'))
spec=importlib.util.spec_from_file_location('resource_running_fixture_test',HERE/'test-synthetic-ldap-fixture-state.py')
F=importlib.util.module_from_spec(spec);spec.loader.exec_module(F)


class RunningReplayPreparationTest(unittest.TestCase):
    def test_default_models_do_not_pause_or_allow_control(self):
        schedule=M['StreamSchedule']()
        self.assertIsNone(schedule.claim())
        self.assertFalse(schedule.status()['enabled'])
        with self.assertRaises(ValueError):schedule.arm(str(uuid.uuid4()),1)

    def test_schedule_maximum_and_operation_identity(self):
        for invalid in (-1,21,True,2.5):
            with self.subTest(value=invalid),self.assertRaises(ValueError):M['StreamSchedule'](invalid)
        schedule=M['StreamSchedule'](20);operation=str(uuid.uuid4())
        schedule.arm(operation,20)
        self.assertEqual(schedule.arm(operation,20)['operation']['state'],'armed')
        with self.assertRaises(ValueError):schedule.arm(str(uuid.uuid4()),20)
        with self.assertRaises(ValueError):schedule.release(str(uuid.uuid4()))

    def test_first_stream_normal_second_real_delta_wait_and_release(self):
        schedule=M['StreamSchedule'](20)
        self.assertIsNone(schedule.claim())
        operation=str(uuid.uuid4());schedule.arm(operation,20);item=schedule.claim()
        self.assertEqual(item['claimed_stream_sequence'],2)
        schedule.first_flushed(item)
        self.assertEqual(schedule.status()['operation']['state'],'waiting_after_first_delta')
        worker=threading.Thread(target=schedule.wait,args=(item,));worker.start()
        worker.join(.02);self.assertTrue(worker.is_alive())
        schedule.release(operation);worker.join(1);self.assertFalse(worker.is_alive())
        schedule.finish(item,True)
        self.assertEqual(schedule.status()['operation']['state'],'finished')
        json.dumps(schedule.status())
        self.assertNotIn('release',schedule.status()['operation'])

    def test_schedule_has_bounded_automatic_timeout(self):
        schedule=M['StreamSchedule'](1);schedule.arm(str(uuid.uuid4()),.03);item=schedule.claim()
        schedule.first_flushed(item);start=time.monotonic();schedule.wait(item)
        self.assertGreaterEqual(time.monotonic()-start,.02)
        self.assertLess(time.monotonic()-start,1)
        schedule.finish(item,False)
        self.assertEqual(schedule.status()['operation']['state'],'stream_disconnected')

    def test_running_frame_cannot_use_another_producer_or_new_body(self):
        request=str(uuid.uuid4());first={'id':request,'response_type':'answer','content':'actual prefix'}
        R['running_frame_matches'](first,dict(first),request)
        for change in ({'id':str(uuid.uuid4())},{'content':'changed current source'},{'response_type':'error'}):
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                R['running_frame_matches'](first,{**first,**change},request)

    def test_running_reference_material_is_exact(self):
        request=str(uuid.uuid4());first={'id':request,'response_type':'references',
            'data':{'references':[{'knowledge_id':'original','content':'actual original reference'}]}}
        R['running_frame_matches'](first,copy.deepcopy(first),request)
        changed=copy.deepcopy(first);changed['data']['references'][0]['content']='new source substitution'
        with self.assertRaises(RuntimeError):R['running_frame_matches'](first,changed,request)

    def test_references_alone_cannot_satisfy_actual_answer_prefix(self):
        self.assertFalse(R['answer_prefix']({'response_type':'references','data':{'references':[{'id':'citation'}]}}))
        self.assertFalse(R['answer_prefix']({'response_type':'answer','content':''}))
        self.assertTrue(R['answer_prefix']({'response_type':'answer','content':'actual model prefix'}))

    def test_running_native_cannot_make_a_second_model_call_or_finish_early(self):
        operation=str(uuid.uuid4());status={'stream_sequence':2,'operation':{
            'operation_id':operation,'claimed_stream_sequence':2,'state':'waiting_after_first_delta'}}
        R['unchanged_model_window'](status,operation,2)
        for change in ('extra-call','complete','other-operation'):
            modified=copy.deepcopy(status)
            if change=='extra-call':modified['stream_sequence']=3
            elif change=='complete':modified['operation']['state']='finished'
            else:modified['operation']['operation_id']=str(uuid.uuid4())
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                R['unchanged_model_window'](modified,operation,2)
        status['operation']['state']='finished'
        R['unchanged_model_window'](status,operation,2,finished=True)

    def test_pending_error_or_eof_cannot_be_a_running_window(self):
        for event in (None,{'response_type':'error','done':True}):
            reader=R['SSEReader'](urllib.request.Request('http://127.0.0.1:1/unused'))
            reader.status=503;reader.incoming.put(event)
            with self.subTest(event=event),self.assertRaises(RuntimeError):
                reader.wait_event(R['legal_body'],timeout=.1)

    def test_http_worker_join_reports_actual_thread_exit(self):
        reader=R['SSEReader'](urllib.request.Request('http://127.0.0.1:1/unused'))
        reader.thread=threading.Thread(target=time.sleep,args=(.02,))
        reader.thread.start()
        self.assertTrue(reader.cancel_and_join())
        self.assertFalse(reader.thread.is_alive())
        self.assertTrue(reader.metadata()['reader_joined'])
        self.assertFalse(reader.metadata()['transport_eof_observed'])

    def test_worker_cleanup_failure_revokes_acceptance_and_preserves_observations(self):
        window={'actual_body_sha256':'a'*64,'producer_is_completed':False}
        for joined,removed in (([True,False],False),([True,True],False)):
            with self.subTest(joined=joined,removed=removed):
                result={'running_replay_accepted':True,'actual_running_window':dict(window)}
                R['record_worker_cleanup'](result,joined,removed)
                self.assertFalse(result['running_replay_accepted'])
                self.assertEqual(result['terminal'],1)
                self.assertEqual(result['cleanup_error_code'],'actual_running_workers_not_cleanly_joined')
                self.assertEqual(result['actual_running_window'],window)
        result={'running_replay_accepted':True,'actual_running_window':dict(window)}
        R['record_worker_cleanup'](result,[True,True],True)
        self.assertTrue(result['running_replay_accepted'])
        self.assertEqual(result['terminal'],0)

    def test_clean_worker_exit_does_not_erase_an_actual_body_failure(self):
        result={'running_replay_accepted':False,'terminal':1,'safe_reason':'actual_body_failure'}
        R['record_worker_cleanup'](result,[True,True],True)
        self.assertFalse(result['running_replay_accepted'])
        self.assertEqual(result['terminal'],1)
        self.assertEqual(result['safe_reason'],'actual_body_failure')

    def test_source_denied_terminal_is_static_and_has_no_fake_completion(self):
        request=str(uuid.uuid4());error={'id':request,'response_type':'error','done':True,
            'content':R['SOURCE_DENIED_CONTENT'],'data':{'error_code':'source_access_changed'}}
        result=R['require_post_revoke_source_denied']([error],request,'actual withheld tail','http://source/f/92')
        self.assertEqual(result['error_code'],'source_access_changed')
        for change in ({'done':False},{'content':'private backend diagnostic'},
                       {'data':{'error_code':'generation_failed'}},{'id':str(uuid.uuid4())}):
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                R['require_post_revoke_source_denied']([{**error,**change}],request,'actual withheld tail','http://source/f/92')
        for leaked in ({'id':request,'response_type':'answer','content':'actual withheld tail'},
                       {'id':request,'response_type':'references','data':{'references':[{'metadata':{'nextcloud_human_url':'http://source/f/92'}}]}},
                       {'id':request,'response_type':'complete','done':True,'data':{'final_content':'actual withheld tail'}}):
            with self.subTest(leaked=leaked),self.assertRaises(RuntimeError):
                R['require_post_revoke_source_denied']([leaked,error],request,'actual withheld tail','http://source/f/92')

    def test_pre_authorised_prefix_is_not_relabelled_as_post_revoke_body(self):
        request=str(uuid.uuid4());prefix={'id':request,'response_type':'answer','content':'actual permitted prefix'}
        terminal={'id':request,'response_type':'error','done':True,'content':R['SOURCE_DENIED_CONTENT'],
                  'data':{'error_code':'source_access_changed'}}
        observed=[prefix,terminal];offset=1
        R['require_post_revoke_source_denied'](observed[offset:],request,'withheld tail','http://source/f/92')
        with self.assertRaises(RuntimeError):
            R['require_post_revoke_source_denied'](observed,request,'withheld tail','http://source/f/92')

    def test_negative_acceptance_also_depends_on_actual_worker_cleanup(self):
        result={'source_midstream_revocation_accepted':True,'running_replay_accepted':False,
                'actual_running_window':{'observed':True}}
        R['record_worker_cleanup'](result,[True,False],False)
        self.assertFalse(result['source_midstream_revocation_accepted'])
        self.assertFalse(result['running_replay_accepted'])
        self.assertEqual(result['terminal'],1)
        self.assertEqual(result['actual_running_window'],{'observed':True})

    def test_current_journal_can_append_but_cannot_rollback_or_rewrite_prefix(self):
        before={'journal_id':'actual-journal','sequence':1,'head_sha256':'head1',
                'event_digests':['digest1'],'event_heads':['head1']}
        after={'journal_id':'actual-journal','sequence':2,'head_sha256':'head2',
               'event_digests':['digest1','digest2'],'event_heads':['head1','head2']}
        self.assertEqual(R['append_only_journal'](before,after)['append_count'],1)
        for changed in ({'journal_id':'replacement'}, {'sequence':0,'head_sha256':'','event_digests':[],'event_heads':[]},
                        {'event_digests':['rewritten','digest2']},{'event_heads':['rewritten','head2']}):
            with self.subTest(changed=changed),self.assertRaises(RuntimeError):
                R['append_only_journal'](before,{**after,**changed})

    def test_real_body_scope_schema_join_uses_body_id_and_counts_cross_tenant(self):
        # This executes the actual predicate against the real scope shape:
        # scopes have body_id/tenant_id/KB, and deliberately no lease_id.
        db=sqlite3.connect(':memory:')
        db.executescript('CREATE TABLE original_body_payloads(id TEXT,tenant_id INTEGER);'
                         'CREATE TABLE original_body_leases(lease_id TEXT,body_id TEXT);'
                         'CREATE TABLE original_body_lease_scopes(body_id TEXT,tenant_id INTEGER,knowledge_base_id TEXT);'
                         'CREATE TABLE original_body_field_heads(body_id TEXT,tenant_id INTEGER);'
                         'CREATE TABLE original_body_parent_refs(body_id TEXT,tenant_id INTEGER);'
                         "INSERT INTO original_body_payloads VALUES('body',8);"
                         "INSERT INTO original_body_leases VALUES('lease','body');"
                         "INSERT INTO original_body_lease_scopes VALUES('body',7,'kb');")
        query=('SELECT count(*) FROM original_body_leases l JOIN original_body_payloads p ON p.id=l.body_id WHERE '+
               R['actual_body_lease_scope_predicate'](7))
        self.assertEqual(db.execute(query).fetchone()[0],1)
        db.execute('DELETE FROM original_body_lease_scopes')
        self.assertEqual(db.execute(query).fetchone()[0],0)
        db.execute("INSERT INTO original_body_field_heads VALUES('body',7)")
        self.assertEqual(db.execute(query).fetchone()[0],1)
        db.close()

    def test_positive_and_source_revoke_trials_cannot_mix_one_owner(self):
        with tempfile.TemporaryDirectory() as scratch:
            directory=Path(scratch).resolve();state={'project':'nc-synldap-abcdef12'}
            R['bind_fresh_trial_mode'](directory,state,False)
            before=(directory/'native-stream-trial-mode.json').read_bytes()
            with self.assertRaises(FileExistsError):R['bind_fresh_trial_mode'](directory,state,True)
            self.assertEqual((directory/'native-stream-trial-mode.json').read_bytes(),before)
            self.assertEqual((directory/'native-stream-trial-mode.json').stat().st_mode&0o777,0o600)

    def frozen_fixture(self,directory,maximum):
        state,compose=F.sample(directory,ui=False)
        code=directory/'mock_embedding.py';code.write_bytes(b'# exact frozen model\n');code.chmod(0o600)
        state['mock_model_code_sha256']=hashlib.sha256(code.read_bytes()).hexdigest()
        compose['services']['mock-embedding']['volumes']=[f'{code}:/srv/mock_embedding.py:ro']
        if maximum:
            state['mock_chat_stream_delay_max_seconds']=maximum
            compose['services']['mock-embedding']['environment']={'MOCK_CHAT_STREAM_DELAY_MAX_SECONDS':str(maximum)}
        state['compose_fingerprint']=F.fixture.compose_fingerprint(compose)
        F.write(directory,state,compose)
        return state,compose,code

    def test_fresh_model_bytes_and_delay_are_frozen_by_owner(self):
        with tempfile.TemporaryDirectory() as scratch:
            directory=Path(scratch).resolve();state,compose,code=self.frozen_fixture(directory,20)
            self.assertEqual(F.fixture.owned_state(directory)[1],state)
            compose['services']['mock-embedding']['environment']['MOCK_CHAT_STREAM_DELAY_MAX_SECONDS']='1'
            state['compose_fingerprint']=F.fixture.compose_fingerprint(compose);F.write(directory,state,compose)
            with self.assertRaisesRegex(RuntimeError,'delay differs'):F.fixture.owned_state(directory)

    def test_frozen_model_mutation_symlink_or_global_mount_is_rejected(self):
        for mode in ('changed','symlink','global-mount'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as scratch:
                directory=Path(scratch).resolve();state,compose,code=self.frozen_fixture(directory,20)
                if mode=='changed':code.write_bytes(b'# replaced model\n')
                elif mode=='symlink':
                    code.unlink();other=directory/'other';other.write_bytes(b'# exact frozen model\n');code.symlink_to(other)
                else:
                    compose['services']['mock-embedding']['volumes']=['/global/current/model.py:/srv/mock_embedding.py:ro']
                    state['compose_fingerprint']=F.fixture.compose_fingerprint(compose);F.write(directory,state,compose)
                with self.assertRaises(RuntimeError):F.fixture.owned_state(directory)

    def test_old_owner_cannot_be_used_for_opt_in_running_trial(self):
        with tempfile.TemporaryDirectory() as scratch:
            directory=Path(scratch).resolve();state,compose=F.sample(directory,ui=False)
            F.write(directory,state,compose)
            self.assertEqual(F.fixture.owned_state(directory)[1],state)
            with self.assertRaisesRegex(RuntimeError,'fresh_owned_stream_opt_in_required'):
                R['fresh_stream_configuration'](directory,state)
            self.assertNotIn('mock_model_code_sha256',state)
            self.assertNotIn('mock_chat_stream_delay_max_seconds',state)

    def test_restore_control_backup_carries_frozen_model_source(self):
        self.assertIn('mock_embedding.py',C['control_files_for_state']({'mock_model_code_sha256':'a'*64}))
        self.assertNotIn('mock_embedding.py',C['control_files_for_state']({}))
        for name in ('state.json','compose.yaml','passwords.json'):
            self.assertIn(name,C['control_files_for_state']({'mock_model_code_sha256':'a'*64}))


if __name__=='__main__':unittest.main()
