import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC=importlib.util.spec_from_file_location('journal',Path(__file__).with_name('publication-recovery-journal.py'))
j=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(j)

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.root.chmod(0o700)
        self.key=self.root/'key';self.key.write_bytes(b'x'*32);self.key.chmod(0o600)
        self.log=self.root/'journal'
    def tearDown(self):self.tmp.cleanup()
    def page(self,after=0,kinds=('withdrawn',),binding='dept'):
        prefix=j.ZERO
        if after and self.log.exists():
            records=[json.loads(x) for x in self.log.read_bytes().splitlines()]
            prefix=records[-1]['body']['page']['next_chain_sha256']
        items=[{'sequence':str(after+i+1),'binding_id':binding,'file_id':'77',
                   'kind':kind,'source_revision':'1','created_at':1} for i,kind in enumerate(kinds)]
        chain=prefix
        for item in items:chain=j.event_hash(chain,item)
        return {'schema_version':1,'instance_id':'instance','stream_id':'00112233-4455-4677-8899-aabbccddeeff',
                'after_sequence':str(after),'next_sequence':str(after+len(kinds)),'head_sequence':str(after+len(kinds)),
                'after_chain_sha256':prefix,'next_chain_sha256':chain,'head_chain_sha256':chain,
                'has_more':False,'items':items}
    def test_complete_continuous_external_pages_and_replay(self):
        first=j.append(self.log,self.key,self.page(kinds=()))
        second=j.append(self.log,self.key,self.page(kinds=('withdrawn','eligible','binding_resume','delete')))
        verified=j.verify(self.log,self.key);self.assertEqual('4',verified['sequence']);self.assertTrue(verified['complete_to_head'])
        plan=j.replay_plan(self.log,self.key,first,second)
        self.assertFalse(plan['ingress_reopen_permitted']);self.assertTrue(plan['authoritative_reconciliation_required'])
        self.assertEqual([{'binding_id':'dept','file_id':'77','last_sequence':'4'}],plan['withdraw_files'])
        self.assertEqual('4',plan['close_bindings'][0]['last_sequence'])
        chain=plan['checkpoint_database_chain_sha256']
        self.assertEqual(['1','2','3','4'],[event['sequence'] for event in plan['journal_events']])
        for event in plan['journal_events']:
            chain=j.event_hash(chain,event)
            self.assertEqual(chain,plan['database_prefix_hashes'][event['sequence']])
        self.assertEqual(chain,plan['through_database_chain_sha256'])
    def test_typed_content_facts_ignore_metadata_and_keep_incomplete_write(self):
        first=j.append(self.log,self.key,self.page(kinds=()))
        second=j.append(self.log,self.key,self.page(kinds=('content_write_begin','content_write_complete','metadata','content_write_begin')))
        plan=j.replay_plan(self.log,self.key,first,second)
        self.assertEqual([{'binding_id':'dept','file_id':'77','revision':'1','writing':True}],plan['latest_content_revisions'])
        self.assertEqual([],plan['withdraw_files'])

    def target_fact(self,op='00112233-4455-4677-8899-aabbccddeeff',binding='dept',root='77',kb='kb.with-dot'):
        target={'schema':'weknora-nextcloud-target-v1','state':'target_deleted','reason':'knowledge_base_deleted',
                'operation_id':op,'instance_id':'instance','binding_id':binding,'tenant_id':'7','knowledge_base_id':kb,
                'operation_revision':'4','closure_id':'','deletion_time_ms':'1700000000000','closed_at':'1700000001'}
        target['closure_id']=j.hashlib.sha256('\n'.join(('weknora-target-deleted-v1',op,'7',kb,'instance',binding,'4','knowledge_base_deleted',target['deletion_time_ms'])).encode('ascii')).hexdigest()
        return {'target':target,'root_file_id':root,'root_key':j.hashlib.sha256(('nextcloud-filecache-root-v1\0'+root).encode('ascii')).hexdigest()}
    def target_page(self,after=0,fact=None):
        value=self.page(after=after,kinds=())
        fact=fact or self.target_fact()
        event={'sequence':str(after+1),'binding_id':fact['target']['binding_id'],'file_id':None,'kind':'target_deleted',
               'source_revision':fact['target']['operation_revision'],'created_at':1700000001,'target_closure':fact}
        value.update(next_sequence=str(after+1),head_sequence=str(after+1),items=[event])
        chain=j.event_hash(value['after_chain_sha256'],event)
        value.update(next_chain_sha256=chain,head_chain_sha256=chain)
        return value
    def test_target_closure_survives_checkpoint_and_later_present_like_events(self):
        j.append(self.log,self.key,self.page(kinds=()))
        anchor=j.append(self.log,self.key,self.target_page())
        tail=j.append(self.log,self.key,self.page(after=1,kinds=('binding_resume','eligible','metadata')))
        plan=j.replay_plan(self.log,self.key,anchor,tail)
        self.assertEqual([self.target_fact()],plan['target_closures'])
        self.assertEqual(['binding_resume','eligible','metadata'],[event['kind'] for event in plan['journal_events']])
        self.assertFalse(plan['ingress_reopen_permitted'])
    def test_target_closure_all_fields_are_chained_and_invalid_scope_refused(self):
        value=self.target_page()
        self.assertEqual(value,j.page(value))
        for key in ('closure_id','operation_revision','knowledge_base_id','closed_at'):
            mutated=json.loads(json.dumps(value));mutated['items'][0]['target_closure']['target'][key]='bad'
            with self.assertRaises(j.JournalError):j.page(mutated)
        for key in ('root_key','root_file_id'):
            mutated=json.loads(json.dumps(value));mutated['items'][0]['target_closure'][key]='bad'
            with self.assertRaises(j.JournalError):j.page(mutated)
        mutated=json.loads(json.dumps(value));mutated['items'][0]['target_closure']['body']='forbidden'
        with self.assertRaises(j.JournalError):j.page(mutated)
        mutated=json.loads(json.dumps(value));mutated['items'][0]['file_id']='77'
        with self.assertRaises(j.JournalError):j.page(mutated)
    def test_target_closure_identity_collision_is_not_a_new_source(self):
        first=j.append(self.log,self.key,self.page(kinds=()))
        j.append(self.log,self.key,self.target_page())
        conflict=self.target_fact(op='10112233-4455-4677-8899-aabbccddeeff',binding='another',root='78')
        tail=j.append(self.log,self.key,self.target_page(after=1,fact=conflict))
        with self.assertRaises(j.JournalError):j.replay_plan(self.log,self.key,first,tail)

    def test_target_closure_external_clock_and_journal_skew_bound(self):
        fact=self.target_fact()
        def rehash():
            target=fact['target']
            target['closure_id']=j.hashlib.sha256('\n'.join(('weknora-target-deleted-v1',target['operation_id'],target['tenant_id'],target['knowledge_base_id'],target['instance_id'],target['binding_id'],target['operation_revision'],target['reason'],target['deletion_time_ms'])).encode('ascii')).hexdigest()
        with mock.patch.object(j.time,'time',return_value=1700000000):
            fact['target']['deletion_time_ms']='1700000300000';fact['target']['closed_at']='1700000300';rehash()
            self.assertEqual(fact,j.target_closure(fact))
            value=self.target_page(fact=fact);value['items'][0]['created_at']=1700000000
            self.assertIsInstance(j.event_hash(j.ZERO,value['items'][0]),str)
            value['items'][0]['created_at']=1699999999
            with self.assertRaises(j.JournalError):j.event_hash(j.ZERO,value['items'][0])
            fact['target']['deletion_time_ms']='1700000301000';fact['target']['closed_at']='1700000301';rehash()
            with self.assertRaises(j.JournalError):j.target_closure(fact)
    def test_target_closure_full_history_limit_refuses_instead_of_truncating(self):
        # Lower only the test resource cap: the producer path stays the real
        # signed continuous collector, and production bound remains 10000.
        self.assertEqual(10000,j.MAX_TARGET_CLOSURES)
        first=j.append(self.log,self.key,self.page(kinds=()))
        j.append(self.log,self.key,self.target_page())
        distinct=self.target_fact(op='10112233-4455-4677-8899-aabbccddeeff',binding='dept2',root='78',kb='kb2')
        tail=j.append(self.log,self.key,self.target_page(after=1,fact=distinct))
        with mock.patch.object(j,'MAX_TARGET_CLOSURES',1):
            with self.assertRaises(j.JournalError):j.replay_plan(self.log,self.key,first,tail)
        self.assertEqual(2,len(j.replay_plan(self.log,self.key,first,tail)['target_closures']))

    def tenant_fact(self,tenant='7',origin='https://wk.example.test',op='00112233-4455-4677-8899-aabbccddeeff',binding='dept'):
        value={'schema':'weknora-nextcloud-tenant-deletion-v1','state':'tenant_deleted','operation_id':op,'instance_id':'instance','binding_id':binding,'tenant_id':tenant,'deletion_id':'','deletion_time_ms':'1700000000000','closed_at':'1700000001'}
        value['deletion_id']=j.hashlib.sha256('\n'.join(('weknora-tenant-deleted-v1',tenant,value['deletion_time_ms'])).encode('ascii')).hexdigest()
        return {'tenant':value,'origin_sha256':j.hashlib.sha256(origin.encode('ascii')).hexdigest()}
    def tenant_page(self,after=0,fact=None):
        value=self.page(after=after,kinds=());fact=fact or self.tenant_fact()
        event={'sequence':str(after+1),'binding_id':fact['tenant']['binding_id'],'file_id':None,'kind':'tenant_deleted','source_revision':'0','created_at':1700000001,'tenant_deletion':fact}
        value.update(next_sequence=str(after+1),head_sequence=str(after+1),items=[event]);chain=j.event_hash(value['after_chain_sha256'],event)
        value.update(next_chain_sha256=chain,head_chain_sha256=chain);return value
    def test_independent_tenant_history_precheckpoint_and_retired_binding_not_closed(self):
        anchor=j.append(self.log,self.key,self.page(kinds=()))
        tail=j.append(self.log,self.key,self.tenant_page())
        plan=j.replay_plan(self.log,self.key,anchor,tail)
        self.assertEqual([self.tenant_fact()],plan['tenant_deletions']);self.assertEqual([],plan['close_bindings']);self.assertEqual([],plan['target_closures'])
        later=j.append(self.log,self.key,self.page(after=1,kinds=('metadata',),binding='migrated-live'))
        recovered=j.replay_plan(self.log,self.key,tail,later)
        self.assertEqual([self.tenant_fact()],recovered['tenant_deletions']);self.assertEqual(['migrated-live'],[x['binding_id'] for x in recovered['close_bindings']])
    def test_independent_tenant_namespace_numeric_order_and_first_anchor_immutable(self):
        first=j.append(self.log,self.key,self.page(kinds=()))
        j.append(self.log,self.key,self.tenant_page(fact=self.tenant_fact(tenant='10')))
        tail=j.append(self.log,self.key,self.tenant_page(after=1,fact=self.tenant_fact(tenant='2',binding='second')))
        plan=j.replay_plan(self.log,self.key,first,tail)
        self.assertEqual(['2','10'],[x['tenant']['tenant_id'] for x in plan['tenant_deletions']])
        conflict=self.tenant_fact(tenant='2',op='10112233-4455-4677-8899-aabbccddeeff',binding='new-anchor')
        tail=j.append(self.log,self.key,self.tenant_page(after=2,fact=conflict))
        with self.assertRaises(j.JournalError):j.replay_plan(self.log,self.key,first,tail)
    def test_tenant_derived_root_fact_requires_independent_owner_history(self):
        first=j.append(self.log,self.key,self.page(kinds=()));fact=self.target_fact();target=fact['target'];target['reason']='tenant_deleted'
        target['closure_id']=j.hashlib.sha256('\n'.join(('weknora-target-deleted-v1',target['operation_id'],target['tenant_id'],target['knowledge_base_id'],target['instance_id'],target['binding_id'],target['operation_revision'],target['reason'],target['deletion_time_ms'])).encode('ascii')).hexdigest()
        tail=j.append(self.log,self.key,self.target_page(fact=fact))
        with self.assertRaises(j.JournalError):j.replay_plan(self.log,self.key,first,tail)
        tail=j.append(self.log,self.key,self.tenant_page(after=1));plan=j.replay_plan(self.log,self.key,first,tail)
        self.assertEqual([fact],plan['target_closures']);self.assertEqual([self.tenant_fact()],plan['tenant_deletions'])
    def test_tenant_all_fields_envelope_and_event_scope_are_chained(self):
        value=self.tenant_page();self.assertEqual(value,j.page(value))
        for field in j.TENANT_FIELDS:
            bad=json.loads(json.dumps(value));bad['items'][0]['tenant_deletion']['tenant'][field]='invalid'
            with self.assertRaises(j.JournalError):j.page(bad)
        for field,val in [('file_id','77'),('source_revision','1'),('binding_id','foreign')]:
            bad=json.loads(json.dumps(value));bad['items'][0][field]=val
            with self.assertRaises(j.JournalError):j.page(bad)
        bad=json.loads(json.dumps(value));bad['items'][0]['tenant_deletion']['origin_sha256']='bad'
        with self.assertRaises(j.JournalError):j.page(bad)
        bad=json.loads(json.dumps(value));bad['items'][0]['target_closure']=self.target_fact()
        with self.assertRaises(j.JournalError):j.page(bad)
    def test_tenant_future_and_event_clock_skew(self):
        fact=self.tenant_fact();tenant=fact['tenant']
        with mock.patch.object(j.time,'time',return_value=1700000000):
            tenant['deletion_time_ms']='1700000300000';tenant['closed_at']='1700000300'
            tenant['deletion_id']=j.hashlib.sha256('\n'.join(('weknora-tenant-deleted-v1',tenant['tenant_id'],tenant['deletion_time_ms'])).encode('ascii')).hexdigest()
            self.assertEqual(fact,j.tenant_deletion(fact))
            value=self.tenant_page(fact=fact);value['items'][0]['created_at']=1700000000
            self.assertIsInstance(j.event_hash(j.ZERO,value['items'][0]),str)
            value['items'][0]['created_at']=1699999999
            with self.assertRaises(j.JournalError):j.event_hash(j.ZERO,value['items'][0])
            tenant['closed_at']='1700000301'
            with self.assertRaises(j.JournalError):j.tenant_deletion(fact)

    def test_tenant_full_history_cap_refuses_and_never_truncates(self):
        self.assertEqual(10000,j.MAX_TENANT_DELETIONS);first=j.append(self.log,self.key,self.page(kinds=()))
        j.append(self.log,self.key,self.tenant_page());tail=j.append(self.log,self.key,self.tenant_page(after=1,fact=self.tenant_fact(origin='https://other.example.test')))
        with mock.patch.object(j,'MAX_TENANT_DELETIONS',1):
            with self.assertRaises(j.JournalError):j.replay_plan(self.log,self.key,first,tail)
        self.assertEqual(2,len(j.replay_plan(self.log,self.key,first,tail)['tenant_deletions']))

    def test_exact_retry_does_not_duplicate(self):
        value=self.page();first=j.append(self.log,self.key,value);size=self.log.stat().st_size
        self.assertEqual(first,j.append(self.log,self.key,value));self.assertEqual(size,self.log.stat().st_size)
    def test_idle_collector_does_not_grow_journal(self):
        first=j.append(self.log,self.key,self.page());size=self.log.stat().st_size
        self.assertEqual(first,j.append(self.log,self.key,self.page(after=1,kinds=())))
        self.assertEqual(size,self.log.stat().st_size)
    def test_gap_overlap_wrong_stream_and_rollback(self):
        j.append(self.log,self.key,self.page())
        for value in [self.page(after=2),self.page(after=0,kinds=('delete',)),{**self.page(after=1),'stream_id':'10112233-4455-4677-8899-aabbccddeeff'}]:
            with self.assertRaises(j.JournalError):j.append(self.log,self.key,value)
    def test_missing_items_and_duplicate_json_refused(self):
        value=self.page();value['items']=[]
        with self.assertRaises(j.JournalError):j.page(value)
        with self.assertRaises(j.JournalError):j.parse(b'{"a":1,"a":2}')
    def test_tampered_body_key_and_chain_refused(self):
        j.append(self.log,self.key,self.page())
        saved=self.log.read_bytes();value=json.loads(saved);value['body']['page']['items'][0]['kind']='eligible'
        self.log.write_bytes(j.canonical(value)+b'\n')
        with self.assertRaises(j.JournalError):j.verify(self.log,self.key)
        self.log.write_bytes(saved);self.key.write_bytes(b'y'*32)
        with self.assertRaises(j.JournalError):j.verify(self.log,self.key)
    def test_crash_tail_and_tail_truncation_require_external_pin(self):
        first=j.append(self.log,self.key,self.page());saved=self.log.read_bytes()
        second=j.append(self.log,self.key,self.page(after=1,kinds=('delete',)))
        self.log.write_bytes(self.log.read_bytes()[:-2])
        with self.assertRaises(j.JournalError):j.verify(self.log,self.key)
        self.log.write_bytes(saved)
        with self.assertRaises(j.JournalError):j.replay_plan(self.log,self.key,first,second)
    def test_empty_bootstrap_required(self):
        with self.assertRaises(j.JournalError):j.append(self.log,self.key,self.page(after=10))
    def test_private_modes_links_and_symlink_parent(self):
        self.key.chmod(0o644)
        with self.assertRaises(j.JournalError):j.append(self.log,self.key,self.page())
        self.key.chmod(0o600);alias=self.root/'alias';alias.symlink_to(self.key)
        with self.assertRaises(j.JournalError):j.append(self.log,alias,self.page())
        target=self.root/'target';target.mkdir(mode=0o700);symlink=self.root/'link';symlink.symlink_to(target)
        with self.assertRaises(j.JournalError):j.append(symlink/'journal',self.key,self.page())
    def test_unknown_kind_decimal_overflow_and_boolean_times(self):
        for field,value in [('kind','reopen'),('sequence',str(2**63)),('created_at',True)]:
            p=self.page();p['items'][0][field]=value
            with self.assertRaises(j.JournalError):j.page(p)
    def test_key_replaced_after_validation_is_refused(self):
        original=j.plain
        def replace(path,directory=False):
            checked=original(path,directory)
            if path==self.key:
                replacement=self.root/'replacement';replacement.write_bytes(b'z'*32);replacement.chmod(0o644)
                os.replace(replacement,path)
            return checked
        with mock.patch.object(j,'plain',side_effect=replace):
            with self.assertRaises(j.JournalError):j.key_bytes(self.key)
    def test_checkpoint_pin_outside_capture_and_continuity(self):
        pin=j.append(self.log,self.key,self.page())
        value=j.checkpoint_anchor(self.log,self.key,pin,[self.root/'backup'])
        self.assertEqual('1',value['sequence'])
        with self.assertRaises(j.JournalError):j.checkpoint_anchor(self.log,self.key,pin,[self.root])
        with self.assertRaises(j.JournalError):j.checkpoint_anchor(self.log,self.key,'f'*64,[])
    def test_restored_database_branch_that_catches_up_is_refused(self):
        j.append(self.log,self.key,self.page())
        value=self.page(after=1,kinds=('delete',))
        changed=self.page(kinds=('eligible',))['next_chain_sha256']
        value['after_chain_sha256']=changed
        value['next_chain_sha256']=j.event_hash(changed,value['items'][0]);value['head_chain_sha256']=value['next_chain_sha256']
        with self.assertRaises(j.JournalError):j.append(self.log,self.key,value)
    def test_pending_witnessed_head_cannot_be_replaced(self):
        first=self.page(kinds=('upsert',)*200)
        next_event={'sequence':'201','binding_id':'dept','file_id':'77','kind':'withdrawn','source_revision':'1','created_at':1}
        first['head_sequence']='201';first['has_more']=True
        first['head_chain_sha256']=j.event_hash(first['next_chain_sha256'],next_event)
        j.append(self.log,self.key,first)
        fork=self.page(after=200,kinds=('eligible',))
        with self.assertRaises(j.JournalError):j.append(self.log,self.key,fork)
        growth=self.page(after=200,kinds=('eligible','upsert'))
        with self.assertRaises(j.JournalError):j.append(self.log,self.key,growth)
        original=self.page(after=200,kinds=('withdrawn',))
        j.append(self.log,self.key,original)
        self.assertTrue(j.verify(self.log,self.key)['complete_to_head'])
    def test_collector_checks_exact_container_owner_and_retains_page(self):
        cid='a'*64;project='isolated-project'
        item={'Id':cid,'State':{'Running':True},'Config':{'Labels':{'com.docker.compose.project':project,'com.docker.compose.service':'nextcloud'}}}
        def command(args,**kwargs):
            data=json.dumps([item]).encode() if args[1]=='inspect' else json.dumps(self.page()).encode()
            return type('Result',(),{'returncode':0,'stdout':data})()
        with mock.patch.object(j.subprocess,'run',side_effect=command):
            result=j.collect(self.log,self.key,cid,project)
        self.assertEqual('1',result['sequence'])
        item['Config']['Labels']['com.docker.compose.project']='foreign'
        with mock.patch.object(j.subprocess,'run',side_effect=command):
            with self.assertRaises(j.JournalError):j.collect(self.log,self.key,cid,project)
    def test_unfinished_page_cannot_anchor_recovery(self):
        value=self.page(kinds=('upsert',)*200);value['head_sequence']='201';value['has_more']=True
        pin=j.append(self.log,self.key,value)
        with self.assertRaises(j.JournalError):j.replay_plan(self.log,self.key,pin,pin)

if __name__=='__main__':unittest.main()
