#!/usr/bin/env python3
"""Offline guards and temporary private-file restoration; no app acceptance."""
import json
import hashlib
import io
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
    def controls(self,directory,evidence):
        fixture=P['owner'];project='nc-synldap-a1b2c3d4'
        ports={'nextcloud':50001,'weknora':50002,'ldap':50003,'weknora_ui':50004}
        passwords={key:'temporary-file-test' for key in ('nc_db','wk_db','nc_admin','ldap_admin',
            'ldap_bind','alice','bob','charlie','wk_admin','jwt','aes')}
        compose=fixture['compose_data'](directory,project,ports,passwords,'app:test','ui:test',
            owner_token='1'*32,body_journal=True)
        code=b'# private temporary model control\n'
        compose['services']['mock-embedding']['volumes']=[f'{directory}/mock_embedding.py:/srv/mock_embedding.py:ro']
        compose['services']['mock-embedding']['environment']={'MOCK_CHAT_STREAM_DELAY_MAX_SECONDS':'20'}
        fixture['apply_resource_profile'](compose,'normal-trial')
        state={'marker':fixture['MARKER'],'project':project,'scratch_dir':str(directory),'owner_token':'1'*32,
            'mode':'nested','ports':ports,'weknora_image':'app:test','weknora_image_id':'sha256:'+'a'*64,
            'weknora_ui_image':'ui:test','weknora_ui_image_id':'sha256:'+'b'*64,'body_journal':True,
            'body_journal_initialized':True,'mock_model_code_sha256':hashlib.sha256(code).hexdigest(),
            'mock_chat_stream_delay_max_seconds':20,'resource_profile':'normal-trial',
            'compose_fingerprint':fixture['compose_fingerprint'](compose)}
        runtime={key:str(uuid.uuid4()) for key in ('source_id','knowledge_id','knowledge_base_id',
            'operation_id','model_id','chat_model_id')}
        runtime.update(binding_id='synthetic-published',tenant_id=7,file_id=8,root_file_id=9,share_id=10)
        controls={'state.json':state,'compose.yaml':compose,'passwords.json':passwords,'runtime.json':runtime,
            'fixture.json':{'synthetic_fixture':True,'knowledge_id':runtime['knowledge_id'],
                'synthetic_query':'synthetic approval code'},'mock_embedding.py':code}
        artifacts={}
        for name,value in controls.items():
            raw=value if isinstance(value,bytes) else json.dumps(value,sort_keys=True,indent=2).encode()+b'\n'
            for path in (directory/name,evidence/('control-'+name)):
                path.write_bytes(raw);path.chmod(0o600)
            artifacts['control-'+name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
        provenance={'app':{'image_id':state['weknora_image_id']},'ui':{'image_id':state['weknora_ui_image_id']},
                    'manifest_sha256':'c'*64}
        checkpoint={'project':project,'owner_token_sha256':hashlib.sha256(state['owner_token'].encode()).hexdigest(),
            'candidate':provenance,'configuration_control_identity':P['control_identity'](state),'artifacts':artifacts}
        return state,checkpoint,provenance

    def private_directories(self,root):
        directory=root/'controls';evidence=root/'checkpoint';anchors=root/'current-anchors'
        for path in (directory,evidence,anchors):path.mkdir(mode=0o700)
        return directory,evidence,anchors

    def test_actual_temporary_control_fault_and_complete_restore_preserve_current_anchors(self):
        with tempfile.TemporaryDirectory() as root:
            directory,evidence,anchors=self.private_directories(Path(root).resolve())
            state,checkpoint,provenance=self.controls(directory,evidence)
            anchor=anchors/'CURRENT-journal-key-pin';anchor.write_bytes(b'current independent authority');anchor.chmod(0o600)
            anchor_facts=(anchor.read_bytes(),anchor.stat().st_ino)
            original={name:(directory/name).read_bytes() for name in P['control_files_for_state'](state)}
            fault=P['fault_checkpoint_control'](directory,evidence,state,checkpoint,provenance)
            self.assertNotEqual((directory/'fixture.json').read_bytes(),original['fixture.json'])
            self.assertEqual(P['owner']['owned_state'](directory)[1],state)
            inodes={name:(directory/name).stat().st_ino for name in original}
            restored=P['restore_checkpoint_controls'](directory,evidence,state,checkpoint,provenance,fault)
            self.assertTrue(restored['actual_checkpoint_controls_restored']);self.assertTrue(restored['fault_absent_after_restore'])
            self.assertEqual(set(restored['files']),set(original))
            for name,raw in original.items():
                self.assertEqual((directory/name).read_bytes(),raw)
                self.assertNotEqual((directory/name).stat().st_ino,inodes[name])
                self.assertEqual((directory/name).stat().st_mode&0o777,0o600)
            self.assertEqual((anchor.read_bytes(),anchor.stat().st_ino),anchor_facts)

    def test_control_restore_refuses_current_owner_pins_model_budget_or_source_drift(self):
        for change in ('owner','image','model','budget','runtime','password'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as root:
                directory,evidence,_=self.private_directories(Path(root).resolve())
                state,checkpoint,provenance=self.controls(directory,evidence)
                fault=P['fault_checkpoint_control'](directory,evidence,state,checkpoint,provenance)
                live=json.loads((directory/'state.json').read_text())
                compose=json.loads((directory/'compose.yaml').read_text())
                if change=='owner':live['owner_token']='2'*32
                elif change=='image':live['weknora_image_id']='sha256:'+'d'*64
                elif change=='model':
                    (directory/'mock_embedding.py').write_bytes(b'# different model\n')
                    live['mock_model_code_sha256']=P['sha'](directory/'mock_embedding.py')
                elif change=='budget':
                    compose['services']['wk-app']['mem_limit']+=1
                    live['compose_fingerprint']=P['owner']['compose_fingerprint'](compose)
                elif change=='runtime':
                    runtime=json.loads((directory/'runtime.json').read_text());runtime['source_id']=str(uuid.uuid4())
                    (directory/'runtime.json').write_text(json.dumps(runtime))
                else:(directory/'passwords.json').write_text('{"jwt":"changed"}')
                (directory/'state.json').write_text(json.dumps(live));(directory/'compose.yaml').write_text(json.dumps(compose))
                before={p.name:p.read_bytes() for p in directory.iterdir()}
                with self.assertRaises(RuntimeError):
                    P['restore_checkpoint_controls'](directory,evidence,live,checkpoint,provenance,fault)
                self.assertEqual({p.name:p.read_bytes() for p in directory.iterdir()},before)

    def test_control_restore_refuses_backup_adoption_and_unknown_anchor_controls(self):
        for change in ('saved-owner','saved-model','extra-anchor','candidate','size'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as root:
                directory,evidence,_=self.private_directories(Path(root).resolve())
                state,checkpoint,provenance=self.controls(directory,evidence)
                fault=P['fault_checkpoint_control'](directory,evidence,state,checkpoint,provenance)
                if change=='candidate':
                    provenance=json.loads(json.dumps(provenance));provenance['app']['image_id']='sha256:'+'d'*64
                    checkpoint['candidate']=provenance
                elif change=='extra-anchor':checkpoint['artifacts']['control-CURRENT-pin.json']={'sha256':'a'*64,'bytes':1}
                elif change=='size':checkpoint['artifacts']['control-runtime.json']['bytes']+=1
                else:
                    name='state.json' if change=='saved-owner' else 'mock_embedding.py'
                    backup=evidence/('control-'+name)
                    if change=='saved-owner':
                        saved=json.loads(backup.read_text());saved['owner_token']='2'*32;backup.write_text(json.dumps(saved))
                    else:backup.write_bytes(b'# altered checkpoint model\n')
                    checkpoint['artifacts']['control-'+name]={'sha256':P['sha'](backup),'bytes':backup.stat().st_size}
                before={p.name:p.read_bytes() for p in directory.iterdir()}
                with self.assertRaises(RuntimeError):
                    P['restore_checkpoint_controls'](directory,evidence,state,checkpoint,provenance,fault)
                self.assertEqual({p.name:p.read_bytes() for p in directory.iterdir()},before)

    def test_control_restore_requires_exact_recorded_query_only_fault(self):
        for change in ('no-fault','unrecorded-digest','knowledge-id','wrong-field'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as root:
                directory,evidence,_=self.private_directories(Path(root).resolve())
                state,checkpoint,provenance=self.controls(directory,evidence)
                fault=P['fault_checkpoint_control'](directory,evidence,state,checkpoint,provenance)
                if change=='no-fault':fault=None
                elif change=='unrecorded-digest':fault['fault_sha256']='d'*64
                elif change=='wrong-field':fault['field']='knowledge_id'
                else:
                    path=directory/'fixture.json';value=json.loads(path.read_text());value['knowledge_id']=str(uuid.uuid4())
                    path.write_text(json.dumps(value));fault['fault_sha256']=P['sha'](path)
                before=(directory/'fixture.json').read_bytes()
                with self.assertRaises(RuntimeError):
                    P['restore_checkpoint_controls'](directory,evidence,state,checkpoint,provenance,fault)
                self.assertEqual((directory/'fixture.json').read_bytes(),before)

    def test_control_restore_rejects_symlink_hardlink_and_changed_destination(self):
        for change in ('source-symlink','destination-hardlink','late-destination'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as root:
                directory,evidence,_=self.private_directories(Path(root).resolve())
                state,checkpoint,provenance=self.controls(directory,evidence)
                fault=P['fault_checkpoint_control'](directory,evidence,state,checkpoint,provenance)
                target=directory/'fixture.json'
                if change=='source-symlink':
                    source=evidence/'control-fixture.json';source.unlink();source.symlink_to(target)
                elif change=='destination-hardlink':os.link(target,directory/'extra-link')
                else:
                    before=target.read_bytes()
                    with self.assertRaisesRegex(RuntimeError,'current_control_changed_before_restore'):
                        P['replace_private_control'](target,b'{}', 'd'*64)
                    self.assertEqual(target.read_bytes(),before)
                    continue
                with self.assertRaises(RuntimeError):
                    P['restore_checkpoint_controls'](directory,evidence,state,checkpoint,provenance,fault)

    def test_owned_rechecks_current_marker_before_any_resource_operation(self):
        with tempfile.TemporaryDirectory() as root:
            directory,evidence,_=self.private_directories(Path(root).resolve())
            state,_,_=self.controls(directory,evidence)
            probe=object.__new__(P['Restore']);probe.directory=directory;probe.state=state
            changed=dict(state);changed['owner_token']='2'*32
            (directory/'state.json').write_text(json.dumps(changed))
            globals_=P['Restore'].owned.__globals__;fake_owner=dict(globals_['owner'])
            fake_owner['assert_owned_resources']=mock.Mock(side_effect=AssertionError('resource_operation_before_marker_guard'))
            with mock.patch.dict(globals_,{'owner':fake_owner}):
                with self.assertRaisesRegex(RuntimeError,'current_fixture_control_identity_changed'):probe.owned()
                fake_owner['assert_owned_resources'].assert_not_called()

    def nc_facts(self,present=False,value=None,digest='a'):
        return {'config_php_sha256':digest*64,'configuration_except_language_sha256':'c'*64,
            'default_language_present':present,'config_php_default_language':value,'occ_default_language':value}

    def test_nc_configuration_fault_requires_real_hash_value_and_other_config_unchanged(self):
        for original in (None,'en','fr'):
            before=self.nc_facts(original is not None,original)
            value='de' if original=='fr' else 'fr';after=self.nc_facts(True,value,'b')
            fault={'setting':'default_language','value':value,'checkpoint_config_php_sha256':before['config_php_sha256'],
                   'fault_config_php_sha256':after['config_php_sha256']}
            P['nc_configuration_fault_guard'](before,after,fault)
            for key,bad in (('config_php_sha256',before['config_php_sha256']),
                            ('occ_default_language',original),('configuration_except_language_sha256','d'*64)):
                with self.subTest(original=original,key=key):
                    changed=dict(after);changed[key]=bad
                    with self.assertRaises(RuntimeError):P['nc_configuration_fault_guard'](before,changed,fault)
        bad=self.nc_facts();bad['password']='must never cross config boundary'
        with self.assertRaises(RuntimeError):P['checked_nc_configuration'](bad)

    def test_nc_config_restore_input_contains_exact_single_regular_config_php(self):
        original=b'<?php $CONFIG=["default_language"=>"en","dbpassword"=>"private-test-value"];\n'
        with tempfile.TemporaryDirectory() as root:
            path=Path(root).resolve()/'nc-html.tar'
            for entries in ([('./config/config.php',original,None)],[],
                            [('config/config.php',original,None),('./config/config.php',original,None)],
                            [('config/config.php',b'', 'other-config.php')]):
                with self.subTest(count=len(entries)):
                    with tarfile.open(path,'w') as archive:
                        for name,raw,link in entries:
                            item=tarfile.TarInfo(name);item.size=len(raw)
                            if link:item.type=tarfile.SYMTYPE;item.linkname=link
                            archive.addfile(item,io.BytesIO(raw))
                    path.chmod(0o600)
                    if len(entries)==1 and entries[0][2] is None:
                        self.assertEqual(P['nc_config_archive_sha256'](path),hashlib.sha256(original).hexdigest())
                    else:
                        with self.assertRaises(RuntimeError):P['nc_config_archive_sha256'](path)

    def test_restored_receipt_does_not_replace_live_config_or_control_read(self):
        probe=object.__new__(P['Restore']);probe.state={}
        checkpoint={'nextcloud_configuration':self.nc_facts()}
        probe.nc_configuration=lambda **_:self.nc_facts(True,'fr','b')
        with self.assertRaisesRegex(RuntimeError,'actual_nc_configuration_restore_missing_or_changed'):
            probe.restored_configuration(checkpoint,{'actual_nc_configuration_restored':True,
                'nextcloud_configuration':checkpoint['nextcloud_configuration']})

    def test_roles_compare_normalizes_only_the_matched_restriction_token(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory).resolve()
            before=directory/'before.sql';after=directory/'after.sql'
            roles=(b'CREATE ROLE employee;\n'
                   b'ALTER ROLE employee WITH NOSUPERUSER LOGIN PASSWORD \'original\';\n'
                   b'GRANT reader TO employee;\n')
            original=b'\\restrict RandomOriginalToken\n'+roles+b'\\unrestrict RandomOriginalToken\n'
            current=b'\\restrict RandomCurrentToken\n'+roles+b'\\unrestrict RandomCurrentToken\n'
            for path,raw in ((before,original),(after,current)):
                path.write_bytes(raw);path.chmod(0o600)
            self.assertNotEqual(P['sha'](before),P['sha'](after))
            self.assertEqual(P['role_dump_comparison_sha256'](before),P['role_dump_comparison_sha256'](after))
            for old,new in ((b'NOSUPERUSER',b'SUPERUSER'),(b'original',b'changed'),
                            (b'GRANT reader',b'GRANT administrator'),(b'employee',b'other')):
                with self.subTest(change=new):
                    after.write_bytes(current.replace(old,new))
                    self.assertNotEqual(P['role_dump_comparison_sha256'](before),P['role_dump_comparison_sha256'](after))
            self.assertEqual(before.read_bytes(),original)

    def test_roles_compare_rejects_invalid_restriction_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'roles.sql'
            for raw in (b'\\restrict OnlyOne\n',b'\\unrestrict Reverse\n\\restrict Reverse\n',
                        b'\\restrict First\n\\unrestrict Second\n',
                        b'\\restrict Duplicate\n\\restrict Duplicate\n\\unrestrict Duplicate\n',
                        b'\\restrict Token --unexpected\n\\unrestrict Token\n'):
                with self.subTest(raw=raw):
                    path.write_bytes(raw);path.chmod(0o600)
                    with self.assertRaisesRegex(RuntimeError,'invalid_role_dump_restrict_pair'):
                        P['role_dump_comparison_sha256'](path)

    def test_roles_dump_retains_raw_backup_and_its_integrity_hash(self):
        probe=object.__new__(P['Restore'])
        globals_=P['Restore'].dump.__globals__
        raw=b'\\restrict ActualToken\nCREATE ROLE employee;\n\\unrestrict ActualToken\n'
        def dump_command(arguments,stdout,timeout):
            self.assertEqual(arguments,['docker','exec','owned-db','pg_dumpall','-U','owned-user','--roles-only'])
            stdout.write(raw)
            return b''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'roles.sql'
            with mock.patch.dict(globals_,{'command':dump_command}):
                facts=probe.dump('owned-db','owned-user','owned-database',path,True)
            self.assertEqual(path.read_bytes(),raw)
            self.assertEqual(facts['sha256'],hashlib.sha256(raw).hexdigest())
            self.assertEqual(facts['role_comparison_sha256'],P['role_dump_comparison_sha256'](path))
            self.assertEqual(facts['bytes'],len(raw))
            self.assertEqual(path.stat().st_mode&0o777,0o600)

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
        with self.assertRaisesRegex(RuntimeError,'body_upgrade_not_authorized'):probe.body_cli('upgrade-scopes')

    def test_authorized_scope_upgrade_uses_the_actual_packaged_cli_mode(self):
        probe=object.__new__(P['Restore'])
        probe.directory=Path('/private/owned');probe.state={'owner':'pinned'};probe.project='nc-synldap-01234567'
        probe.actors=lambda:['wk-app','nextcloud'];probe.stopped=mock.Mock()
        probe.owned=mock.Mock();probe.record=mock.Mock()
        command=mock.Mock(return_value=b'')
        globals_=P['Restore'].body_cli.__globals__
        fake_owner=dict(globals_['owner'])
        fake_owner['compose_command']=lambda directory,state,*args:['owned-compose',*args]
        with mock.patch.dict(globals_,{'owner':fake_owner,'command':command}):
            probe.body_cli('upgrade-scopes','body_owner_explicit_upgrade')
        arguments=command.call_args.args[0]
        self.assertEqual(arguments[-1],'upgrade-scopes')
        self.assertIn('original-body-retention -driver postgres -mode "$1"',arguments[-3])
        self.assertEqual(probe.stopped.call_args_list,[mock.call('wk-app'),mock.call('nextcloud')])
        probe.record.assert_called_once_with('packaged_body_upgrade-scopes',terminal=0)

    def test_actual_stopped_state_is_required_for_cli(self):
        probe=object.__new__(P['Restore'])
        probe.actors=lambda:['wk-app','nextcloud']
        probe.stopped=lambda _:(_ for _ in ()).throw(RuntimeError('still_running'))
        with self.assertRaises(RuntimeError):probe.body_cli('verify')

    def test_readiness_not_replaced_by_prepared_or_receipt_boolean(self):
        probe=object.__new__(P['Restore'])
        probe.load_checkpoint=lambda:{}
        with tempfile.TemporaryDirectory() as directory:
            probe.evidence=Path(directory).resolve()
            probe.compose=mock.Mock()
            with self.assertRaises(FileNotFoundError):probe.reopen_and_accept()
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
