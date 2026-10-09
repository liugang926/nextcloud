#!/usr/bin/env python3
"""Phase-gated normal candidate restore on one existing marker-owned fixture.

Never creates another stack and never removes/restores current body anchors.
No phase may be relabeled as full acceptance. Candidate and source gate bundles
are immutable private inputs; raw backup files and receipts stay owner-only.
"""
import argparse
import base64
import datetime as dt
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT/'apps/integration_weknora/tests'))
from publication_http_smoke import login,request
owner=runpy.run_path(str(HERE/'synthetic-ldap-fixture.py'))
e2e=runpy.run_path(str(HERE/'synthetic-ldap-e2e.py'))
qa=runpy.run_path(str(HERE/'synthetic-ldap-ask-handoff.py'))
history=runpy.run_path(str(HERE/'synthetic-ldap-history-acceptance.py'))
images=runpy.run_path(str(HERE/'inspect-weknora-candidate-images.py'))
legacy=runpy.run_path(str(HERE/'isolated-dual-service-restore.py'))
publication=runpy.run_path(str(HERE/'publication-recovery-journal.py'))
gate=runpy.run_path(str(HERE/'coordinated-recovery-gate.py'))
BODY_VOLUMES=frozenset(('wk-body-journal','wk-body-key','wk-body-pin'))
DATA_VOLUMES=frozenset(('nc-html','wk-data','nc-redis-data','docreader-tmp'))
CONTROL_FILES=('state.json','compose.yaml','passwords.json','runtime.json','fixture.json')
HASH=re.compile(r'[0-9a-f]{64}\Z')

def require(value,code):
    if not value:raise RuntimeError(code)

def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()

def plain(path,directory=False):
    path=Path(path)
    require(path.is_absolute() and path.resolve()==path,'noncanonical_private_path')
    st=path.lstat()
    require(st.st_uid==os.getuid() and stat.S_IMODE(st.st_mode)==(0o700 if directory else 0o600),'private_path_owner_mode')
    require(stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode) and st.st_nlink==1,'private_path_type')
    return path

def write_json(path,data):
    raw=json.dumps(data,sort_keys=True,indent=2).encode()+b'\n'
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())

def command(arguments,stdin=None,stdout=None,timeout=180):
    input_args={'input':stdin} if isinstance(stdin,bytes) else {'stdin':stdin or subprocess.DEVNULL}
    result=subprocess.run(arguments,**input_args,stdout=stdout or subprocess.PIPE,
        stderr=subprocess.PIPE,timeout=timeout,check=False)
    require(result.returncode==0,'owned_command_failed_'+str(result.returncode))
    return result.stdout if stdout is None else b''

def volume_restore_guard(role):
    require(role in DATA_VOLUMES and role not in BODY_VOLUMES,'refused_anchor_or_unknown_volume_restore')

def inspect_tar(path):
    plain(path)
    with tarfile.open(path,'r') as archive:
        for item in archive:
            p=Path(item.name)
            require(not p.is_absolute() and '..' not in p.parts,'unsafe_tar_path')
            require(not item.isdev() and not item.isfifo(),'unsafe_tar_special_file')
            if item.issym() or item.islnk():
                target=Path(item.linkname)
                require(not target.is_absolute() and '..' not in target.parts,'unsafe_tar_link')

def pinned_publication(checkpoint):
    current=checkpoint['publication_anchor_current']
    live=publication['verify'](Path(current['ledger']),Path(current['key']))
    require(live['complete_to_head'] is True and live['record_sha256']==current['record_sha256'] and
            live['sequence']==current['metadata']['sequence'],'post_checkpoint_publication_requires_explicit_closed_recovery')
    return live

def runtime_scope(runtime):
    require(runtime.get('binding_id')=='synthetic-published' and
            runtime.get('publication_root','group_share')=='group_share','unexpected_source_fixture')
    for key in ('source_id','knowledge_id','knowledge_base_id','operation_id','model_id','chat_model_id'):
        require(str(uuid.UUID(runtime[key]))==runtime[key],'invalid_runtime_uuid')
    require(type(runtime.get('tenant_id')) is int and runtime['tenant_id']>0 and
            type(runtime.get('file_id')) is int and runtime['file_id']>0,'invalid_runtime_scope')
    require(type(runtime.get('root_file_id')) is int and runtime['root_file_id']>0 and
            str(runtime.get('share_id','')).isdigit(),'invalid_runtime_source_root_share')

def nc_application_provenance(repository=ROOT):
    scope='apps/integration_weknora'
    require(not command(['git','-C',str(repository),'status','--porcelain','--untracked-files=all','--',scope]).strip(),
            'nextcloud_application_source_not_frozen')
    paths=command(['git','-C',str(repository),'ls-files','-z','--',scope]).split(b'\0')
    entries=[]
    for value in paths:
        if not value:continue
        name=value.decode();path=repository/name
        require(path.is_file() and not path.is_symlink(),'nextcloud_application_source_alias')
        entries.append((name,sha(path)))
    require(entries,'nextcloud_application_source_missing')
    return {'tracked_source_sha256':hashlib.sha256(json.dumps(entries,separators=(',',':')).encode()).hexdigest(),
        'source_files':len(entries)}


