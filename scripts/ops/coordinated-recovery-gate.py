#!/usr/bin/env python3
"""Verify pinned two-service closure and current source reconciliation evidence.

This owner-only verifier never starts ingress. Receipts are observations from
the live maintenance CLIs, not substitutes for those CLIs or a personal grant.
"""
import argparse
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

spec=importlib.util.spec_from_file_location('recovery_journal',Path(__file__).with_name('publication-recovery-journal.py'))
journal=importlib.util.module_from_spec(spec);spec.loader.exec_module(journal)
require=journal.require
HASH=re.compile(r'[a-f0-9]{64}')
PAIR_FIELDS={'operation_id','tenant_id','knowledge_base_id','datasource_id','instance_id','binding_id','base_url','config_sha256','publication_epoch','key_id','state'}
ANCHOR_FIELDS={'instance_id','stream_id','record_sha256','sequence','database_chain_sha256'}

def sha(raw):return hashlib.sha256(raw).hexdigest()
def verify_checkpoint_files(manifest,directory):
    require(manifest.get('status')=='COMPLETE' and not (directory/'INCOMPLETE').exists() and not (directory/'INCOMPLETE').is_symlink(),'checkpoint completion marker conflicts')
    artifacts=manifest.get('artifact_sha256')
    require(isinstance(artifacts,dict) and artifacts,'checkpoint artifacts are missing')
    for name,pin in artifacts.items():
        require(isinstance(name,str) and Path(name).name==name and name not in ('.','..','manifest.json'),'unsafe checkpoint artifact name')
        path=directory/name
        journal.plain(path)
        require(HASH.fullmatch(pin or '') is not None and sha(path.read_bytes())==pin,'checkpoint artifact changed')
def read_private(path,pin=None):
    journal.plain(path.parent,True);before=journal.plain(path)
    require(before.st_size<=16<<20,'oversized recovery evidence')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        actual=os.fstat(fd)
        require((actual.st_dev,actual.st_ino,actual.st_size,actual.st_mode,actual.st_nlink)==(before.st_dev,before.st_ino,before.st_size,before.st_mode,before.st_nlink),'recovery evidence changed during open')
        with os.fdopen(fd,'rb',closefd=False) as handle:raw=handle.read((16<<20)+1)
        require(len(raw)==before.st_size,'recovery evidence changed during read')
    finally:os.close(fd)
    if pin is not None:require(HASH.fullmatch(pin or '') is not None and hmac.compare_digest(sha(raw),pin),'external artifact pin mismatch')
    return raw

def inventory_binding(anchor,path,pin,excluded_roots=()):
    raw=read_private(path,pin);inventory=journal.parse(raw)
    require(set(inventory)=={'schema_version','anchor','pairs','source_state_sha256'} and inventory['schema_version']==1,'invalid original source inventory')
    require(set(inventory['anchor'])==ANCHOR_FIELDS and all(inventory['anchor'][k]==anchor[k] for k in ANCHOR_FIELDS),'inventory belongs to another journal checkpoint')
    require(anchor.get('schema_version')==1 and anchor.get('source_head_complete') is True and anchor.get('authoritative_reconciliation_required') is True,'checkpoint has no complete external journal lower bound')
    journal.integer(anchor['sequence'])
    require(HASH.fullmatch(inventory['source_state_sha256'] or '') is not None,'missing source state digest')
    pairs=inventory['pairs'];require(isinstance(pairs,list) and 0<len(pairs)<=10000,'empty or oversized original source inventory')
    operations=set();bindings=set();sources=set();kbscopes=set()
    for pair in pairs:
        require(set(pair)==PAIR_FIELDS and pair['instance_id']==anchor['instance_id'] and pair['state'] in ('active','pending'),'invalid original source identity')
        require(type(pair['tenant_id']) is int and 0<pair['tenant_id']<=2**63-1 and type(pair['publication_epoch']) is int and pair['publication_epoch']>=0,'invalid source epoch or tenant')
        require(all(isinstance(pair[k],str) and pair[k] for k in PAIR_FIELDS-{'tenant_id','publication_epoch'}) and HASH.fullmatch(pair['config_sha256']) is not None,'incomplete original source tuple')
        require(pair['operation_id'] not in operations and pair['binding_id'] not in bindings and pair['datasource_id'] not in sources and (pair['tenant_id'],pair['knowledge_base_id']) not in kbscopes,'ambiguous original source inventory')
        operations.add(pair['operation_id']);bindings.add(pair['binding_id']);sources.add(pair['datasource_id']);kbscopes.add((pair['tenant_id'],pair['knowledge_base_id']))
    require([p['operation_id'] for p in pairs]==sorted(operations),'unordered original inventory')
    for root in excluded_roots:
        root=Path(root).resolve();require(path!=root and root not in path.parents,'source inventory must remain outside restored inputs and checkpoint')
    return {'schema_version':1,'inventory_sha256':pin,'source_state_sha256':inventory['source_state_sha256'],'anchor':inventory['anchor'],'pairs':pairs,'pair_count':len(pairs),'replay_valid_lower_bound':anchor['sequence']}

