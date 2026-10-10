#!/usr/bin/env python3
"""Real packaged Nextcloud journal export, external retention and closed replay."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import time
import sys
import urllib.request
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
fresh=module('fresh',HERE/'isolated-fresh-install-smoke.py')
journal=module('journal',HERE/'publication-recovery-journal.py')
sys.path.insert(0,str(ROOT/'apps/integration_weknora/tests'))
from publication_http_smoke import login,request

def probe(compose,occ,container,port,user,password,tmp):
    fresh.run(*compose,'restart','nextcloud',timeout=120)
    fresh.run(*compose,'up','-d','--wait','--wait-timeout','180','nextcloud',timeout=240)
    base=f'http://127.0.0.1:{port}'
    api=base+'/index.php/apps/integration_weknora/api/v1'
    auth='Basic '+base64.b64encode(f'{user}:{password}'.encode()).decode()
    root_url=base+'/remote.php/dav/files/'+user+'/Recovery/'
    def dav(url,method,data=None):
        req=urllib.request.Request(url,data=data,method=method,headers={'Authorization':auth,'Depth':'0'})
        with urllib.request.urlopen(req,timeout=20) as reply:return reply.status,reply.read()
    assert dav(root_url,'MKCOL')[0]==201
    _,xml=dav(root_url,'PROPFIND',b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop><oc:fileid/></d:prop></d:propfind>')
    root_id=int(ET.fromstring(xml).find('.//{http://owncloud.org/ns}fileid').text)
    admin,csrf=login(base,user,password)
    for _ in range(30):
        ready,detail=request(admin,api+'/admin/bindings',headers={'requesttoken':csrf})
        if ready==200:break
        time.sleep(1)
    else:raise AssertionError('packaged app admin route was not ready: '+str(ready))
    status,body=request(admin,api+'/admin/bindings','POST',{'requesttoken':csrf,'Content-Type':'application/json'},
                        json.dumps({'id':'recovery-test','name':'Recovery','owner_uid':user,'root_file_id':root_id}).encode())
    assert status==201,(status,body[:300])
    file_url=root_url+'synthetic.txt';assert dav(file_url,'PUT',b'isolated journal source bytes')[0]==201
    _,xml=dav(file_url,'PROPFIND',b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop><oc:fileid/></d:prop></d:propfind>')
    file_id=int(ET.fromstring(xml).find('.//{http://owncloud.org/ns}fileid').text)
    private=tmp.resolve()/'external-journal';private.mkdir(mode=0o700)
    key=private/'key';key.write_bytes(secrets.token_bytes(32));key.chmod(0o600)
    log=private/'journal'
    first=json.loads(occ('integration_weknora:export-recovery-ledger','--after=0'))
    assert not first['has_more']
    anchor=journal.append(log,key,first)
    target=api+f'/admin/bindings/recovery-test/files/{file_id}/withdraw'
    status,body=request(admin,target,'POST',{'requesttoken':csrf},b'');assert status==200,(status,body[:300])
    second=json.loads(occ('integration_weknora:export-recovery-ledger','--after='+first['next_sequence']))
    assert any(x['kind']=='withdrawn' and x['file_id']==str(file_id) for x in second['items'])
    through=journal.append(log,key,second)
    # Replay a backup-era state. Only this fixture DB is touched; originals/DAV
    # remain intact, and no shared service or production data is involved.
    sql=(f"UPDATE oc_weknora_pub_state SET state='eligible',decision_audit_id=0 WHERE binding_id='recovery-test' AND file_id={file_id}; "
         f"DELETE FROM oc_weknora_recovery_log WHERE sequence>{int(first['next_sequence'])}; "
         f"UPDATE oc_weknora_recovery_head SET sequence={int(first['next_sequence'])},chain_sha256='{first['next_chain_sha256']}';")
    fresh.run(*compose,'exec','-T','db','psql','-U','nextcloud','-d','nextcloud','-v','ON_ERROR_STOP=1','-c',sql)
    plan=journal.replay_plan(log,key,anchor,through)
    payload=journal.canonical(plan);envelope={'plan':payload.decode('ascii'),'hmac_sha256':__import__('hmac').new(key.read_bytes(),payload,hashlib.sha256).hexdigest()}
    plan_file=private/'plan.json';plan_file.write_text(json.dumps(envelope,separators=(',',':')));plan_file.chmod(0o600)
    fresh.run(*compose,'exec','-T','nextcloud','mkdir','-m','700','/tmp/recovery')
    fresh.run(*compose,'exec','-T','nextcloud','chown','www-data:www-data','/tmp/recovery')
    fresh.run('docker','cp',str(key),container+':/tmp/recovery/key')
    fresh.run('docker','cp',str(plan_file),container+':/tmp/recovery/plan')
    fresh.run(*compose,'exec','-T','nextcloud','chown','www-data:www-data','/tmp/recovery/key','/tmp/recovery/plan')
    occ('maintenance:mode','--on')
    result=json.loads(fresh.run(*compose,'exec','-T','-u','www-data','nextcloud','php','custom_apps/integration_weknora/appinfo/recovery-console.php','--plan=/tmp/recovery/plan','--key-file=/tmp/recovery/key'))
    assert result['closed_bindings']==1 and result['withdrawn_files']==1 and result['ingress_reopen_permitted'] is False
    repeated=json.loads(fresh.run(*compose,'exec','-T','-u','www-data','nextcloud','php','custom_apps/integration_weknora/appinfo/recovery-console.php','--plan=/tmp/recovery/plan','--key-file=/tmp/recovery/key'))
    assert repeated['ingress_reopen_permitted'] is False
    observed=fresh.run(*compose,'exec','-T','db','psql','-U','nextcloud','-d','nextcloud','-At','-c',
                       f"SELECT publication_state FROM oc_weknora_binding_id WHERE binding_id='recovery-test'; SELECT state FROM oc_weknora_pub_state WHERE binding_id='recovery-test' AND file_id={file_id};")
    assert observed.splitlines()==['stopped','withdrawn'],observed
    # Idempotent replay must remain closed; new local journal rows are not an
    # external through-boundary, so a retry uses recorded application receipts.
    occ('maintenance:mode','--off')
    status,_=dav(file_url,'GET');assert status==200
    print(json.dumps({'journal_export_contiguous':True,'external_record_hmac_verified':True,
          'simulated_backup_rollback':True,'withdrawal_reapplied':True,'binding_stopped':True,
          'original_dav_readable':True,'ingress_reopen_accepted':False,'shared_target_modified':False},sort_keys=True))

if __name__=='__main__':fresh.main(runtime_probe=probe)
