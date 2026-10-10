#!/usr/bin/env python3
"""Opt-in full application restore of a genuinely undelivered Auto153 intent.

Each variant requires its own fresh owned fixture/checkpoint. No SQL seeding,
body signing, direct worker invocation, or broker-only restore is supported.
Run only in the explicitly assigned serial runtime window. Method tests and
source preparation cannot attest full application acceptance.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import runpy
import sys
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
N=runpy.run_path(str(HERE/'synthetic-ldap-normal-restore.py'))
C=runpy.run_path(str(HERE/'synthetic-ldap-pending-auto-contract.py'))
require=N['require'];plain=N['plain'];sha=N['sha'];write_json=N['write_json']
request=N['request'];owner=N['owner'];e2e=N['e2e'];login=N['login']


def last_real_enrichment(snapshot,status,operation):
    item=status.get('operation') or {};k=snapshot.get('knowledge') or {}
    require(item.get('operation_id')==operation and item.get('kind')=='question' and
            item.get('state')=='waiting_before_response' and k.get('parse_status')=='finalizing' and
            k.get('pending_subtasks_count')==1 and snapshot.get('intents')==[],
            'actual_last_enrichment_not_waiting')
    require(snapshot.get('stats',{}).get('other_processing')==0,'other_actual_enrichment_not_drained')
    base=item.get('baseline_calls') or {};calls=status.get('calls') or {}
    require(base.get('auto_tag')==calls.get('auto_tag') and
            calls.get('question',{}).get('started',0)-base.get('question',{}).get('started',0)==1,
            'actual_target_model_window_not_unique')


def immutable_pending_restored(checkpoint,restored,snapshot):
    original=checkpoint['pending_auto']['receipt']
    require(restored.get('checkpoint_sha256')==checkpoint['_actual_checkpoint_sha256'] and
            restored.get('actual_dual_full_pg_restore') is True and
            restored.get('original_pending_intent_restored') is True and
            restored.get('actual_nc_configuration_restored') is True and
            restored.get('configuration_controls',{}).get('actual_checkpoint_controls_restored') is True and
            restored.get('configuration_controls',{}).get('fault_absent_after_restore') is True and
            restored.get('current_body_or_independent_publication_anchors_restored') is False and
            restored.get('actual_original_data_volumes_restored')==sorted(N['DATA_VOLUMES']),
            'pending_auto_actual_full_restore_missing')
    require(C['pending_receipt'](snapshot,original['expected_tag_id'])==original,
            'pending_auto_restored_original_changed')
    C['pending_quiescence'](snapshot)


def keep_exact_closed_receipt(path,receipt):
    """Reuse exact private bytes only after the caller repeats every live gate."""
    require(path.name=='closed-pending-gate.json','pending_closed_receipt_path_invalid')
    plain(path.parent,True)
    expected=json.dumps(receipt,sort_keys=True,indent=2).encode()+b'\n'
    try:
        write_json(path,receipt)
        reused=False
    except FileExistsError:
        reused=True
    # Read with private path/inode/nlink/mode/race guards. Existing receipts
    # are never rewritten, normalized or deleted, including on disagreement.
    require(N['private_control_bytes'](path)==expected,'pending_closed_receipt_changed')
    return reused


class PendingRestore(N['Restore']):
    def __init__(self,scratch,manifest,profile,evidence,variant):
        super().__init__(scratch,manifest,profile,evidence)
        require(variant in ('positive','source','actor','model'),'pending_auto_unknown_variant')
        require(type(self.state.get('mock_postprocess_control_max_seconds')) is int and
                0 < self.state['mock_postprocess_control_max_seconds'] <= 60,
                'fresh_pending_auto_model_opt_in_required')
        self.variant=variant
        self.tag_id=C['canonical_uuid'](self.runtime.get('pending_auto_tag_id'))
        self.tag_name=self.runtime.get('pending_auto_tag_name')
        require(isinstance(self.tag_name,str) and self.tag_name=='Pending Auto '+self.project,
                'actual_api_candidate_missing')

    def model(self,payload=None):return C['model_control'](owner,self.directory,self.state,payload)

    def target_snapshot(self,knowledge_id):
        return self.sql(C['pending_snapshot_query'](self.runtime['tenant_id'],self.runtime['knowledge_base_id'],knowledge_id))

    def wk_api(self,method,path,payload=None,token=None):
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',
                 'X-Tenant-ID':str(self.runtime['tenant_id'])}
        code,raw=request(urllib.request.build_opener(),self.wk_base+path,method,headers,
                         None if payload is None else json.dumps(payload).encode())
        return code,json.loads(raw) if raw else {}

    def admin_login(self):
        code,body=e2e['http_json'](self.wk_base,'POST','/api/v1/auth/login',
            {'email':'synthetic-admin@example.test','password':self.passwords['wk_admin']})
        require(code==200 and isinstance(body.get('token'),str),'actual_pending_admin_login_failed')
        return body['token']

    def prepare_operator(self,admin_token):
        # Provision through real HTTP before admission. This lets the original
        # web actor's workspace membership be revoked without orphaning a KB.
        email='synthetic-restore-operator@example.test'
        code,_=e2e['http_json'](self.wk_base,'POST','/api/v1/auth/register',
            {'username':'synthetic-restore-operator','email':email,'password':self.passwords['wk_admin']})
        require(code==201,'actual_pending_operator_registration_failed')
        code,_=self.wk_api('POST',f"/api/v1/tenants/{self.runtime['tenant_id']}/members",
            {'email':email,'role':'owner'},admin_token)
        require(code==201,'actual_pending_operator_membership_failed')
        code,body=e2e['http_json'](self.wk_base,'POST','/api/v1/auth/login',
            {'email':email,'password':self.passwords['wk_admin']})
        require(code==200 and isinstance(body.get('token'),str),'actual_pending_operator_login_failed')
        return body['token']

    def deliver_once(self):
        self.owned();self.no_external_workers()
        for task in ('integration_weknora:deliver-events','integration_weknora:poll-event-status'):
            N['command'](['docker','exec','-u','www-data',self.nc,'php','occ',task,'--quiet'],timeout=30)
        self.record('pending_probe_owned_actual_delivery_status_pass',default_cron=False)

    def upload(self,operation):
        auth=base64.b64encode(('devadmin:'+self.passwords['nc_admin']).encode()).decode()
        url=self.nc_base+'/remote.php/dav/files/devadmin/Published/pending-auto-'+operation+'.txt'
        body=b'The supplied synthetic approval marker is ORCHID-QUARTZ-2749.\n'
        code,_=request(urllib.request.build_opener(),url,'PUT',{'Authorization':'Basic '+auth},body)
        require(code==201,'actual_pending_source_upload_not_created')
        xml=b'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop><oc:fileid/><d:getetag/></d:prop></d:propfind>'
        code,raw=request(urllib.request.build_opener(),url,'PROPFIND',
            {'Authorization':'Basic '+auth,'Depth':'0','Content-Type':'application/xml'},xml)
        require(code==207,'actual_pending_source_identity_failed')
        root=ET.fromstring(raw);fid=root.find('.//{http://owncloud.org/ns}fileid');etag=root.find('.//{DAV:}getetag')
        require(fid is not None and fid.text and fid.text.isdigit() and etag is not None and etag.text,
                'actual_pending_source_identity_missing')
        return {'file_id':int(fid.text),'etag':etag.text.strip('"'),'url_sha256':hashlib.sha256(url.encode()).hexdigest()}

    def find_target(self,file_id):
        require(type(file_id) is int and file_id>0,'invalid_pending_file')
        source=C['canonical_uuid'](self.runtime['source_id'])
        return self.sql("SELECT COALESCE(jsonb_agg(candidate_knowledge_id),'[]'::jsonb) FROM nextcloud_source_versions "
            f"WHERE tenant_id={self.runtime['tenant_id']} AND datasource_id='{source}' AND external_id LIKE '%:{file_id}'")

    def target_source_scope(self,facts):
        source=facts['source'];original=facts['receipt']
        runtime={**self.runtime,'file_id':source['file_id'],'knowledge_id':original['knowledge_id']}
        decision=N['legacy']['source_decision'](self.nc_base,self.state,runtime)
        require(decision.get('allow') is True and decision.get('source_etag')==source['etag'],
                'actual_original_pending_source_not_current')
        return {'file_id':source['file_id'],'source_etag':decision['source_etag'],
                'policy_revision':decision.get('policy_revision'),'personal_source_authorized':True}

    def deny_original_actor_or_model(self,snapshot,token):
        original=C['pending_receipt'](snapshot,self.tag_id)
        actor=snapshot['actors'][0]
        if self.variant=='actor':
            if actor.get('principal_type')=='web_user' and actor.get('user_id'):
                user=C['canonical_uuid'](actor['user_id'])
                code,_=self.wk_api('DELETE',f"/api/v1/tenants/{self.runtime['tenant_id']}/members/{user}",token=token)
                require(code in (200,204),'actual_original_actor_api_revoke_failed')
                active=self.sql(f"SELECT count(*) FROM tenant_members WHERE user_id='{user}' AND tenant_id={self.runtime['tenant_id']} AND status='active' AND deleted_at IS NULL")
                require(active==0,'actual_original_actor_membership_still_active')
            elif type(actor.get('api_key_id')) is int:
                key=actor['api_key_id']
                code,_=self.wk_api('DELETE',f"/api/v1/tenants/{self.runtime['tenant_id']}/api-keys/{key}",token=token)
                require(code in (200,204),'actual_original_actor_api_key_revoke_failed')
                require(self.sql(f"SELECT count(*) FROM tenant_api_keys WHERE id={key} AND revoked_at IS NULL")==0,
                        'actual_original_api_key_still_active')
            else:raise RuntimeError('actual_original_actor_has_no_safe_api_revoke')
            change={'kind':'original_actor_api_revocation','actual_http_status':code}
        else:
            model=C['canonical_uuid'](self.runtime['chat_model_id'])
            query=f"SELECT revision FROM knowledge_enrichment_row_versions WHERE row_kind='model' AND row_id='{model}'"
            before=self.sql(query)
            code,_=self.wk_api('PUT',f'/api/v1/models/{model}/credentials',{'api_key':'synthetic-current-revoked'},token)
            require(code==200,'actual_original_model_api_change_failed')
            after=self.sql(query)
            require(type(before) is int and type(after) is int and after>before,'actual_model_control_revision_not_changed')
            change={'kind':'original_model_credentials_api_change','actual_http_status':code,
                    'before_revision':before,'after_revision':after}
        # Never re-capture or re-seal the pending task after API mutation.
        require(C['pending_receipt'](self.target_snapshot(original['knowledge_id']),self.tag_id)==original,
                'negative_api_change_recreated_original_intent')
        return change

    def capture(self,baseline_history):
        require(not (self.evidence/'checkpoint.json').exists(),'checkpoint_exists')
        require(baseline_history is not None,'actual_two_round_pending_baseline_required')
        token=self.admin_login();operator=self.prepare_operator(token) if self.variant=='actor' else token
        kb=C['canonical_uuid'](self.runtime['knowledge_base_id'])
        candidates=self.sql("SELECT COALESCE(jsonb_agg(id ORDER BY id),'[]'::jsonb) FROM knowledge_tags "
            f"WHERE tenant_id={self.runtime['tenant_id']} AND knowledge_base_id='{kb}'")
        require(candidates==[self.tag_id],'fresh_pending_fixture_requires_exact_single_api_created_candidate')
        baseline=json.loads(plain(baseline_history).read_text())
        receipt=self.history_receipt(baseline=baseline);self.idle()
        operation=str(uuid.uuid4())
        self.model({'action':'arm','operation_id':operation,'kind':'question',
                    'delay_seconds':self.state['mock_postprocess_control_max_seconds'],'expected_tag_name':self.tag_name})
        source=self.upload(operation);knowledge=None;snapshot=None
        deadline=time.monotonic()+self.state['mock_postprocess_control_max_seconds']
        while time.monotonic()<deadline:
            self.deliver_once();ids=self.find_target(source['file_id'])
            require(len(ids)<=1,'pending_source_multiple_generations')
            if ids:
                knowledge=C['canonical_uuid'](ids[0]);snapshot=self.target_snapshot(knowledge)
                try:last_real_enrichment(snapshot,self.model(),operation);break
                except RuntimeError:pass
            time.sleep(.25)
        else:raise RuntimeError('actual_last_enrichment_pending_fault_boundary_not_reached')
        # Actual sequential boundary: stop the exact owned broker while a real
        # final enrichment request is waiting, then release its HTTP response.
        self.compose('stop','wk-redis');self.stopped('wk-redis')
        self.model({'action':'release','operation_id':operation})
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            snapshot=self.target_snapshot(knowledge)
            try:
                original=C['pending_receipt'](snapshot,self.tag_id)
                model=C['released_model_window'](self.model(),operation)
                break
            except RuntimeError:pass
            require(owner['docker_inspect']('container',self.app)['State']['Running'] is True,
                    'actual_broker_outage_exited_app_before_pending_seal')
            time.sleep(.25)
        else:raise RuntimeError('actual_broker_outage_did_not_seal_undelivered_intent')
        negative=self.deny_original_actor_or_model(snapshot,operator) if self.variant in ('actor','model') else None
        require(C['pending_receipt'](self.target_snapshot(knowledge),self.tag_id)==original,
                'original_pending_changed_before_checkpoint')
        facts={'variant':self.variant,'operation_id':operation,'receipt':original,'model_window':model,
               'source':source,'negative_api_change':negative,'broker_stopped_before_response_release':True,
               'original_pending_after_real_broker_outage':True}
        facts['target_source_scope']=self.target_source_scope(facts)
        identity=self.identity();baseline_scope=self.source_scope(check_wk=False)
        anchors=self.anchor_volume_facts();publication=self.publication_current()
        self.stop_actors();C['pending_quiescence'](self.target_snapshot(knowledge))
        self.stopped('wk-redis')
        write_json(self.evidence/'actual-pending-before-checkpoint.json',facts)
        self.capture_full_checkpoint(receipt,identity,baseline_scope,anchors,publication,facts)
        self.record('actual_pending_full_application_checkpoint',runtime_acceptance=False)

    def pending_checkpoint(self):
        checkpoint=self.load_checkpoint();facts=checkpoint.get('pending_auto') or {}
        require(facts.get('variant')==self.variant and facts.get('original_pending_after_real_broker_outage') is True,
                'pending_auto_checkpoint_variant_changed')
        return checkpoint

    def fault(self):
        checkpoint=self.pending_checkpoint();original=checkpoint['pending_auto']['receipt']
        self.compose('start','nc-db','wk-db','nc-redis','wk-redis');self.databases_ready();self.stopped('wk-app')
        require(C['pending_receipt'](self.target_snapshot(original['knowledge_id']),self.tag_id)==original,
                'original_pending_changed_before_actual_sql_fault')
        self.compose('start','nextcloud')
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try:
                nc_config=self.nc_configuration();break
            except (OSError,RuntimeError):time.sleep(.5)
        else:raise RuntimeError('pending_fault_nextcloud_unavailable')
        require(nc_config==checkpoint['nextcloud_configuration'],'pending_fault_configuration_changed_before_api')
        config_fault=self.fault_nc_configuration(checkpoint)
        controls=N['fault_checkpoint_control'](self.directory,self.evidence,self.state,checkpoint,self.provenance)
        fault_name='pending-full-restore-fault-'+uuid.uuid4().hex+'.txt'
        auth=base64.b64encode(('devadmin:'+self.passwords['nc_admin']).encode()).decode()
        url=self.nc_base+'/remote.php/dav/files/devadmin/'+fault_name
        code,_=request(urllib.request.build_opener(),url,'PUT',{'Authorization':'Basic '+auth},b'actual owned unpublished restore fault bytes')
        require(code==201,'actual_pending_full_files_fault_not_created')
        fault_file_id=e2e['file_id'](url,{'Authorization':'Basic '+auth})
        removed=self.sql(f"WITH removed AS (DELETE FROM task_pending_ops WHERE id={original['intent_id']} AND tenant_id={original['tenant_id']} AND op='auto_tag_completion' RETURNING id) SELECT to_jsonb(count(*)) FROM removed")
        require(removed==1,'actual_pending_sql_loss_not_injected')
        self.stop_actors()
        write_json(self.evidence/'actual-fault.json',{'mode':'pending_auto','source_unchanged':True,
            'removed_pending_intent_id':original['intent_id'],'original_payload_sha256':original['payload_sha256'],
            'fault_file_name':fault_name,'fault_file_id':fault_file_id,
            'body_anchors_retained':self.anchor_volume_facts(),'nextcloud_configuration_fault':config_fault,
            'configuration_control_fault':controls})
        self.record('actual_pending_sql_loss_and_application_configuration_fault')

    def closed(self):
        checkpoint=self.pending_checkpoint()
        restored=json.loads(plain(self.evidence/'actual-data-restore.json').read_text())
        check={**checkpoint,'_actual_checkpoint_sha256':sha(self.evidence/'checkpoint.json')}
        original=checkpoint['pending_auto']['receipt']
        for service in self.actors():self.stopped(service)
        self.no_external_workers();self.restored_configuration(checkpoint,restored)
        self.packaged_tools();self.body_cli('reconcile');self.body_cli('verify')
        require(self.identity()==checkpoint['identity'],'pending_restored_control_identity_changed')
        N['pinned_publication'](checkpoint)
        immutable_pending_restored(check,restored,self.target_snapshot(original['knowledge_id']))
        a=checkpoint['external_recovery_anchor']
        args=tuple(value for field,flag in [('instance_id','instance-id'),('stream_id','stream-id'),
            ('record_sha256','checkpoint-record-sha256'),('sequence','checkpoint-sequence'),
            ('database_chain_sha256','checkpoint-database-chain-sha256')]
            for value in ('-'+flag,str(a[field])))
        live=self.publication_cli('capture',args)
        expected=json.loads(plain(Path(checkpoint['original_inventory']['path'])).read_text())
        require(live==expected,'pending_restored_full_original_source_inventory_changed')
        fault=json.loads(plain(self.evidence/'actual-fault.json').read_text())
        require(type(fault.get('fault_file_id')) is int and fault['fault_file_id']>0,
                'pending_actual_files_fault_identity_missing')
        require(self.sql(f"SELECT count(*) FROM oc_filecache WHERE fileid={fault['fault_file_id']}",cloud=True)==0,
                'pending_full_restore_filecache_fault_survived')
        name=fault.get('fault_file_name')
        require(isinstance(name,str) and name.startswith('pending-full-restore-fault-') and
                name.endswith('.txt') and name==Path(name).name,'pending_fault_file_name_changed')
        code="$CONFIG=[];require '/var/www/html/config/config.php';$p=$CONFIG['datadirectory'].'/devadmin/files/'.$argv[1];echo json_encode(['fault_file_absent'=>!file_exists($p)],JSON_THROW_ON_ERROR);"
        command=owner['compose_command'](self.directory,self.state,'run','-T','--rm','--no-deps',
            '--name',self.project+'-nextcloud-99','--user','www-data','--entrypoint','php','nextcloud','-r',code,name)
        self.owned();physical=json.loads(N['command'](command,timeout=120));self.owned()
        require(physical=={'fault_file_absent':True},'pending_full_restore_physical_file_fault_survived')
        keep_exact_closed_receipt(self.evidence/'closed-pending-gate.json',{'candidate':self.provenance,
            'checkpoint_sha256':sha(self.evidence/'checkpoint.json'),'original':original,'accepted':False})

    def reopen(self):
        self.closed();checkpoint=self.pending_checkpoint();facts=checkpoint['pending_auto'];original=facts['receipt']
        self.compose('start','nextcloud','mock-embedding','docreader')
        self.stopped('wk-app')
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try:self.source_scope(check_wk=False);break
            except (OSError,RuntimeError):time.sleep(.5)
        else:raise RuntimeError('pending_current_baseline_source_unavailable')
        require(self.target_source_scope(facts)==facts['target_source_scope'],'pending_target_source_scope_changed_before_startup')
        if self.variant=='source':
            admin,csrf=login(self.nc_base,'devadmin',self.passwords['nc_admin'])
            url=self.nc_base+'/index.php/apps/integration_weknora/api/v1/admin/bindings/synthetic-published/files/'+str(facts['source']['file_id'])+'/withdraw'
            code,_=request(admin,url,'POST',{'requesttoken':csrf,'Content-Type':'application/json'},b'{}')
            require(code==200,'actual_restored_original_source_api_withdraw_failed')
            self.record('actual_restored_target_source_withdraw_wk_closed',actual_http_status=code)
        before=self.model({'action':'configure','expected_tag_name':self.tag_name})
        require(all(v=={'started':0,'completed':0,'failed':0} for v in before['calls'].values()),
                'pending_restore_model_process_not_fresh')
        self.compose('start','wk-app');self.health()
        first_start=owner['docker_inspect']('container',self.app)['State']['StartedAt']
        deadline=time.monotonic()+120;result=None
        while time.monotonic()<deadline:
            observed=self.target_snapshot(original['knowledge_id']);after=self.model()
            try:
                result=C['startup_result'](observed,original,before,after,self.variant!='positive');break
            except RuntimeError:pass
            time.sleep(.5)
        else:raise RuntimeError('actual_normal_startup_did_not_finish_original_pending_intent')
        # Restart only the real app. Keep the actual model request counters;
        # terminal SQL state must prevent another model invocation/publication.
        self.compose('restart','wk-app');self.health()
        require(owner['docker_inspect']('container',self.app)['State']['StartedAt']!=first_start,
                'actual_second_normal_app_startup_not_established')
        deadline=time.monotonic()+70
        while time.monotonic()<deadline:
            C['startup_result'](self.target_snapshot(original['knowledge_id']),original,before,self.model(),self.variant!='positive')
            time.sleep(.5)
        snapshot=self.target_snapshot(original['knowledge_id'])
        require(all(snapshot['stats'][key]==0 for key in ('body_leases','content_leases','active_auto')),
                'pending_startup_original_leases_or_intent_not_terminal')
        restored=json.loads(plain(self.evidence/'actual-data-restore.json').read_text())
        write_json(self.evidence/'actual-pending-startup.json',{'variant':self.variant,'candidate':self.provenance,
            'checkpoint_sha256':sha(self.evidence/'checkpoint.json'),'original':original,'result':result,
            'actual_full_db_files_configuration_restore':restored['actual_dual_full_pg_restore'] and restored['actual_nc_configuration_restored'],
            'actual_normal_app_startups':2,'second_startup_model_calls_added':0,
            'pending_auto_component_restore_accepted':True,'whole_v1_or_external_model_accepted':False})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ('scratch','candidate-manifest','evidence'):parser.add_argument('--'+flag,type=Path,required=True)
    parser.add_argument('--profile',choices=('c6','rag'),required=True)
    parser.add_argument('--variant',choices=('positive','source','actor','model'),required=True)
    parser.add_argument('--phase',choices=('capture','fault','restore-data','closed','reopen'),required=True)
    parser.add_argument('--baseline-history',type=Path)
    args=parser.parse_args();probe=None
    try:
        probe=PendingRestore(args.scratch,args.candidate_manifest,args.profile,args.evidence,args.variant)
        if args.phase=='capture':probe.capture(args.baseline_history)
        else:getattr(probe,{'fault':'fault','restore-data':'restore_data','closed':'closed','reopen':'reopen'}[args.phase])()
        print(json.dumps({'phase':args.phase,'project':probe.project,'terminal':0,
                          'runtime_receipt':str(probe.evidence/'actual-pending-startup.json'),
                          'source_preparation_or_method_tests_attest_runtime':False}))
        return 0
    except Exception as error:
        if probe:
            held=False
            try:probe.stop_actors();held=True
            except Exception:pass
            probe.record('pending_auto_phase_failed',phase_requested=args.phase,error_type=type(error).__name__,
                         all_actors_closed=held,runtime_acceptance=False)
        print('Owned pending Auto restore refused: '+type(error).__name__,file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