def plan_verify(raw,key,manifest):
    signed=journal.parse(raw);require(set(signed)=={'plan','hmac_sha256'} and isinstance(signed['plan'],str),'invalid signed plan')
    payload=signed['plan'].encode('ascii');require(hmac.compare_digest(hmac.new(key,payload,hashlib.sha256).hexdigest(),signed['hmac_sha256']),'plan HMAC mismatch')
    plan=journal.parse(payload);anchor=manifest['external_recovery_anchor']
    for field in ('instance_id','stream_id'):require(plan[field]==anchor[field],'plan source identity mismatch')
    for field,target in (('checkpoint_record_sha256','record_sha256'),('checkpoint_sequence','sequence'),('checkpoint_database_chain_sha256','database_chain_sha256')):require(plan[field]==anchor[target],'plan checkpoint mismatch')
    start,end=journal.integer(plan['checkpoint_sequence']),journal.integer(plan['through_sequence'])
    require(start<=end and plan['schema_version']==1 and plan['authoritative_reconciliation_required'] is True and plan['ingress_reopen_permitted'] is False,'plan has invalid replay lower bound')
    prefixes=plan['database_prefix_hashes'];require(set(prefixes)=={str(n) for n in range(start,end+1)} and all(HASH.fullmatch(v or '') for v in prefixes.values()),'incomplete authenticated prefix range')
    require(prefixes[str(start)]==plan['checkpoint_database_chain_sha256'] and prefixes[str(end)]==plan['through_database_chain_sha256'],'plan prefix mismatch')
    return plan,sha(payload)

