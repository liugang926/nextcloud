#!/usr/bin/env python3
"""Owned live NC + stock PG + maintenance CLI coordinated rollback probe.

Only nonce-labelled fixture resources are touched. Uses real LDAP GUID lookup,
signed NC APIs and actual pg_dump/pg_restore; WK ingestion/indexes are repository
fixtures, so this does not accept the complete deployed application stack.
"""
import argparse
import base64
import hashlib
import contextlib
import io
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import runpy
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
owner=runpy.run_path(str(HERE/'synthetic-ldap-fixture.py'))
e2e=runpy.run_path(str(HERE/'synthetic-ldap-e2e.py'))
sys.path.insert(0,str(ROOT/'apps/integration_weknora/tests'))
from publication_http_smoke import login,request
from source_pairing_http_smoke import file_id
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
journal=module('joint_journal',HERE/'publication-recovery-journal.py');gate=module('joint_gate',HERE/'coordinated-recovery-gate.py')
GO_IMAGE='weknora-go-test@sha256:5a74e8b5137496d3e22c72c01d862c4cbb1fe6ce476ddf5616fd9a807d6ae9ac'
PG_IMAGE=owner['POSTGRES_IMAGE']

def run(*args,input=None,timeout=600):
    r=subprocess.run(args,input=input,capture_output=True,timeout=timeout)
    if r.returncode:
        error=RuntimeError(args[0]+' failed; private fixture command exit '+str(r.returncode))
        error.private_output=r.stdout+r.stderr
        raise error
    return r.stdout
def write(path,v):
    path.write_bytes(v if isinstance(v,bytes) else journal.canonical(v));path.chmod(0o600)
def wait(path,runner,timeout=600):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if path.exists():return
        if run('docker','inspect','--format','{{.State.Running}}',runner).strip()!=b'true':raise RuntimeError('owned Go fixture stopped before '+path.name)
        time.sleep(1)
    raise RuntimeError('owned fixture timed out at '+path.name)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def assert_fixture_owned(state,require_empty=False):
    project,nonce=state['project'],state['owner_token'];resources=owner['project_resources'](project)
    if require_empty:
        assert not any(resources.values());return
    for name in resources['container']:
        item=owner['docker_inspect']('container',name);labels=item['Config']['Labels'];service=labels['com.docker.compose.service']
        assert labels.get(owner['OWNER_LABEL'])==nonce and service in {'cert-init','openldap','nc-db','nc-redis','nextcloud','wk-db'} and name==project+'-'+service+'-1'
    for name in resources['volume']:
        item=owner['docker_inspect']('volume',name);labels=item['Labels'];assert labels.get(owner['OWNER_LABEL'])==nonce and name==project+'_'+labels['com.docker.compose.volume']
    for name in resources['network']:
        item=owner['docker_inspect']('network',name);labels=item['Labels'];assert labels.get(owner['OWNER_LABEL'])==nonce and name in {project+'_default',project+'_httpedge'}
    assert resources['network'] <= {project+'_default',project+'_httpedge'}

