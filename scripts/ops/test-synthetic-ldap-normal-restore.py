#!/usr/bin/env python3
"""Offline guard tests; no backups, fixtures or restore claims are produced."""
import json
import os
from pathlib import Path
import runpy
import tarfile
import tempfile
import unittest
from unittest import mock
import uuid

P=runpy.run_path(str(Path(__file__).with_name('synthetic-ldap-normal-restore.py')))

class RestoreGuardTest(unittest.TestCase):
    def test_never_restore_current_anchor_volume(self):
        for role in P['DATA_VOLUMES']:P['volume_restore_guard'](role)
        for role in (*P['BODY_VOLUMES'],'wk-postgres','foreign'):
            with self.assertRaises(RuntimeError):P['volume_restore_guard'](role)

    def test_private_json_is_exclusive_and_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'control.json'
            P['write_json'](path,{'id':'owned'})
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(json.loads(path.read_text()),{'id':'owned'})
            with self.assertRaises(FileExistsError):P['write_json'](path,{})
            link=Path(directory)/'link';link.symlink_to(path)
            with self.assertRaises(RuntimeError):P['plain'](link)

    def test_archive_rejects_escape_and_absolute_links(self):
        with tempfile.TemporaryDirectory() as directory:
            for name,link in [('../escape',''),('safe','/etc/passwd'),('safe','../../escape')]:
                path=Path(directory)/'bad.tar'
                with tarfile.open(path,'w') as archive:
                    item=tarfile.TarInfo(name)
                    if link:item.type=tarfile.SYMTYPE;item.linkname=link
                    archive.addfile(item)
                path.chmod(0o600)
                with self.assertRaises(RuntimeError):P['inspect_tar'](path)

    def test_restore_never_initializes_empty_anchor(self):
        probe=object.__new__(P['Restore'])
        with self.assertRaises(RuntimeError):probe.body_cli('init')
        with self.assertRaises(RuntimeError):probe.body_cli('migrate')
        with self.assertRaises(RuntimeError):probe.body_cli('upgrade')

    def test_actual_stopped_state_is_required_for_cli(self):
        probe=object.__new__(P['Restore'])
        probe.actors=lambda:['wk-app','nextcloud']
        probe.stopped=lambda _:(_ for _ in ()).throw(RuntimeError('still_running'))
        with self.assertRaises(RuntimeError):probe.body_cli('verify')

    def test_readiness_not_replaced_by_prepared_or_receipt_boolean(self):
        probe=object.__new__(P['Restore'])
        probe.load_checkpoint=lambda:{}
        with tempfile.TemporaryDirectory() as directory:
            probe.evidence=Path(directory)
            probe.compose=mock.Mock()
            with self.assertRaises(RuntimeError):probe.reopen_and_accept()
            probe.compose.assert_not_called()

    def test_runtime_scope_rejects_unowned_binding(self):
        runtime={key:str(uuid.uuid4()) for key in ('source_id','knowledge_id','knowledge_base_id',
            'operation_id','model_id','chat_model_id')}
        runtime.update(binding_id='synthetic-published',tenant_id=7,file_id=8,root_file_id=9,share_id=10)
        P['runtime_scope'](runtime)
        runtime['binding_id']='foreign'
        with self.assertRaises(RuntimeError):P['runtime_scope'](runtime)

    def test_pending_auto_interface_is_explicitly_not_acceptance(self):
        probe=object.__new__(P['Restore'])
        with tempfile.TemporaryDirectory() as directory:
            probe.evidence=Path(directory)
            probe.pending_auto_plan()
            result=json.loads((probe.evidence/'pending-auto-interface.json').read_text())
            self.assertFalse(result['accepted'])
            self.assertTrue(result['fake_sql_seed_forbidden'])
            self.assertIn('actual signed ingest',result['required_real_boundary'])

    def test_existing_gate_boolean_still_runs_actual_closed_checks(self):
        probe=object.__new__(P['Restore'])
        with tempfile.TemporaryDirectory() as directory:
            probe.evidence=Path(directory).resolve();probe.provenance={'app':'pinned'}
            P['write_json'](probe.evidence/'checkpoint.json',{})
            P['write_json'](probe.evidence/'closed-clean-gate.json',{
                'candidate':probe.provenance,'checkpoint_sha256':P['sha'](probe.evidence/'checkpoint.json'),
                'actual_body_cli':True})
            probe.load_checkpoint=lambda:{}
            probe.actual_clean_gates=mock.Mock(side_effect=RuntimeError('actual_body_missing'))
            probe.compose=mock.Mock()
            with self.assertRaisesRegex(RuntimeError,'actual_body_missing'):probe.reopen_and_accept()
            probe.actual_clean_gates.assert_called_once();probe.compose.assert_not_called()

    def test_stopped_nc_does_not_try_docker_top(self):
        probe=object.__new__(P['Restore'])
        globals_=P['Restore'].no_external_workers.__globals__
        with tempfile.TemporaryDirectory() as directory:
            probe.directory=Path(directory);probe.nc='owned-nextcloud'
            fake_owner=dict(globals_['owner']);fake_owner['docker_inspect']=lambda *_:{'State':{'Running':False}}
            with mock.patch.dict(globals_,{'owner':fake_owner,'command':mock.Mock(side_effect=AssertionError('unexpected_top'))}):
                probe.no_external_workers()

    def test_current_publication_advance_cannot_use_clean_gate(self):
        globals_=P['pinned_publication'].__globals__
        checkpoint={'publication_anchor_current':{'ledger':'/private/current','key':'/private/key',
            'record_sha256':'a'*64,'metadata':{'sequence':'3'}}}
        fake_publication=dict(globals_['publication']);fake_publication['verify']=lambda *_:{
            'complete_to_head':True,'record_sha256':'b'*64,'sequence':'4'}
        with mock.patch.dict(globals_,{'publication':fake_publication}):
            with self.assertRaisesRegex(RuntimeError,'post_checkpoint_publication'):P['pinned_publication'](checkpoint)

    def test_stale_fault_cannot_pass_clean_body_gate(self):
        probe=object.__new__(P['Restore'])
        with tempfile.TemporaryDirectory() as directory:
            probe.evidence=Path(directory).resolve()
            P['write_json'](probe.evidence/'actual-fault.json',{'source_unchanged':False,'mode':'stale_publication'})
            probe.body_cli=mock.Mock()
            with self.assertRaisesRegex(RuntimeError,'stale_source_cannot'):probe.actual_clean_gates({})
            probe.body_cli.assert_not_called()

    def test_command_bytes_are_input_not_an_invalid_fileno_stream(self):
        globals_=P['command'].__globals__
        result=mock.Mock(returncode=0,stdout=b'owned')
        with mock.patch.dict(globals_,{'subprocess':mock.Mock(run=mock.Mock(return_value=result))}):
            self.assertEqual(P['command'](['actual-cli'],stdin=b'tar bytes'),b'owned')
            call=globals_['subprocess'].run.call_args.kwargs
            self.assertEqual(call['input'],b'tar bytes');self.assertNotIn('stdin',call)

if __name__=='__main__':unittest.main()