def verify_current(manifest,plan,plan_sha,nc,wk,phase):
    require(manifest.get('status')=='COMPLETE' and manifest.get('all_services_stopped_at_completion') is True,'checkpoint was incomplete')
    binding=manifest.get('weknora_recovery_inventory');require(binding is not None and binding.get('schema_version')==1,'checkpoint has no pinned original WeKnora inventory')
    anchor=manifest['external_recovery_anchor'];require(binding['anchor']=={k:anchor[k] for k in ANCHOR_FIELDS} and binding['replay_valid_lower_bound']==plan['checkpoint_sequence'],'coordinated lower bound mismatch')
    pairs=binding['pairs'];require(binding['pair_count']==len(pairs),'checkpoint pair count mismatch')
    ncr=nc['receipt'];wkr=wk['receipt']
    require(nc.get('schema_version')==1 and wk.get('schema_version')==1 and nc['instance_id']==plan['instance_id'] and nc['ingress_reopen_permitted'] is False,'invalid current observation')
    for field,ncfield in (('checkpoint_record_sha256','checkpoint_sha256'),('recovery_record_sha256','recovery_sha256'),('stream_id','stream_id')):
        require(ncr[ncfield]==plan[field] and wkr[field]==plan[field],'two-service receipt boundary mismatch')
    require(ncr['plan_sha256']==plan_sha and wkr['plan_sha256']==plan_sha and int(ncr['completed'])==1 and str(ncr['through_sequence'])==plan['through_sequence'] and str(wkr['through_sequence'])==plan['through_sequence'],'two-service plan receipt mismatch')
    require(wkr['instance_id']==plan['instance_id'] and wkr['inventory_sha256']==binding['inventory_sha256'] and wkr['pair_count']==len(pairs),'closure used another original inventory')
    currentpairs={p['operation_id']:p for p in nc['pairs']};sources={s['pair']['operation_id']:s for s in wk['sources']};bindings={b['id']:b for b in nc['bindings']}
    require(len(currentpairs)==len(nc['pairs']) and set(currentpairs)=={p['operation_id'] for p in pairs} and set(sources)==set(currentpairs) and len(sources)==len(wk['sources']),'missing or changed original source scopes')
    withdrawal={(x['binding_id'],str(x['file_id'])) for x in nc['withdrawals'] if x['state']=='withdrawn'}
    require(withdrawal=={(x['binding_id'],x['file_id']) for x in plan['withdraw_files'] if x['binding_id'] in bindings},'current withdrawal mismatch')
    if phase=='closed':require(all(b['publication_state']=='stopped' for b in nc['bindings']),'a restored Nextcloud binding reopened')
    ready=[]
    for p in pairs:
        np=currentpairs[p['operation_id']];s=sources[p['operation_id']]
        require(s['pair']==p,'original source tuple changed')
        for k,nk in (('binding_id','binding_id'),('instance_id','instance_id'),('tenant_id','tenant_id'),('knowledge_base_id','knowledge_base_id'),('datasource_id','data_source_id'),('key_id','key_id'),('state','state'),('publication_epoch','publication_epoch')):require(str(p[k])==str(np[nk]),'remote original pair changed')
        require(p['binding_id'] in bindings,'original Nextcloud binding is missing');b=bindings[p['binding_id']]
        if phase=='closed':
            require(s['scope_state']=='closed' and s['source_status']=='paused' and s['reconciliation'] is None and s['published']==[],'WeKnora restored source is not closed')
            continue
        require(s['scope_state']=='reconciled' and s['source_status']=='active' and b['publication_state']=='active' and b['publication_epoch']>p['publication_epoch'],'source has no current reconciliation')
        r=s['reconciliation'];require(r['plan_sha256']==plan_sha and r['operation_id']==p['operation_id'] and r['pair_sha256']==s['pair_sha256'],'source reconciliation belongs to another closure')
        m=r['manifest'];identity=m['identity']
        require(identity['InstanceID']==p['instance_id'] and identity['BindingID']==p['binding_id'] and identity['BaseURL']==p['base_url'] and identity['PublicationState']=='active' and identity['PublicationEpoch']==b['publication_epoch'],'current source identity mismatch')
        files={str(f['file_id']):f['etag'] for f in m['files']};require(len(files)==len(m['files']) and m['generation'],'incomplete stable manifest')
        require(not any((p['binding_id'],fid) in withdrawal for fid in files),'withdrawn source reappeared')
        published={x['file_id']:x for x in s['published']};require(set(published)==set(files) and len(published)==len(s['published']),'full current manifest has not been ingested')
        retired={x['knowledge_id'] for x in s['retired_generations']}
        for fid,x in published.items():require(x['etag']==files[fid] and x['knowledge_id'] not in retired and x['indexed_chunk_ids'] and len(set(x['indexed_chunk_ids']))==len(x['indexed_chunk_ids']),'current source lacks a new indexed generation')
        ready.append(p['operation_id'])
    return {'schema_version':1,'phase':phase,'plan_sha256':plan_sha,'inventory_sha256':binding['inventory_sha256'],'source_operations':sorted(sources),'reconciled_source_operations':ready,'two_service_replay_verified':True,'ingress_reopen_permitted':False,'index_observation_scope':'relational indexed chunks; external backend and authoritative personal directory checks remain required'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','plan','key-file','nextcloud-state','weknora-state','output'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('manifest','nextcloud-state','weknora-state'):p.add_argument('--'+name+'-sha256',required=True)
    p.add_argument('--phase',choices=('closed','sources'),required=True);a=p.parse_args();os.umask(0o077)
    fd=None
    try:
        journal.plain(a.output.parent,True)
        require(a.output.is_absolute() and a.output.parent.resolve()==a.output.parent,'unsafe output path')
        fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        manifest=journal.parse(read_private(a.manifest,a.manifest_sha256));verify_checkpoint_files(manifest,a.manifest.parent)
        plan,digest=plan_verify(read_private(a.plan),journal.key_bytes(a.key_file),manifest)
        result=verify_current(manifest,plan,digest,journal.parse(read_private(a.nextcloud_state,a.nextcloud_state_sha256)),journal.parse(read_private(a.weknora_state,a.weknora_state_sha256)),a.phase)
        raw=journal.canonical(result);os.write(fd,raw);os.fsync(fd);print('evidence_sha256='+sha(raw));return 0
    except (OSError,ValueError,KeyError,TypeError) as e:
        if fd is not None:os.unlink(a.output)
        print('Coordinated recovery refused: '+str(e),file=sys.stderr);return 1
    finally:
        if fd is not None:os.close(fd)

if __name__=='__main__':sys.exit(main())