def fixture_subnets(occupied):
    """Allocate only unused benchmark-range subnets; never remove other networks."""
    used=[ipaddress.ip_network(value) for value in occupied]
    choices=list(ipaddress.ip_network('198.18.0.0/15').subnets(new_prefix=28))
    secrets.SystemRandom().shuffle(choices)
    selected=[]
    for candidate in choices:
        if any(candidate.version==other.version and candidate.overlaps(other) for other in used):continue
        selected.append(str(candidate));used.append(candidate)
        if len(selected)==2:return selected
    raise RuntimeError('No unused isolated fixture subnets available')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--weknora-source',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--ad-image',default='weknora-auto-space-ad-test:recovery-20261012');a=p.parse_args()
    os.umask(0o077)
    if a.evidence.exists():raise RuntimeError('evidence output must be new')
    # Inspect resource pressure before starting the bounded fixture.
    stats=run('docker','stats','--no-stream','--format','{{.Name}} {{.CPUPerc}} {{.MemUsage}}').decode()
    prepared=io.StringIO()
    with contextlib.redirect_stdout(prepared):owner['prepare']('weknora-ldap-app:nextcloud-rag','nested')
    directory=Path(json.loads(prepared.getvalue())['scratch']).resolve()
    state=json.loads((directory/'state.json').read_text());passwords=json.loads((directory/'passwords.json').read_text());project=state['project'];nonce=state['owner_token'];runner=None
    config=json.loads((directory/'compose.yaml').read_text());allowed={'openldap','nc-db','nc-redis','nextcloud','wk-db'}
    config['services']={k:v for k,v in config['services'].items() if k in allowed};config['services']['wk-db']['image']=PG_IMAGE
    ad_image=json.loads(run('docker','image','inspect',a.ad_image))[0]['Id']
    config['services']['openldap']={
        'image':ad_image,'hostname':'prd-ad','labels':{owner['OWNER_LABEL']:nonce},
        'environment':{'WEKNORA_PRD_TEST_RUNTIME':'1'},
        'volumes':[str(HERE/'fixtures/ldaps')+':/fixtures:ro'],
        'networks':{'default':{'aliases':['prd-ad']}},
        'command':['bash','-c','test ! -e /var/lib/samba/private/sam.ldb && rm -f /etc/samba/smb.conf && python3 /fixtures/provision.py && exec bash /fixtures/start.sh'],
        'healthcheck':{'test':['CMD-SHELL', 'ldapsearch -x -H ldaps://prd-ad -D Administrator@PRD.EXAMPLE.TEST -w SyntheticPrdAdmin2026! -b DC=prd,DC=example,DC=test -s base -o TLS_CACERT=/var/lib/samba/private/tls/ca.pem dn >/dev/null'], 'interval':'5s','timeout':'5s','retries':45}}
    config['services']['nextcloud']['environment']['LDAPTLS_CACERT']='/etc/ssl/certs/ca-certificates.crt'
    config['volumes']={k:v for k,v in config['volumes'].items() if k in {'ldap-certs','ldap-fixture','nc-postgres','nc-redis-data','nc-html','wk-postgres'}}
    config['networks']['default']['internal']=True
    config['networks']['httpedge']={'labels':{owner['OWNER_LABEL']:nonce}}
    network_ids=run('docker','network','ls','-q').decode().split()
    networks=json.loads(run('docker','network','inspect',*network_ids)) if network_ids else []
    occupied=[entry['Subnet'] for network in networks for entry in (network.get('IPAM',{}).get('Config') or []) if entry.get('Subnet')]
    for name,subnet in zip(('default','httpedge'),fixture_subnets(occupied)):
        config['networks'][name]['ipam']={'config':[{'subnet':subnet}]}
    config['services']['nextcloud']['networks']=['default','httpedge']
    for name,s in config['services'].items():
        s['cpus']=1;s['mem_limit']='1g' if name=='nextcloud' else '512m';s.pop('ports',None) if name!='nextcloud' else None
    config['services']['wk-db']['volumes']=[];config['services']['wk-db']['tmpfs']=['/var/lib/postgresql/data:size=256m,mode=700']
    write(directory/'compose.yaml',config);compose=('docker','compose','-p',project,'-f',str(directory/'compose.yaml'));f=directory/'external';f.mkdir(mode=0o700)
    stages=[];ids={}
    try:
        run(*compose,'up','-d','--wait','--wait-timeout','600',timeout=660)
        for name in allowed:
            cid=run(*compose,'ps','-aq',name).decode().strip();item=json.loads(run('docker','inspect',cid))[0]
            assert item['Config']['Labels'][owner['OWNER_LABEL']]==nonce and item['HostConfig']['Memory']<=1024**3 and item['HostConfig']['NanoCpus']<=10**9
            if name!='nextcloud':assert not item['HostConfig'].get('PortBindings')
            ids[name]=cid
        nc=ids['nextcloud'];wkpg=ids['wk-db'];ncpg=ids['nc-db'];base=f"http://127.0.0.1:{state['ports']['nextcloud']}";api=base+'/index.php/apps/integration_weknora/api/v1'
        def ready_login(origin,user,password):
            for attempt in range(30):
                try:return login(origin,user,password)
                except (AssertionError,OSError):
                    if attempt==29:raise
                    time.sleep(2)
        e2e['nextcloud_setup'].__globals__['login']=ready_login
        ad=ids['openldap']
        ca=directory/'samba-ca.crt'
        run('docker','cp',ad+':/var/lib/samba/private/tls/ca.pem',str(ca))
        run('docker','cp',str(ca),nc+':/usr/local/share/ca-certificates/recovery-ad.crt')
        run('docker','exec',nc,'update-ca-certificates')
        passwords['ldap_bind']='SyntheticPrdAdmin2026!'
        for user in ('alice','bob','publisher'):
            passwords[user]='SyntheticRecovery'+user.title()+'2026!'
            run('docker','exec',ad,'samba-tool','user','create',user,passwords[user])
            details=run('docker','exec',ad,'samba-tool','user','show',user).decode()
            import re
            state['guids'][user]=re.search(r'^objectGUID: ([a-fA-F0-9-]+)$',details,re.M).group(1).lower()
        run('docker','exec',ad,'samba-tool','group','add','Engineering')
        run('docker','exec',ad,'samba-tool','group','add','Platform')
        run('docker','exec',ad,'samba-tool','group','addmembers','Platform','alice')
        run('docker','exec',ad,'samba-tool','group','addmembers','Engineering','Platform')
        write(directory/'state.json',state);write(directory/'passwords.json',passwords)
        ldap_settings={'ldapHost':'ldaps://prd-ad','ldapPort':'636','ldapAgentName':'Administrator@PRD.EXAMPLE.TEST',
            'ldapBase':'DC=prd,DC=example,DC=test','ldapBaseUsers':'CN=Users,DC=prd,DC=example,DC=test','ldapBaseGroups':'CN=Users,DC=prd,DC=example,DC=test',
            'ldapUserFilter':'(&(objectClass=user)(!(objectClass=computer)))','ldapLoginFilter':'(&(objectClass=user)(sAMAccountName=%uid))',
            'ldapGroupFilter':'(objectClass=group)','ldapUserDisplayName':'sAMAccountName','ldapGroupDisplayName':'cn','ldapCacheTTL':'0','ldapConnectionTimeout':'5','ldapTLS':'0','turnOffCertCheck':'0'}
        runtime=e2e['nextcloud_setup'](directory,state,passwords,base,False,ldap_settings,publisher='publisher')

        occ=lambda *args:e2e['occ'](state,*args).strip()
        admin,csrf=login(base,'publisher',passwords['publisher']);headers={'requesttoken':csrf,'Content-Type':'application/json'}
        def admin_json(path,method='POST',v=None):
            code,b=request(admin,api+path,method,headers,None if v is None else json.dumps(v).encode());assert code in (200,201),(path,code,b[:250]);return json.loads(b)
        auth={'Authorization':'Basic '+base64.b64encode(('publisher:'+passwords['publisher']).encode()).decode()};dav=urllib.request.build_opener();folder=base+'/remote.php/dav/files/publisher/Published';oldurl=folder+'/acl-note.txt';currenturl=folder+'/current.txt'
        assert request(dav,currenturl,'PUT',auth,b'owned current generation initial bytes')[0]==201
        def etag(url):
            req=urllib.request.Request(url,method='PROPFIND',headers={**auth,'Depth':'0'},data=b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getetag/></d:prop></d:propfind>')
            import xml.etree.ElementTree as ET
            with urllib.request.urlopen(req,timeout=20) as r:return ET.fromstring(r.read()).find('.//{DAV:}getetag').text.strip('"')
        kb,ds,operation=map(str,(uuid.uuid4(),uuid.uuid4(),uuid.uuid4()));binding=runtime['binding_id']
        prepared_pair=admin_json('/admin/bindings/'+binding+'/source-pairing',v={'operation_id':operation,'tenant_id':'7','knowledge_base_id':kb});pair=prepared_pair['pairing'];token=prepared_pair['token']
        machine={'Authorization':'Bearer '+token,'X-WeKnora-Key-Id':pair['key_id'],'Content-Type':'application/json'}
        payload={'operation_id':operation,'instance_id':pair['instance_id'],'tenant_id':'7','knowledge_base_id':kb,'data_source_id':ds}
        code,b=request(dav,api+'/bindings/'+binding+'/source-pairing/commit','POST',machine,json.dumps(payload).encode());assert code==200,(code,b[:200])
        key=f/'key';write(key,secrets.token_bytes(32));log=f/'journal';first=json.loads(occ('integration_weknora:export-recovery-ledger','--after=0'));assert not first['has_more'];anchor_pin=journal.append(log,key,first)
        anchor=journal.checkpoint_anchor(log,key,anchor_pin,[ROOT,a.weknora_source,directory/'checkpoint'])
        files=[{'ID':runtime['file_id'],'ETag':etag(oldurl),'Path':'acl-note.txt'},{'ID':file_id(currenturl,auth),'ETag':etag(currenturl),'Path':'current.txt'}]
        for file in files:
            revision_php="define('OC_CONSOLE',1);require '/var/www/html/lib/base.php';$app=new \\OCA\\IntegrationWeknora\\AppInfo\\Application();echo $app->getContainer()->get(\\OCA\\IntegrationWeknora\\Service\\SourceContentRevisionService::class)->current((int)$argv[1]);"
            file['Revision']=run('docker','exec','-u','www-data',nc,'php','-r',revision_php,str(file['ID'])).decode().strip()
        fixture={'Instance':pair['instance_id'],'Binding':binding,'Operation':operation,'Epoch':pair['publication_epoch'],'KeyID':pair['key_id'],'Token':token,'KB':kb,'DS':ds,'Directory':state['directory_id'],'AliceGUID':state['guids']['alice'],'BobGUID':state['guids']['bob'],'Files':files,'Anchor':{k:anchor[k] for k in gate.ANCHOR_FIELDS}}
        write(f/'fixture.json',fixture)
        runnername=project+'-go';runner=run('docker','run','-d','--name',runnername,'--label',owner['OWNER_LABEL']+'='+nonce,'--cpus','1','--memory','2g','--network',project+'_default','-v',str(a.weknora_source.resolve())+':/app','-v',str(f)+':/fixture','-v','weknora-go-mod:/go/pkg/mod:ro','-v','weknora-go-build:/root/.cache/go-build','-w','/app','-e','WEKNORA_COORDINATED_RECOVERY_FIXTURE=/fixture','-e','WEKNORA_RECOVERY_CLI=/tmp/publication-recovery','-e','WEKNORA_LEASE_TEST_POSTGRES_DSN=host=wk-db user=weknora dbname=weknora password='+passwords['wk_db']+' sslmode=disable',GO_IMAGE,'sh','-c','gofmt -w internal/application/repository/coordinated_recovery_live_test.go internal/application/repository/nextcloud_publication_recovery.go internal/application/repository/nextcloud_publication_recovery_test.go && go build -buildvcs=false -p 1 -o /tmp/publication-recovery ./cmd/publication-recovery && go test -p 1 ./internal/application/repository -run ^TestPostgresCoordinatedRecoveryLiveNextcloud$ -count=1 -v > /fixture/go-result.log 2>&1').decode().strip()
        wait(f/'capture-ready.json',runner);capture=json.loads((f/'capture-ready.json').read_text());invbind=gate.inventory_binding(anchor,f/'inventory.json',capture['inventory_sha256'],[ROOT,a.weknora_source,directory/'checkpoint'])
        # Both genuine database backups precede post-checkpoint withdrawal.
        occ('maintenance:mode','--on');run(*compose,'stop','nextcloud',timeout=120)
        write(f/'nc.dump',run('docker','exec',ncpg,'pg_dump','-U','nextcloud','-d','nextcloud','-Fc'))
        write(f/'wk.dump',run('docker','exec',wkpg,'pg_dump','-U','weknora','-d','weknora','-Fc','--schema='+capture['schema']))
        manifest={'status':'COMPLETE','all_services_stopped_at_completion':True,'scope':'owned_live_nc_and_stock_pg_repository_fixture','external_recovery_anchor':anchor,'weknora_recovery_inventory':invbind,'artifact_sha256':{'nc.dump':sha(f/'nc.dump'),'wk.dump':sha(f/'wk.dump')}};write(f/'manifest.json',manifest);stages.append('two_real_database_backups_with_external_original_inventory_pin')
        run(*compose,'start','nextcloud');occ('maintenance:mode','--off');admin,csrf=login(base,'publisher',passwords['publisher']);headers['requesttoken']=csrf
        write(f/'captured',{})
        wait(f/'tombstone-ready',runner)
        admin_json('/admin/bindings/'+binding+'/files/'+str(files[0]['ID'])+'/withdraw',v={})
        second=json.loads(occ('integration_weknora:export-recovery-ledger','--after='+first['next_sequence']));through=journal.append(log,key,second)
        plan=journal.replay_plan(log,key,anchor_pin,through);raw=journal.canonical(plan);write(f/'plan',json.dumps({'plan':raw.decode('ascii'),'hmac_sha256':__import__('hmac').new(key.read_bytes(),raw,hashlib.sha256).hexdigest()},separators=(',',':')).encode())
        occ('maintenance:mode','--on');run(*compose,'stop','nextcloud',timeout=120)
        run('docker','exec','-i',ncpg,'pg_restore','-U','nextcloud','-d','nextcloud','--clean','--if-exists',input=(f/'nc.dump').read_bytes())
        run('docker','exec','-i',wkpg,'pg_restore','-U','weknora','-d','weknora','--clean','--if-exists',input=(f/'wk.dump').read_bytes())
        run(*compose,'start','nextcloud')
        run('docker','exec',nc,'mkdir','-m','700','/tmp/joint-recovery');run('docker','cp',str(key),nc+':/tmp/joint-recovery/key');run('docker','cp',str(f/'plan'),nc+':/tmp/joint-recovery/plan');run('docker','exec',nc,'chown','-R','www-data:www-data','/tmp/joint-recovery')
        replay=('docker','exec','-u','www-data',nc,'php','custom_apps/integration_weknora/appinfo/recovery-console.php','--plan=/tmp/joint-recovery/plan','--key-file=/tmp/joint-recovery/key')
        receipt=json.loads(run(*replay));repeat=json.loads(run(*replay));assert receipt==repeat
        write(f/'nextcloud-closed-state.json',json.loads(run(*replay,'--inspect-state')));write(f/'restore-ready',{})
        wait(f/'closed-ready',runner)
        _,plan_sha=gate.plan_verify((f/'plan').read_bytes(),key.read_bytes(),manifest)
        gate.verify_checkpoint_files(manifest,f)
        closed=gate.verify_current(manifest,plan,plan_sha,json.loads((f/'nextcloud-closed-state.json').read_text()),json.loads((f/'weknora-closed-state.json').read_text()),'closed');write(f/'closed-gate.json',closed)
        def gate_cli(phase,nc_state,wk_state,out):
            return run(sys.executable,str(HERE/'coordinated-recovery-gate.py'),'--phase='+phase,'--manifest='+str(f/'manifest.json'),'--manifest-sha256='+sha(f/'manifest.json'),'--plan='+str(f/'plan'),'--key-file='+str(key),'--nextcloud-state='+str(f/nc_state),'--nextcloud-state-sha256='+sha(f/nc_state),'--weknora-state='+str(f/wk_state),'--weknora-state-sha256='+sha(f/wk_state),'--output='+str(f/out))
        gate_cli('closed','nextcloud-closed-state.json','weknora-closed-state.json','closed-gate-cli.json')
        import copy
        bad=copy.deepcopy(manifest);bad['weknora_recovery_inventory']['pairs'][0]['datasource_id']='changed-tuple'
        try:gate.verify_current(bad,plan,plan_sha,json.loads((f/'nextcloud-closed-state.json').read_text()),json.loads((f/'weknora-closed-state.json').read_text()),'closed');raise AssertionError('changed tuple accepted')
        except gate.journal.JournalError:pass
        bad=copy.deepcopy(manifest);bad.pop('weknora_recovery_inventory')
        try:gate.verify_current(bad,plan,plan_sha,json.loads((f/'nextcloud-closed-state.json').read_text()),json.loads((f/'weknora-closed-state.json').read_text()),'closed');raise AssertionError('old checkpoint accepted')
        except gate.journal.JournalError:pass
        write(f/'INCOMPLETE',{})
        try:gate.verify_checkpoint_files(manifest,f);raise AssertionError('conflicting marker accepted')
        except gate.journal.JournalError:pass
        (f/'INCOMPLETE').unlink()
        stages.append('real_dual_database_rollback_exact_plan_dual_closed_receipts')
        # All source scopes remain paused until actual current signed pair and
        # double complete NC manifest are reconciled by the maintenance CLI.
        tail_page=json.loads(run(*replay,'--export-after='+plan['through_sequence']))
        assert not tail_page['has_more']
        tail_pin=journal.append(log,key,tail_page)
        tail_plan=journal.replay_plan(log,key,anchor_pin,tail_pin);tail_raw=journal.canonical(tail_plan)
        write(f/'tail-plan',json.dumps({'plan':tail_raw.decode('ascii'),'hmac_sha256':__import__('hmac').new(key.read_bytes(),tail_raw,hashlib.sha256).hexdigest()},separators=(',',':')).encode())
        run('docker','cp',str(f/'tail-plan'),nc+':/tmp/joint-recovery/tail-plan');run('docker','exec',nc,'chown','www-data:www-data','/tmp/joint-recovery/tail-plan')
        tail_replay=tuple('--plan=/tmp/joint-recovery/tail-plan' if arg=='--plan=/tmp/joint-recovery/plan' else arg for arg in replay)
        stages.append('authenticated_journal_restored_without_fork_and_new_closure_tail_retained')
        current_controls=json.loads(run(*replay,'--inspect-state'))['control_state']
        write(f/'nextcloud-control-reconcile.json',json.loads(run(*tail_replay,'--reconcile-controls','--control-plan-sha256='+plan_sha,'--control-inventory-sha256='+current_controls['sha256'],'--actor=publisher')))
        occ('maintenance:mode','--off');admin,csrf=login(base,'publisher',passwords['publisher']);headers['requesttoken']=csrf
        admin_json('/admin/bindings/'+binding+'/resume',v={})
        write(f/'current-file.json',files[1]);write(f/'resumed',{})
        wait(f/'final-ready',runner)
        occ('maintenance:mode','--on');write(f/'nextcloud-final-state.json',json.loads(run(*replay,'--inspect-state')))
        sources=gate.verify_current(manifest,plan,plan_sha,json.loads((f/'nextcloud-final-state.json').read_text()),json.loads((f/'weknora-final-state.json').read_text()),'sources');write(f/'source-gate.json',sources);gate_cli('sources','nextcloud-final-state.json','weknora-final-state.json','source-gate-cli.json');stages.append('live_signed_pair_double_manifest_new_id_indexed_catalog_and_personal_ldap_acl')
        occ('maintenance:mode','--off')
        assert request(dav,currenturl,'PUT',auth,b'owned second source version bytes')[0] in (201,204)
        current_revision=run('docker','exec','-u','www-data',nc,'php','-r',revision_php,str(files[1]['ID'])).decode().strip()
        write(f/'current-revision.json',{'revision':current_revision})
        write(f/'etag-changed',{});wait(f/'etag-denied',runner)
        # Revoke the actual nested department share. Direct admin DAV remains.
        admin,csrf=login(base,'publisher',passwords['publisher']);code,b=request(admin,base+'/ocs/v2.php/apps/files_sharing/api/v1/shares/'+str(runtime['share_id']),'DELETE',{'requesttoken':csrf,'OCS-APIRequest':'true','Accept':'application/json'});assert code==200
        write(f/'acl-revoked',{});wait(f/'complete.json',runner)
        assert request(dav,oldurl,headers=auth)[0]==200
        go_exit=int(run('docker','wait',runner,timeout=30).decode().strip());assert go_exit==0
        go_log=(f/'go-result.log').read_text()
        for secret in [*passwords.values(),token]:go_log=go_log.replace(secret,'[redacted]')
        write(a.evidence.with_suffix('.go.log'),go_log.encode())
        result={'schema_version':1,'status':'PASS','go_exit_code':go_exit,'go_log_sha256':sha(a.evidence.with_suffix('.go.log')),'stages':stages,'tests':json.loads((f/'complete.json').read_text()),'owned_container_ids':ids,'go_container_id':runner,'source_commits':{'nextcloud':run('git','-C',str(ROOT),'rev-parse','HEAD').decode().strip(),'weknora':run('git','-C',str(a.weknora_source),'rev-parse','HEAD').decode().strip()},'checkpoint_manifest':manifest,'closed_gate':closed,'source_gate':sources,'observation_sha256':{name:sha(f/name) for name in ('nextcloud-closed-state.json','weknora-closed-state.json','nextcloud-final-state.json','weknora-final-state.json')},'shared_target_modified':False,'ingress_reopen_accepted':False,'runtime_boundary':'real live Nextcloud + disposable Samba AD LDAPS; WK actual CLI/repository on stock PG, mock relational indexed chunks; full app and external index backend acceptance remains required'}
        write(a.evidence,result)
        print(json.dumps({'status':'PASS','evidence':str(a.evidence),'ingress_reopen_accepted':False}))
    except BaseException as failure:
        if hasattr(failure, 'private_output'):
            details=failure.private_output.decode(errors='replace')
            for secret in passwords.values():details=details.replace(secret,'[redacted]')
            write(directory/'orchestration-failure.log',details.encode())
        for service in ('nextcloud','openldap'):
            if service not in ids:
                probe=subprocess.run([*compose,'ps','-aq',service],capture_output=True,timeout=15)
                cid=probe.stdout.decode().strip()
                if cid:
                    item=json.loads(run('docker','inspect',cid))[0]
                    if item['Config']['Labels'].get(owner['OWNER_LABEL'])==nonce:ids[service]=cid
        if runner:ids['go']=runner
        for name in ('nextcloud','openldap','go'):
            if name in ids:
                logged=subprocess.run(['docker','logs','--tail','80',ids[name]],capture_output=True,timeout=20)
                details=(logged.stdout+logged.stderr).decode(errors='replace')
                for secret in passwords.values():details=details.replace(secret,'[redacted]')
                write(directory/(name+'-failure.log'),details.encode())
        print('Private failed-fixture evidence: '+str(directory),file=sys.stderr)
        raise
    finally:
        if runner:
            item=json.loads(run('docker','inspect',runner))[0];assert item['Config']['Labels'][owner['OWNER_LABEL']]==nonce;run('docker','rm','-f',runner)
        assert_fixture_owned(state)
        run(*compose,'down','--volumes','--remove-orphans',timeout=180)
        assert_fixture_owned(state,require_empty=True)
        if a.evidence.exists():result=json.loads(a.evidence.read_text());result['cleanup_verified']=True;write(a.evidence,result)
        # Retain private failed fixture logs for diagnosis; successful secrets
        # and owned DB artifacts are removed after the sanitized evidence write.
        if a.evidence.exists():shutil.rmtree(directory)

if __name__=='__main__':main()