class Restore:
    def __init__(self,scratch,manifest,profile,evidence):
        self.directory,self.state=owner['owned_state'](scratch)
        plain(self.directory,True)
        for name in CONTROL_FILES:plain(self.directory/name)
        self.owned()
        require(self.state.get('body_journal_initialized') is True,'body_journal_not_bootstrapped')
        self.runtime=json.loads(plain(self.directory/'runtime.json').read_text())
        self.fixture=json.loads(plain(self.directory/'fixture.json').read_text())
        runtime_scope(self.runtime)
        require(self.fixture.get('synthetic_fixture') is True and
                self.fixture.get('knowledge_id')==self.runtime['knowledge_id'],'fixture_runtime_drift')
        self.passwords=json.loads(plain(self.directory/'passwords.json').read_text())
        raw=plain(manifest).read_bytes()
        entry=json.loads(raw)['profiles'][profile]
        app=images['check_image'](images['inspect'](self.state['weknora_image']),entry,'app')
        require(app['image_id']==self.state['weknora_image_id'],'candidate_app_role_drift')
        self.provenance={'profile':profile,'manifest_sha256':hashlib.sha256(raw).hexdigest(),'app':app}
        config=json.loads((self.directory/'compose.yaml').read_text())
        mounts=[m.split(':') for m in config['services']['nextcloud']['volumes']
                if isinstance(m,str) and ':custom_apps/integration_weknora:' in m]
        # Compose generated by the fixture uses an absolute target path.
        if not mounts:
            mounts=[m.split(':') for m in config['services']['nextcloud']['volumes']
                if isinstance(m,str) and ':/var/www/html/custom_apps/integration_weknora:' in m]
        require(len(mounts)==1 and len(mounts[0])==3 and mounts[0][2]=='ro','nextcloud_application_mount_not_immutable')
        source=Path(mounts[0][0]);require(source.is_absolute() and source.resolve()==source,'nextcloud_application_source_alias')
        self.nc_source_root=source.parents[1]
        self.provenance['nextcloud_app']=nc_application_provenance(self.nc_source_root)
        if self.state.get('weknora_ui_image'):
            self.provenance['ui']=images['check_image'](images['inspect'](self.state['weknora_ui_image']),entry,'ui')
            require(self.provenance['ui']['image_id']==self.state['weknora_ui_image_id'],'candidate_ui_role_drift')
        self.evidence=Path(evidence).resolve()
        if not self.evidence.exists():self.evidence.mkdir(mode=0o700)
        plain(self.evidence,True)
        require(self.directory not in self.evidence.parents and self.evidence!=self.directory,
                'evidence_must_be_independent_of_fixture_controls')
        self.project=self.state['project'];self.nc=self.project+'-nextcloud-1';self.app=self.project+'-wk-app-1'
        self.nc_db=self.project+'-nc-db-1';self.wk_db=self.project+'-wk-db-1'
        self.nc_base=f"http://127.0.0.1:{self.state['ports']['nextcloud']}"
        self.wk_base=f"http://127.0.0.1:{self.state['ports']['weknora']}"
        self.event_file=self.evidence/'phase-events.jsonl'
        self.errors=None

    def owned(self):
        owner['assert_owned_resources'](self.directory,self.state)
        if hasattr(self,'provenance'):
            require(nc_application_provenance(self.nc_source_root)==self.provenance['nextcloud_app'],'nextcloud_application_source_changed')
    def sql(self,q,cloud=False):return e2e['sql_json'](self.nc_db if cloud else self.wk_db,q)
    def record(self,phase,**facts):
        if self.event_file.exists():plain(self.event_file)
        event={'phase':phase,'at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),**facts}
        fd=os.open(self.event_file,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as output:output.write(json.dumps(event,separators=(',',':'))+'\n');output.flush();os.fsync(output.fileno())
    def compose(self,*args):
        self.owned();value=command(owner['compose_command'](self.directory,self.state,*args),timeout=180);self.owned();return value
    def stopped(self,service):
        item=owner['docker_inspect']('container',self.project+'-'+service+'-1')
        require(item['State']['Running'] is False,'actor_not_stopped_'+service)
    def actors(self):return ['wk-app','nextcloud','docreader','mock-embedding']+(['wk-ui'] if self.state.get('weknora_ui_image') else [])
    def no_external_workers(self):
        # The caller must not share an active probe-owned sender/native stream.
        # Process inspection is actual evidence, not a supplied boolean.
        flag=self.directory/'active-probe-workers.json'
        require(not flag.exists(),'external_probe_worker_must_be_stopped')
        if owner['docker_inspect']('container',self.nc)['State']['Running']:
            top=command(['docker','top',self.nc,'-eo','args'],timeout=30).decode(errors='replace')
            require(not any(name in top for name in ('integration_weknora:deliver-events',
                'integration_weknora:poll-event-status','cron.php')),'owned_nc_background_pass_still_running')
    def stop_actors(self):
        self.no_external_workers();self.owned()
        self.compose('stop',*self.actors())
        for service in self.actors():self.stopped(service)
        self.record('all_app_reader_builder_actors_stopped')

    def identity(self):
        r=self.runtime
        return self.sql("SELECT jsonb_build_object('tenant',s.tenant_id,'kb',s.knowledge_base_id,"
            "'source',s.id,'config_sha256',p.datasource_config_sha256,'pair_operation',p.operation_id,"
            "'pair_instance',p.nextcloud_instance_id,'binding',p.binding_id,'pair_epoch',p.publication_epoch,"
            "'pair_key_id',p.key_id,'pair_state',p.state,'source_state',s.status,"
            "'event_connection',(SELECT connection_id FROM nextcloud_event_connections WHERE datasource_id=s.id AND status='active'),"
            "'baseline_candidate',(SELECT candidate_knowledge_id FROM nextcloud_source_versions "
            f"WHERE datasource_id=s.id AND external_id LIKE '%:{r['file_id']}'),"
            "'body_journal',(SELECT journal_id FROM original_body_journal_heads WHERE id=1),"
            "'kb_policy_digest',(SELECT md5(COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.id),'[]'::jsonb)::text) "
            f"FROM resource_access_policies x WHERE x.tenant_id={r['tenant_id']} AND x.resource_id='{r['knowledge_base_id']}'),"
            "'kb_grants_digest',(SELECT md5(COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.id),'[]'::jsonb)::text) "
            f"FROM resource_group_grants x WHERE x.tenant_id={r['tenant_id']} AND x.resource_id='{r['knowledge_base_id']}'),"
            "'workspace_grants_digest',(SELECT md5(COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.id),'[]'::jsonb)::text) "
            f"FROM tenant_group_role_grants x WHERE x.tenant_id={r['tenant_id']}),"
            "'model_digest',(SELECT md5(string_agg(id||':'||parameters::text,',' ORDER BY id)) FROM models "
            f"WHERE id IN ('{r['model_id']}','{r['chat_model_id']}'))) "
            "FROM data_sources s JOIN nextcloud_source_pairings p ON p.datasource_id=s.id "
            f"WHERE s.id='{r['source_id']}' AND s.tenant_id={r['tenant_id']} AND p.operation_id='{r['operation_id']}'")

    def source_scope(self,check_wk=True):
        # Current personal source authority is separate from the local DB tuple.
        decision=legacy['source_decision'](self.nc_base,self.state,self.runtime)
        require(decision.get('allow') is True and isinstance(decision.get('source_etag'),str) and
                decision['source_etag'],'actual_source_scope_not_authorized')
        if check_wk:qa['source_context_unchanged'](self.state,self.nc_base,self.passwords,self.runtime)
        # Before WK opens, its complete local grant snapshots are checked by
        # identity(), while this real signed NC read proves Alice/source scope.
        return {'directory_id':self.state['directory_id'],'alice_guid':self.state['guids']['alice'].lower(),
            'binding_id':self.runtime['binding_id'],'root_file_id':self.runtime['root_file_id'],
            'file_id':self.runtime['file_id'],'share_id':self.runtime['share_id'],
            'source_etag':decision['source_etag'],'policy_revision':decision.get('policy_revision'),
            'personal_source_authorized':True}

    def idle(self,timeout=120):
        r=self.runtime;deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            # This fixture has no default cron service. These are real,
            # explicit probe-owned delivery/status CLI passes, with their
            # actual execution recorded; they do not attest default cadence.
            self.owned();self.no_external_workers()
            for task in ('integration_weknora:deliver-events','integration_weknora:poll-event-status'):
                command(['docker','exec','-u','www-data',self.nc,'php','occ',task,'--quiet'],timeout=30)
            self.record('probe_owned_actual_event_delivery_status_cli_pass',default_cron=False)
            stats=self.sql("SELECT jsonb_build_object('running',(SELECT count(*) FROM sync_logs "
                f"WHERE tenant_id={r['tenant_id']} AND status='running'),'processing',(SELECT count(*) FROM knowledges "
                f"WHERE tenant_id={r['tenant_id']} AND parse_status IN ('pending','processing','finalizing')),"
                "'staged',(SELECT count(*) FROM nextcloud_source_versions "
                f"WHERE tenant_id={r['tenant_id']} AND state='staging'),"
                "'auto',(SELECT count(*) FROM task_pending_ops WHERE task_type='knowledge:auto_tag' "
                f"AND tenant_id={r['tenant_id']} "
                "AND op IN ('auto_tag_completion','auto_tag_running')),'content_leases',(SELECT count(*) FROM nextcloud_content_leases "
                f"WHERE tenant_id={r['tenant_id']} AND released_at_ms IS NULL "
                "AND expires_at_ms>EXTRACT(EPOCH FROM clock_timestamp())*1000),'body_leases',(SELECT count(DISTINCT l.lease_id) "
                "FROM original_body_leases l JOIN original_body_payloads b ON b.id=l.body_id "
                f"WHERE b.tenant_id={r['tenant_id']} AND l.released_at_ms IS NULL "
                "AND l.expires_at_ms>EXTRACT(EPOCH FROM clock_timestamp())*1000))")
            outbox=self.sql("SELECT jsonb_build_object('head',(SELECT COALESCE(MAX(id),0) FROM oc_weknora_outbox "
                "WHERE binding_id='synthetic-published'),'received',received_id,'status',status) FROM oc_weknora_event_conn "
                "WHERE binding_id='synthetic-published'",cloud=True)
            cursor=self.sql("SELECT jsonb_build_object('received',p.received_id,'applied',d.applied_id) "
                "FROM nextcloud_event_connections c JOIN nextcloud_event_checkpoint p USING(connection_id) "
                "JOIN nextcloud_event_dispatch d USING(connection_id) "
                f"WHERE c.datasource_id='{r['source_id']}' AND c.status='active'")
            if (all(type(value) is int and value==0 for value in stats.values()) and
                outbox['status']=='active' and outbox['head']<=outbox['received']<=cursor['received']<=cursor['applied']):return stats
            time.sleep(.5)
        raise RuntimeError('actual_idle_lease_zero_not_established')

    def token(self):return qa['wait_ldap_login'](self.wk_base,'alice',self.passwords['alice'])
    def history_receipt(self,create=False,original=None,baseline=None):
        token=self.token();human=qa['citation_url'](self.project,self.runtime['knowledge_id'],self.runtime['file_id'],self.nc_base)
        if create:
            session=qa['answer_and_citation'](self.wk_base,token,self.runtime,human);expected=1
        elif baseline:
            require(baseline.get('project')==self.project and type(baseline.get('completed_history_count')) is int and
                0<baseline['completed_history_count']<=10,'baseline_history_fixture_or_count_invalid')
            session=baseline['session_id'];expected=baseline['completed_history_count']
        else:session=original['session_id'];expected=original['completed_count']
        rows=history['history'](self.wk_base,session,token,expected)
        ids=sorted(row['id'] for row in rows)
        if original:
            require(ids==original['message_ids'] and hashlib.sha256(human.encode()).hexdigest()==original['citation_sha256'],
                    'original_completed_message_ids_or_citation_changed')
        replay_facts={}
        for message in ids:
            code,raw=history['replay'](self.wk_base,session,message,token)
            require(code==200 and qa['MARKER'].encode() in raw and human.encode() in raw,'original_native_replay_or_citation_failed')
            replay_facts[message]={'http_status':code,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
        # Actual original admissions, generation origins and body heads are
        # required; HTTP success/prepared state cannot replace these facts.
        safe_session=str(uuid.UUID(session));safe_ids=[str(uuid.UUID(x)) for x in ids]
        quoted=','.join("'"+x+"'" for x in safe_ids)
        proof=self.sql("SELECT jsonb_build_object('material_receipts',(SELECT count(*) FROM message_material_receipts "
            f"WHERE message_id IN ({quoted})),'original_admissions',(SELECT count(*) FROM message_material_admissions "
            f"WHERE message_id IN ({quoted}) AND session_id='{safe_session}'),'generation_origins',(SELECT count(*) FROM message_generation_origins "
            f"WHERE message_id IN ({quoted}) AND session_id='{safe_session}'),'body_heads',(SELECT count(*) FROM original_body_field_heads "
            f"WHERE source_table='messages' AND row_key IN ({quoted})))")
        require(proof['material_receipts']==len(ids) and proof['original_admissions']>=len(ids) and
                proof['generation_origins']>=len(ids) and proof['body_heads']>=len(ids),'actual_message_original_body_coverage_missing')
        originals=self.sql("SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY r.message_id),'[]'::jsonb) FROM "
            "(SELECT message_id,admission_id FROM message_material_receipts "
            f"WHERE message_id IN ({quoted})) r")
        body_heads=self.sql("SELECT COALESCE(jsonb_agg(to_jsonb(h) ORDER BY h.slot_id),'[]'::jsonb) FROM "
            "(SELECT h.slot_id,h.body_id,h.revision,h.admission_id,h.source_identity_sha256,p.body_sha256,p.body_bytes,p.state "
            "FROM original_body_field_heads h JOIN original_body_payloads p ON p.id=h.body_id "
            f"WHERE h.source_table='messages' AND h.row_key IN ({quoted})) h")
        require(body_heads and all(h['state']=='live' and h['body_bytes']>0 for h in body_heads),'original_message_body_not_live')
        if original:
            require(originals==original['material_receipts'] and body_heads==original['message_body_heads'],
                    'original_message_admission_or_body_identity_changed')
        return {'session_id':session,'message_ids':ids,'completed_count':len(ids),
            'citation_sha256':hashlib.sha256(human.encode()).hexdigest(),'original_body_proof':proof,
            'material_receipts':originals,'message_body_heads':body_heads,'replay_metadata':replay_facts,
            'history_original':True,'native_completed_replay_original':True}

    def anchor_volume_facts(self):
        self.owned();facts={}
        for role in sorted(BODY_VOLUMES):
            name=self.project+'_'+role;item=owner['docker_inspect']('volume',name)
            labels=item.get('Labels') or {}
            require(labels.get(owner['OWNER_LABEL'])==self.state['owner_token'] and
                labels.get('com.docker.compose.volume')==role,'body_anchor_owner_role_changed')
            facts[role]={'name':name,'created_at':item.get('CreatedAt'),'labels':labels}
        return facts

    def body_cli(self,mode,upgrade_authorization=None):
        require(mode in ('verify','reconcile','upgrade'),'refused_body_initialization_on_restore')
        if mode=='upgrade':require(upgrade_authorization=='body_owner_explicit_upgrade','body_upgrade_not_authorized')
        for service in self.actors():self.stopped(service)
        script='''set -eu
cd /app
export WEKNORA_ORIGINAL_BODY_MAINTENANCE_DSN="postgres://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}?sslmode=disable"
exec gosu appuser original-body-retention -driver postgres -mode "$1"
'''
        self.owned();command(owner['compose_command'](self.directory,self.state,'run','--rm','--no-deps',
            '--name',self.project+'-wk-app-99','--entrypoint','/bin/sh','wk-app','-c',script,'sh',mode),timeout=240)
        self.record('packaged_body_'+mode,terminal=0)

    def packaged_tools(self):
        for service in self.actors():self.stopped(service)
        script='set -eu; test -x /usr/local/bin/original-body-retention; test -x /usr/local/bin/publication-recovery; test -x /usr/local/bin/capacity-reconcile'
        self.owned();command(owner['compose_command'](self.directory,self.state,'run','--rm','--no-deps',
            '--name',self.project+'-wk-app-99','--entrypoint','/bin/sh','wk-app','-c',script),timeout=60)
        self.owned();self.record('same_candidate_packaged_offline_tools_present',terminal=0)

    def publication_cli(self,mode,args=(),files=None):
        require(mode in ('capture','apply','snapshot','reconcile'),'invalid_publication_cli_mode')
        for service in self.actors():self.stopped(service)
        payload=io.BytesIO()
        with tarfile.open(fileobj=payload,mode='w') as archive:
            for name,data in sorted((files or {}).items()):
                require(name in ('inventory','plan','key'),'unsafe_maintenance_bundle_name')
                item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o600;archive.addfile(item,io.BytesIO(data))
        payload.seek(0)
        script='''set -eu
umask 077
d=$(mktemp -d)
trap 'rm -rf -- "$d"' EXIT
tar -C "$d" -xf -
printf 'postgres://%s:%s@%s:%s/%s?sslmode=disable' "$DB_USER" "$DB_PASSWORD" "$DB_HOST" "$DB_PORT" "$DB_NAME" > "$d/dsn"
chown -R appuser:appuser "$d"
cd "$d"
if test -f "$d/plan"; then set -- "$@" -plan "$d/plan"; fi
if test -f "$d/key"; then set -- "$@" -key-file "$d/key"; fi
if test -f "$d/inventory"; then set -- "$@" -inventory "$d/inventory"; fi
gosu appuser publication-recovery -postgres-connection-file "$d/dsn" -output "$d/output" "$@" >&2
cat "$d/output"
'''
        self.owned();raw=command(owner['compose_command'](self.directory,self.state,'run','-T','--rm','--no-deps',
            '--name',self.project+'-wk-app-99','--entrypoint','/bin/sh','wk-app','-c',script,'sh','-mode',mode,*args),
            stdin=payload.getvalue(),timeout=660)
        self.owned();result=json.loads(raw)
        self.record('actual_packaged_publication_'+mode,terminal=0,output_sha256=hashlib.sha256(raw).hexdigest())
        return result

    def nc_offline(self,*args):
        for service in self.actors():self.stopped(service)
        self.owned();raw=command(owner['compose_command'](self.directory,self.state,'run','--rm','--no-deps',
            '--name',self.project+'-nextcloud-99','--user','www-data','--entrypoint','php','nextcloud','occ',*args),timeout=120)
        self.owned();return raw

    def nc_recovery(self,plan,key,inspect=False):
        for service in self.actors():self.stopped(service)
        payload=io.BytesIO()
        with tarfile.open(fileobj=payload,mode='w') as archive:
            for name,data in (('plan',plan),('key',key)):
                item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o600;archive.addfile(item,io.BytesIO(data))
        script='''set -eu
umask 077
d=$(mktemp -d)
trap 'rm -rf -- "$d"' EXIT
tar -C "$d" -xf -
php custom_apps/integration_weknora/appinfo/recovery-console.php --plan="$d/plan" --key-file="$d/key" "$@"
'''
        self.owned();raw=command(owner['compose_command'](self.directory,self.state,'run','-T','--rm','--no-deps',
            '--name',self.project+'-nextcloud-99','--user','www-data','--entrypoint','/bin/sh','nextcloud',
            '-c',script,'sh',*(['--inspect-state'] if inspect else [])),stdin=payload.getvalue(),timeout=240)
        self.owned();self.record('actual_offline_nc_publication_'+('snapshot' if inspect else 'apply'),terminal=0)
        return json.loads(raw)

    def databases_ready(self):
        for container,user,database in ((self.nc_db,'nextcloud','nextcloud'),(self.wk_db,'weknora','weknora')):
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                self.owned()
                result=subprocess.run(['docker','exec',container,'pg_isready','-U',user,'-d',database],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
                if result.returncode==0:break
                time.sleep(.5)
            else:raise RuntimeError('owned_database_not_ready')

    def dump(self,container,user,db,path,globals_only=False):
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        args=['docker','exec',container,'pg_dumpall' if globals_only else 'pg_dump','-U',user]
        args+=['--roles-only'] if globals_only else ['-Fc','-d',db]
        with os.fdopen(fd,'wb') as target:command(args,stdout=target,timeout=300)
        if not globals_only:
            with plain(path).open('rb') as source:command(['docker','exec','-i',container,'pg_restore','--list'],stdin=source,timeout=60)
        return {'sha256':sha(path),'bytes':path.stat().st_size}

    def archive(self,role,path):
        volume_restore_guard(role);self.owned()
        name=self.project+'_'+role
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as target:
            command(['docker','run','--rm','--pull','never','--network','none','--label',
                owner['OWNER_LABEL']+'='+self.state['owner_token'],'--cpus','1','--memory','512m','--mount',
                f'type=volume,source={name},target=/payload,readonly','--entrypoint','tar',owner['PYTHON_IMAGE'],
                '-C','/payload','-cf','-','.'],stdout=target,timeout=300)
        inspect_tar(path);return {'sha256':sha(path),'bytes':path.stat().st_size}

    def publication_current(self):
        folder=self.evidence.parent/('normal-restore-publication-current-'+self.state['owner_token'])
        if not folder.exists():folder.mkdir(mode=0o700)
        plain(folder,True)
        key=folder/'key';ledger=folder/'journal.jsonl'
        if not key.exists():
            fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as target:target.write(os.urandom(32));target.flush();os.fsync(target.fileno())
        info=owner['docker_inspect']('container',self.nc)
        self.owned();collected=publication['collect'](ledger,key,info['Id'],self.project);self.owned()
        current=publication['verify'](ledger,key)
        require(current==collected and current['complete_to_head'] is True,'publication_current_head_incomplete')
        pin=current['record_sha256']
        anchor=publication['checkpoint_anchor'](ledger,key,pin,[self.directory,self.evidence])
        return {'directory':str(folder),'key':str(key),'ledger':str(ledger),'record_sha256':pin,'metadata':current,'anchor':anchor}

    def original_inventory(self,current):
        anchor=current['anchor']
        args=tuple(value for field,flag in [('instance_id','instance-id'),('stream_id','stream-id'),
            ('record_sha256','checkpoint-record-sha256'),('sequence','checkpoint-sequence'),
            ('database_chain_sha256','checkpoint-database-chain-sha256')]
            for value in ('-'+flag,str(anchor[field])))
        captured=self.publication_cli('capture',args)
        path=Path(current['directory'])/'original-inventory.json';write_json(path,captured)
        binding=gate['inventory_binding'](anchor,path,sha(path),[self.evidence,self.directory])
        pairs=binding['pairs'];require(len(pairs)==1 and pairs[0]['operation_id']==self.runtime['operation_id'] and
            pairs[0]['datasource_id']==self.runtime['source_id'] and pairs[0]['knowledge_base_id']==self.runtime['knowledge_base_id'],
            'actual_original_inventory_fixture_scope_drift')
        return {'path':str(path),'sha256':sha(path),'binding':binding}

    def checkpoint(self,baseline_history):
        require(not (self.evidence/'checkpoint.json').exists(),'checkpoint_exists')
        require(baseline_history is not None,'existing_actual_alice_baseline_history_required')
        baseline=json.loads(plain(baseline_history).read_text())
        receipt=self.history_receipt(baseline=baseline)
        self.idle();identity=self.identity();source=self.source_scope();anchors=self.anchor_volume_facts()
        publication_current=self.publication_current()
        self.stop_actors();self.packaged_tools();self.body_cli('verify')
        inventory=self.original_inventory(publication_current)
        files={}
        for role,container,user,database in [('nc',self.nc_db,'nextcloud','nextcloud'),('wk',self.wk_db,'weknora','weknora')]:
            files[role+'.dump']=self.dump(container,user,database,self.evidence/(role+'.dump'))
            files[role+'-roles.sql']=self.dump(container,user,database,self.evidence/(role+'-roles.sql'),True)
        # Logical dumps are the actual restoration input. DBs/Redis are cold
        # during storage archives; body volumes stay present and unmodified.
        self.compose('stop','nc-db','wk-db','nc-redis','wk-redis')
        for role in sorted(DATA_VOLUMES):files[role+'.tar']=self.archive(role,self.evidence/(role+'.tar'))
        for name in CONTROL_FILES:
            destination=self.evidence/('control-'+name)
            legacy['copy_private'](self.directory/name,destination)
            files[destination.name]={'sha256':sha(destination),'bytes':destination.stat().st_size}
        manifest={'schema_version':1,'status':'COMPLETE','project':self.project,
            'owner_token_sha256':hashlib.sha256(self.state['owner_token'].encode()).hexdigest(),
            'candidate':self.provenance,'identity':identity,'source_scope':source,'saved_history':receipt,'artifacts':files,
            'original_inventory':inventory,'external_recovery_anchor':publication_current['anchor'],
            'weknora_recovery_inventory':inventory['binding'],'all_services_stopped_at_completion':True,
            'body_anchor_volumes_current':anchors,'publication_anchor_current':publication_current,
            'all_apps_readers_builders_stopped':True,'pending_auto_restore_accepted':False}
        write_json(self.evidence/'checkpoint.json',manifest)
        write_json(Path(publication_current['directory'])/'checkpoint-pin.json',
            {'project':self.project,'checkpoint_sha256':sha(self.evidence/'checkpoint.json'),'candidate':self.provenance})
        self.record('actual_full_logical_and_storage_checkpoint',completed=True)

    def load_checkpoint(self):
        checkpoint=json.loads(plain(self.evidence/'checkpoint.json').read_text())
        require(checkpoint.get('status')=='COMPLETE' and checkpoint.get('project')==self.project and
            checkpoint.get('candidate')==self.provenance,'checkpoint_owner_candidate_changed')
        for name,entry in checkpoint['artifacts'].items():
            require(Path(name).name==name and HASH.fullmatch(entry['sha256']) and
                sha(plain(self.evidence/name))==entry['sha256'],'checkpoint_artifact_changed')
        require(checkpoint['body_anchor_volumes_current']==self.anchor_volume_facts(),'body_anchor_volume_generation_changed')
        current=checkpoint['publication_anchor_current'];plain(Path(current['directory']),True)
        pin=json.loads(plain(Path(current['directory'])/'checkpoint-pin.json').read_text())
        require(pin=={'project':self.project,'checkpoint_sha256':sha(self.evidence/'checkpoint.json'),'candidate':self.provenance},
            'independent_checkpoint_pin_changed')
        inventory=checkpoint['original_inventory'];plain(Path(inventory['path']))
        require(sha(Path(inventory['path']))==inventory['sha256'] and
            gate['inventory_binding'](current['anchor'],Path(inventory['path']),inventory['sha256'],[self.evidence,self.directory])==inventory['binding'],
            'independent_original_inventory_changed')
        return checkpoint

    def resume_checkpoint_for_fault(self):
        self.load_checkpoint()
        self.compose('start','nc-db','wk-db','nc-redis','wk-redis')
        self.databases_ready()
        self.body_cli('verify')
        self.compose('start',*self.actors())
        self.health()
        self.record('actors_reopened_for_actual_fault_only')

    def health(self):
        for target in (self.nc_base+'/status.php',self.wk_base+'/health'):
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                try:
                    with urllib.request.urlopen(target,timeout=2) as response:
                        if response.status==200:break
                except OSError:pass
                time.sleep(.5)
            else:raise RuntimeError('restored_health_timeout')

    def fault(self,stale=False):
        # A real unpublished file + real personal session exercise both data
        # systems without revoking the original source needed for clean replay.
        self.resume_checkpoint_for_fault()
        basic=base64.b64encode(('devadmin:'+self.passwords['nc_admin']).encode()).decode()
        path=self.nc_base+'/remote.php/dav/files/devadmin/restore-fault-'+uuid.uuid4().hex+'.txt'
        code,_=request(urllib.request.build_opener(),path,'PUT',{'Authorization':'Basic '+basic},b'owned unpublished restore fault bytes')
        require(code==201,'actual_nc_fault_file_creation_failed')
        code,created=e2e['http_json'](self.wk_base,'POST','/api/v1/sessions',{},token=self.token())
        require(code==201 and isinstance(created.get('data',{}).get('id'),str),'actual_wk_fault_session_creation_failed')
        if stale:
            admin,csrf=login(self.nc_base,'devadmin',self.passwords['nc_admin'])
            url=self.nc_base+'/index.php/apps/integration_weknora/api/v1/admin/bindings/synthetic-published/files/'+str(self.runtime['file_id'])+'/withdraw'
            code,_=request(admin,url,'POST',{'requesttoken':csrf,'Content-Type':'application/json'},b'{}')
            require(code==200,'actual_explicit_published_source_withdraw_failed')
        current=self.publication_current();self.idle()
        checkpoint=self.load_checkpoint()
        if not stale:require(self.source_scope()==checkpoint['source_scope'],'fault_changed_original_source_scope')
        self.stop_actors()
        fault={'source_unchanged':not stale,'mode':'stale_publication' if stale else 'clean','file_path':path,'file_path_sha256':hashlib.sha256(path.encode()).hexdigest(),
            'wk_session_id':created['data']['id'],'publication_current':current,'body_anchors_retained':self.anchor_volume_facts()}
        write_json(self.evidence/'actual-fault.json',fault);self.record('actual_unpublished_file_and_personal_session_fault')

    def stale_fault(self):self.fault(True)

    def restore_data(self):
        checkpoint=self.load_checkpoint();fault=json.loads(plain(self.evidence/'actual-fault.json').read_text())
        require(fault.get('mode') in ('clean','stale_publication') and fault['body_anchors_retained']==checkpoint['body_anchor_volumes_current'],
            'actual_fault_scope_or_current_anchor_changed')
        for service in self.actors():self.stopped(service)
        self.no_external_workers();self.compose('stop','nc-db','wk-db','nc-redis','wk-redis')
        for role in sorted(DATA_VOLUMES):
            volume_restore_guard(role);source=plain(self.evidence/(role+'.tar'));inspect_tar(source)
            # The entire mount is one exact owned role, never an anchor. No
            # shell expansion accepts an arbitrary host path or volume name.
            script='set -eu; find /payload -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar -C /payload -xf -'
            self.owned()
            with source.open('rb') as stream:command(['docker','run','--rm','-i','--pull','never','--network','none',
                '--label',owner['OWNER_LABEL']+'='+self.state['owner_token'],'--cpus','1','--memory','512m','--mount',
                f'type=volume,source={self.project}_{role},target=/payload,volume-nocopy','--entrypoint','sh',
                owner['PYTHON_IMAGE'],'-c',script],stdin=stream,timeout=300)
        self.compose('start','nc-db','wk-db','nc-redis','wk-redis')
        self.databases_ready()
        for role,container,user,db in [('nc',self.nc_db,'nextcloud','nextcloud'),('wk',self.wk_db,'weknora','weknora')]:
            # Keep the same pinned cluster roles; prove they were not rolled
            # back/changed before executing actual full database pg_restore.
            role_current=self.evidence/(role+'-roles-current.sql')
            self.dump(container,user,db,role_current,True)
            require(sha(role_current)==checkpoint['artifacts'][role+'-roles.sql']['sha256'],'cluster_roles_changed')
            with plain(self.evidence/(role+'.dump')).open('rb') as source:
                command(['docker','exec','-i',container,'pg_restore','-U',user,'-d',db,
                         '--clean','--if-exists','--exit-on-error'],stdin=source,timeout=600)
            self.record('actual_'+role+'_pg_restore',terminal=0)
        # Queue restoration is deliberately empty: old Redis messages are not
        # original authority, and SQL153 intents survive the actual DB restore.
        command(['docker','exec',self.project+'-wk-redis-1','redis-cli','FLUSHALL'],timeout=30)
        fault_session=str(uuid.UUID(fault['wk_session_id']))
        require(self.sql(f"SELECT count(*) FROM sessions WHERE id='{fault_session}'")==0,
                'post_checkpoint_personal_session_survived_actual_restore')
        require(self.anchor_volume_facts()==checkpoint['body_anchor_volumes_current'],'current_anchors_replaced')
        write_json(self.evidence/'actual-data-restore.json',{'checkpoint_sha256':sha(self.evidence/'checkpoint.json'),
            'actual_dual_full_pg_restore':True,'actual_original_data_volumes_restored':sorted(DATA_VOLUMES),
            'fault_session_absent':True,'body_anchor_volumes_current':self.anchor_volume_facts(),'all_actors_closed':True})
        self.record('application_data_restored_apps_closed',redis_queue_policy='owned_empty_sql_authoritative')

    def actual_clean_gates(self,checkpoint,upgrade=False):
        fault=json.loads(plain(self.evidence/'actual-fault.json').read_text())
        require(fault['source_unchanged'] is True and fault['mode']=='clean','stale_source_cannot_use_clean_restore_gate')
        restored=json.loads(plain(self.evidence/'actual-data-restore.json').read_text())
        require(restored['checkpoint_sha256']==sha(self.evidence/'checkpoint.json') and restored['actual_dual_full_pg_restore'] is True,
                'actual_dual_restore_boundary_missing')
        self.no_external_workers()
        for service in self.actors():self.stopped(service)
        self.packaged_tools()
        if upgrade:self.body_cli('upgrade','body_owner_explicit_upgrade')
        self.body_cli('reconcile');self.body_cli('verify')
        require(self.identity()==checkpoint['identity'],'restored_source_model_generation_identity_changed')
        pinned_publication(checkpoint)
        # The same capture command independently proves the entire original
        # source inventory still matches the checkpoint after actual restore.
        a=checkpoint['external_recovery_anchor']
        args=tuple(value for field,flag in [('instance_id','instance-id'),('stream_id','stream-id'),
            ('record_sha256','checkpoint-record-sha256'),('sequence','checkpoint-sequence'),
            ('database_chain_sha256','checkpoint-database-chain-sha256')]
            for value in ('-'+flag,str(a[field])))
        live=self.publication_cli('capture',args)
        expected=json.loads(plain(Path(checkpoint['original_inventory']['path'])).read_text())
        require(live==expected,'restored_full_original_source_inventory_changed')

    def verify_clean_closed(self,upgrade=False):
        checkpoint=self.load_checkpoint()
        self.actual_clean_gates(checkpoint,upgrade)
        write_json(self.evidence/'closed-clean-gate.json',{'candidate':self.provenance,'checkpoint_sha256':sha(self.evidence/'checkpoint.json'),'actual_body_cli':True,
            'source_generation_unchanged':True,'original_scope_pinned':True,'stale_publication_restore_accepted':False})
        self.record('clean_original_closed_gates_passed')

    def reopen_and_accept(self):
        checkpoint=self.load_checkpoint()
        closed=json.loads(plain(self.evidence/'closed-clean-gate.json').read_text())
        require(closed.get('candidate')==self.provenance and closed.get('checkpoint_sha256')==sha(self.evidence/'checkpoint.json'),
                'actual_closed_gate_missing_or_changed')
        # A persisted receipt never substitutes for live maintenance checks.
        self.actual_clean_gates(checkpoint)
        self.compose('start','nextcloud')
        self.stopped('wk-app')
        # Actual current source authorization, binding/pair and LDAP source
        # checks occur with WK app still stopped, never from a prepared flag.
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try:
                decision=legacy['source_decision'](self.nc_base,self.state,self.runtime)
                if decision.get('allow'):break
            except (OSError,RuntimeError):pass
            time.sleep(.5)
        else:raise RuntimeError('actual_restored_source_authority_not_live')
        require(self.identity()==checkpoint['identity'] and self.source_scope(check_wk=False)==checkpoint['source_scope'],
                'source_identity_or_current_personal_authority_drift_before_app_open')
        fault=json.loads(plain(self.evidence/'actual-fault.json').read_text())
        basic=base64.b64encode(('devadmin:'+self.passwords['nc_admin']).encode()).decode()
        code,_=request(urllib.request.build_opener(),fault['file_path'],'PROPFIND',{'Authorization':'Basic '+basic,'Depth':'0'})
        require(code==404,'post_checkpoint_owner_file_survived_actual_storage_restore')
        self.compose('start','mock-embedding','docreader','wk-app',*(['wk-ui'] if self.state.get('weknora_ui_image') else []))
        self.health();original=self.history_receipt(original=checkpoint['saved_history'])
        first=self.history_receipt(create=True)
        qa['source_context_unchanged'](self.state,self.nc_base,self.passwords,self.runtime)
        write_json(self.evidence/'actual-restored-positive.json',{'original':original,'first_post_restore_qa':first,
            'original_source_authority':True,'current_ldap_actor':True,'runtime_acceptance':True})
        self.record('actual_original_history_replay_and_first_post_restore_qa',original_ids=True,first_qa=True)

    def revoke_and_accept(self):
        checkpoint=self.load_checkpoint();positive=json.loads(plain(self.evidence/'actual-restored-positive.json').read_text())
        qa['revoke_source_share'](self.nc_base,self.passwords,self.runtime['share_id'])
        token=self.token();human=qa['citation_url'](self.project,self.runtime['knowledge_id'],self.runtime['file_id'],self.nc_base)
        for receipt in (checkpoint['saved_history'],positive['first_post_restore_qa']):
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                code,body=e2e['http_json'](self.wk_base,'GET','/api/v1/messages/'+receipt['session_id']+'/load?limit=20',token=token)
                raw=json.dumps(body)
                if code in (200,403,404) and qa['MARKER'] not in raw and human not in raw:break
                time.sleep(.5)
            else:raise RuntimeError('source_revoked_original_history_exposed')
            for message in receipt['message_ids']:
                code,raw=history['replay'](self.wk_base,receipt['session_id'],message,token)
                require(code in (200,403,404) and qa['MARKER'].encode() not in raw and human.encode() not in raw,'source_revoked_native_original_replay_exposed')
        code,raw,events=history['rejected_generation'](self.wk_base,positive['first_post_restore_qa']['session_id'],token,self.runtime)
        require(qa['MARKER'].encode() not in raw and human.encode() not in raw and
            (code in (403,404) or code==200 and any(e.get('response_type')=='error' and e.get('done') is True for e in events)),
            'source_revoked_new_qa_not_terminal_safe')
        qa['source_context_unchanged'](self.state,self.nc_base,self.passwords,self.runtime)
        write_json(self.evidence/'actual-source-only-denial.json',{'original_and_post_restore_history_redacted':True,
            'native_replays_redacted':True,'new_qa_terminal_safe':True,'identity_and_kb_grants_retained':True,
            'pending_auto_restore_accepted':False,'stale_publication_restore_accepted':False})
        self.record('actual_source_only_revocation_completed')

    def stale_plan(self):
        checkpoint=self.load_checkpoint();fault=json.loads(plain(self.evidence/'actual-fault.json').read_text())
        require(fault['mode']=='stale_publication' and fault['source_unchanged'] is False,'actual_publication_fault_missing')
        for service in self.actors():self.stopped(service)
        current=checkpoint['publication_anchor_current'];live=publication['verify'](Path(current['ledger']),Path(current['key']))
        require(live==fault['publication_current']['metadata'] and int(live['sequence'])>int(current['metadata']['sequence']),
            'independent_current_publication_did_not_advance')
        plan=publication['replay_plan'](Path(current['ledger']),Path(current['key']),current['record_sha256'],live['record_sha256'])
        require(any(x['binding_id']=='synthetic-published' and str(x['file_id'])==str(self.runtime['file_id'])
                    for x in plan['withdraw_files']),'actual_original_source_withdrawal_not_pinned')
        raw=publication['canonical'](plan);key=publication['key_bytes'](Path(current['key']))
        path=Path(current['directory'])/'restore-plan.json'
        write_json(path,{'plan':raw.decode('ascii'),'hmac_sha256':hmac.new(key,raw,hashlib.sha256).hexdigest()})
        verified,digest=gate['plan_verify'](path.read_bytes(),key,checkpoint)
        require(verified==plan,'signed_plan_roundtrip_changed')
        write_json(self.evidence/'actual-stale-plan.json',{'path':str(path),'sha256':sha(path),'plan_sha256':digest,
            'current_record_sha256':live['record_sha256'],'current_sequence':live['sequence'],'accepted':False})
        self.record('actual_independent_signed_publication_plan_pinned',runtime_restore_accepted=False)

    def closed_stale(self,upgrade=False):
        checkpoint=self.load_checkpoint()
        restored=json.loads(plain(self.evidence/'actual-data-restore.json').read_text())
        require(restored['checkpoint_sha256']==sha(self.evidence/'checkpoint.json'),'actual_dual_restore_boundary_missing')
        for service in self.actors():self.stopped(service)
        self.no_external_workers();self.packaged_tools()
        plan_fact=json.loads(plain(self.evidence/'actual-stale-plan.json').read_text())
        raw=plain(Path(plan_fact['path'])).read_bytes();require(sha(Path(plan_fact['path']))==plan_fact['sha256'],'retained_plan_changed')
        current=checkpoint['publication_anchor_current'];key=publication['key_bytes'](Path(current['key']))
        plan,digest=gate['plan_verify'](raw,key,checkpoint)
        live=publication['verify'](Path(current['ledger']),Path(current['key']))
        require(live['record_sha256']==plan_fact['current_record_sha256'] and digest==plan_fact['plan_sha256'],
            'retained_current_plan_or_journal_changed')
        if upgrade:self.body_cli('upgrade','body_owner_explicit_upgrade')
        self.body_cli('reconcile');self.body_cli('verify')
        self.nc_offline('maintenance:mode','--on')
        nc_receipt=self.nc_recovery(raw,key);require(self.nc_recovery(raw,key)==nc_receipt,'actual_nc_replay_not_idempotent')
        inventory=checkpoint['original_inventory'];files={'plan':raw,'key':key,'inventory':plain(Path(inventory['path'])).read_bytes()}
        args=('-inventory-sha256',inventory['sha256'])
        receipt=self.publication_cli('apply',args,files)
        require(self.publication_cli('apply',args,files)==receipt,'actual_wk_replay_not_idempotent')
        nc=self.nc_recovery(raw,key,True)
        wk=self.publication_cli('snapshot',('-inventory-sha256',inventory['sha256'],
            '-plan-sha256',digest),{'inventory':files['inventory']})
        evidence=gate['verify_current'](checkpoint,plan,digest,nc,wk,'closed')
        write_json(self.evidence/'actual-nc-closed-state.json',nc);write_json(self.evidence/'actual-wk-closed-state.json',wk)
        write_json(self.evidence/'actual-stale-closed-gate.json',evidence)
        self.body_cli('verify')
        self.record('actual_full_app_two_service_stale_publication_closed',all_ingress_still_closed=True,
            fresh_source_generation_restore_accepted=False,original_history_positive_accepted=False)

    def pending_auto_plan(self):
        write_json(self.evidence/'pending-auto-interface.json',{'accepted':False,'fake_sql_seed_forbidden':True,
            'required_real_boundary':'actual signed ingest, actual broker outage/process exit after completion+sealed SQL intent before delivery',
            'required_artifacts':['exact knowledge/admission/generation/scope','queue and model manifest body heads',
                'immutable payload digest','actual pending/running claim and process/broker terminal','current source/actor/model negative'],
            'restore_sequence':['stop all actors','full pg_dump and files checkpoint','retain CURRENT body/publication anchors',
                'actual full restore with app closed','actual offline body/source gates','owned empty Redis',
                'actual startup SQL recovery under current scope','one tag publication+terminal receipt','withdrawn-source old-task denial']})

    def cleanup(self):
        denial=json.loads(plain(self.evidence/'actual-source-only-denial.json').read_text())
        require(all(denial.get(key) is True for key in ('original_and_post_restore_history_redacted',
            'native_replays_redacted','new_qa_terminal_safe','identity_and_kb_grants_retained')),
            'whole_clean_application_acceptance_incomplete')
        self.owned();self.compose('down','--volumes','--remove-orphans')
        owner['assert_owned_resources'](self.directory,self.state,require_empty=True)
        self.record('exact_owned_cleanup_verified',cache_volumes_preserved=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch',type=Path,required=True)
    parser.add_argument('--candidate-manifest',type=Path,required=True)
    parser.add_argument('--profile',choices=('c6','rag'),required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--phase',choices=('checkpoint','fault','stale-fault','restore-data','closed-clean','closed-stale','reopen','revoke','stale-plan','pending-auto-plan','cleanup'),required=True)
    parser.add_argument('--body-upgrade-authorized',action='store_true',help='only after the body owner explicitly declares packaged upgrade required')
    parser.add_argument('--baseline-history',type=Path,help='checkpoint: owner-only receipt from the actual existing synthetic history probe; history is re-read, not trusted')
    args=parser.parse_args();probe=None
    try:
        probe=Restore(args.scratch,args.candidate_manifest,args.profile,args.evidence)
        if args.phase=='checkpoint':probe.checkpoint(args.baseline_history)
        elif args.phase=='closed-clean':probe.verify_clean_closed(args.body_upgrade_authorized)
        elif args.phase=='closed-stale':probe.closed_stale(args.body_upgrade_authorized)
        else:getattr(probe,{'fault':'fault','restore-data':'restore_data',
            'reopen':'reopen_and_accept','revoke':'revoke_and_accept','stale-plan':'stale_plan',
            'stale-fault':'stale_fault','pending-auto-plan':'pending_auto_plan','cleanup':'cleanup'}[args.phase])()
        print(json.dumps({'project':probe.project,'phase':args.phase,'terminal':0,
            'evidence':str(probe.evidence),'stale_publication_restore_accepted':False,'pending_auto_restore_accepted':False}))
        return 0
    except Exception as error:
        if probe:
            held_closed=False
            # A failed data/source gate cannot leave any reader or builder open.
            # Fault/checkpoint failures also retain files and CURRENT anchors.
            if args.phase!='pending-auto-plan':
                try:probe.stop_actors();held_closed=True
                except Exception:pass
            code=str(error)
            probe.record('phase_failed',error_type=type(error).__name__,
                error_code=code if re.fullmatch(r'[a-z_0-9]+',code) else 'retained_private_context_required',
                requested_phase=args.phase,all_actor_containers_closed=held_closed)
        print('Owned normal restore phase refused: '+type(error).__name__,file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
