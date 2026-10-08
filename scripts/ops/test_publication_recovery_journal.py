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
