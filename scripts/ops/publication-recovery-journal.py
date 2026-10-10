#!/usr/bin/env python3
"""Retain continuous occ recovery pages outside database/volume checkpoints.

The signing key is local and private; no password or document body is stored.
A checkpoint must pin an independently retained journal record before shutdown.
Replay plans only close publication and never authorize reopening.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

KINDS = {'upsert', 'metadata', 'delete', 'subtree_scan', 'subtree_moved',
         'subtree_deleted', 'reconcile', 'withdrawn', 'eligible', 'binding_stop',
         'binding_resume', 'binding_retired', 'binding_created'}
PAGE_KEYS = {'schema_version','instance_id','stream_id','after_sequence',
             'next_sequence','head_sequence','after_chain_sha256','next_chain_sha256','head_chain_sha256','items','has_more'}
ITEM_KEYS = {'sequence','binding_id','file_id','kind','source_revision','created_at'}
ZERO = '0' * 64
MAX_PAGE = 256 * 1024

class JournalError(ValueError): pass

def require(ok, message):
    if not ok: raise JournalError(message)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()

def integer(value):
    require(isinstance(value,str) and re.fullmatch(r'0|[1-9][0-9]{0,18}',value) is not None,
            'invalid decimal sequence')
    result=int(value);require(result <= 2**63-1,'sequence overflow');return result

def event_hash(previous,item):
    values=[previous,item['sequence'],item['binding_id'],item['file_id'] or '-',item['kind'],item['source_revision'],str(item['created_at'])]
    return hashlib.sha256('\n'.join(values).encode('ascii')).hexdigest()

def page(value):
    require(isinstance(value,dict) and set(value)==PAGE_KEYS and type(value['schema_version']) is int and value['schema_version']==1,
            'invalid journal page')
    require(isinstance(value['instance_id'],str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value['instance_id']) is not None,'invalid instance')
    require(isinstance(value['stream_id'],str) and re.fullmatch(r'[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}',value['stream_id']) is not None,'invalid stream')
    after,nxt,head=map(integer,(value['after_sequence'],value['next_sequence'],value['head_sequence']))
    require(after <= nxt <= head and type(value['has_more']) is bool and value['has_more']==(nxt<head), 'inconsistent page head')
    require(isinstance(value['items'],list) and len(value['items'])<=200 and len(value['items'])==nxt-after,
            'missing journal sequence')
    for field in ('after_chain_sha256','next_chain_sha256','head_chain_sha256'):
        require(isinstance(value[field],str) and re.fullmatch(r'[a-f0-9]{64}',value[field]) is not None,'invalid database chain hash')
    require(after!=0 or value['after_chain_sha256']==ZERO,'invalid initial database prefix')
    prefix=value['after_chain_sha256']
    for offset,item in enumerate(value['items'],after+1):
        require(isinstance(item,dict) and set(item)==ITEM_KEYS,'invalid journal item')
        require(integer(item['sequence'])==offset,'noncontiguous journal item')
        require(isinstance(item['binding_id'],str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',item['binding_id']) is not None,'invalid binding')
        require(item['file_id'] is None or integer(item['file_id'])>0,'invalid file identity')
        require(item['kind'] in KINDS,'unknown journal kind')
        integer(item['source_revision'])
        require(type(item['created_at']) is int and 0 <= item['created_at'] <= 2**63-1,'invalid item time')
        prefix=event_hash(prefix,item)
    require(prefix==value['next_chain_sha256'],'page database chain mismatch')
    require(nxt!=head or prefix==value['head_chain_sha256'],'terminal database chain mismatch')
    require(nxt==head or len(value['items'])==200,'truncated nonterminal page')
    return value

def plain(path:Path, directory=False):
    require(path.is_absolute() and path.resolve(strict=True)==path,'path must be absolute without symlink ancestors')
    info=path.lstat()
    require(info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)==(0o700 if directory else 0o600), 'unsafe private path mode/owner')
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink==1,'unsafe path type/link')
    return info

def key_bytes(path):
    plain(path.parent,True);before=plain(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        actual=os.fstat(fd)
        require(stat.S_ISREG(actual.st_mode) and actual.st_uid==os.getuid() and stat.S_IMODE(actual.st_mode)==0o600 and actual.st_nlink==1,'opened signing key is not private')
        require((actual.st_dev,actual.st_ino,actual.st_uid,actual.st_mode,actual.st_nlink)==
                (before.st_dev,before.st_ino,before.st_uid,before.st_mode,before.st_nlink),'signing key changed during open')
        value=os.read(fd,33);require(len(value)==32,'signing key must be exactly 32 bytes');return value
    finally:os.close(fd)

def parse(data):
    def pairs(values):
        result={}
        for k,v in values:
            require(k not in result,'duplicate JSON field');result[k]=v
        return result
    try:return json.loads(data,object_pairs_hook=pairs)
    except (ValueError,UnicodeDecodeError) as error:raise JournalError('invalid JSON') from error

def record(body,key):
    unsigned={'body':body,'sha256':hashlib.sha256(canonical(body)).hexdigest()}
    return {**unsigned,'hmac_sha256':hmac.new(key,canonical(unsigned),hashlib.sha256).hexdigest()}

def read_records(handle,key):
    handle.seek(0);records=[];previous=ZERO;sequence=0;identity=None;db_prefix=ZERO
    for line in handle:
        require(len(line)<=MAX_PAGE*2 and line.endswith(b'\n'),'incomplete or oversized journal record')
        item=parse(line)
        require(isinstance(item,dict) and set(item)=={'body','sha256','hmac_sha256'},'invalid signed record')
        require(hmac.compare_digest(canonical(item),canonical(record(item['body'],key))),'journal signature or digest mismatch')
        body=item['body'];require(isinstance(body,dict) and set(body)=={'previous_sha256','page'},'invalid record body')
        value=page(body['page']);current=(value['instance_id'],value['stream_id'])
        require(body['previous_sha256']==previous and integer(value['after_sequence'])==sequence,'journal hash/sequence continuity broken')
        require(identity is None or identity==current,'instance or stream changed')
        require(value['after_chain_sha256']==db_prefix,'restored database event history forked')
        db_prefix=value['next_chain_sha256']
        validate_head_commitments(records,value)
        identity=current;sequence=integer(value['next_sequence']);previous=item['sha256'];records.append(item)
    return records

def validate_head_commitments(records,value):
    commitments={}
    for record in records:
        prior=record['body']['page']
        seq=prior['head_sequence'];digest=prior['head_chain_sha256']
        require(seq not in commitments or commitments[seq]==digest,'previous database head commitment forked')
        commitments[seq]=digest
    head=value['head_sequence']
    require(head not in commitments or commitments[head]==value['head_chain_sha256'],'witnessed database head forked')
    chain=value['after_chain_sha256'];after=value['after_sequence']
    if after in commitments:require(chain==commitments[after],'cursor differs from witnessed database head')
    for event in value['items']:
        chain=event_hash(chain,event)
        if event['sequence'] in commitments:
            require(chain==commitments[event['sequence']],'page replaced previously witnessed database history')

def open_journal(path):
    plain(path.parent,True)
    if path.exists():plain(path)
    before=path.lstat() if path.exists() else None
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    actual=os.fstat(fd)
    try:
        require(stat.S_ISREG(actual.st_mode) and actual.st_nlink==1 and actual.st_uid==os.getuid() and stat.S_IMODE(actual.st_mode)==0o600,'opened journal is not private')
        require(before is None or (actual.st_dev,actual.st_ino)==(before.st_dev,before.st_ino),'journal changed during open')
        plain(path.parent,True)
        current=path.lstat();require((current.st_dev,current.st_ino)==(actual.st_dev,actual.st_ino),'journal path changed during open')
        handle=os.fdopen(fd,'r+b');fcntl.flock(handle,fcntl.LOCK_EX);return handle
    except BaseException:os.close(fd);raise

def append(path,keyfile,value):
    value=page(value);key=key_bytes(keyfile)
    with open_journal(path) as handle:
        records=read_records(handle,key)
        if records:
            last=records[-1]
            require((value['instance_id'],value['stream_id'])==(last['body']['page']['instance_id'],last['body']['page']['stream_id']),'wrong journal identity')
            if value==last['body']['page']:return last['sha256']
            if not value['items'] and value['after_sequence']==last['body']['page']['next_sequence'] and value['head_sequence']==last['body']['page']['head_sequence'] and value['after_chain_sha256']==last['body']['page']['next_chain_sha256'] and value['head_chain_sha256']==last['body']['page']['head_chain_sha256']:
                return last['sha256']
            require(integer(value['after_sequence'])==integer(last['body']['page']['next_sequence']),'page overlaps or skips retained history')
            require(value['after_chain_sha256']==last['body']['page']['next_chain_sha256'],'database history forked after restore')
            require(integer(value['head_sequence'])>=integer(last['body']['page']['head_sequence']),'database journal head rolled back')
            previous=last['sha256']
        else:
            require(integer(value['after_sequence'])==0,'new journal must start at sequence zero')
            previous=ZERO
        validate_head_commitments(records,value)
        item=record({'previous_sha256':previous,'page':value},key)
        handle.seek(0,os.SEEK_END);handle.write(canonical(item)+b'\n');handle.flush();os.fsync(handle.fileno())
        dirfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
        return item['sha256']

def verify(path,keyfile):
    plain(path);key=key_bytes(keyfile)
    with open_journal(path) as handle:records=read_records(handle,key)
    require(records,'empty external journal')
    last=records[-1];value=last['body']['page']
    return {'schema_version':1,'instance_id':value['instance_id'],'stream_id':value['stream_id'],
            'sequence':value['next_sequence'],'head_sequence':value['head_sequence'],
            'complete_to_head':not value['has_more'],'record_sha256':last['sha256'],'records':len(records)}

def checkpoint_anchor(path,keyfile,pin,excluded_roots):
    require(re.fullmatch(r'[a-f0-9]{64}',pin or '') is not None,'external checkpoint record pin required')
    for value in (path,keyfile):
        plain(value)
        for root in excluded_roots:
            resolved=root.resolve()
            require(value!=resolved and resolved not in value.parents,'journal/key must remain outside restored inputs and checkpoint')
    key=key_bytes(keyfile)
    with open_journal(path) as handle:records=read_records(handle,key)
    matches=[x for x in records if x['sha256']==pin]
    require(len(matches)==1,'external checkpoint record missing')
    value=matches[0]['body']['page'];require(not value['has_more'],'checkpoint anchor must reach its captured source head')
    return {'schema_version':1,'instance_id':value['instance_id'],'stream_id':value['stream_id'],
            'sequence':value['next_sequence'],'database_chain_sha256':value['next_chain_sha256'],'record_sha256':pin,
            'source_head_complete':True,'authoritative_reconciliation_required':True}

def replay_plan(path,keyfile,anchor,through):
    plain(path);key=key_bytes(keyfile)
    with open_journal(path) as handle:records=read_records(handle,key)
    require(re.fullmatch(r'[a-f0-9]{64}',anchor or '') is not None,'checkpoint needs external record pin')
    require(re.fullmatch(r'[a-f0-9]{64}',through or '') is not None,'recovery needs externally pinned terminal record')
    hashes=[x['sha256'] for x in records]
    require(anchor in hashes and through in hashes,'pinned external record missing')
    start,end=hashes.index(anchor),hashes.index(through);require(start<=end,'recovery predates checkpoint anchor')
    last=records[end]['body']['page'];require(not last['has_more'],'recovery journal must reach captured head')
    # Resume/upsert/eligible are recorded, but never authorize recovery reopening.
    prefix_hashes={records[start]['body']['page']['next_sequence']:records[start]['body']['page']['next_chain_sha256']}
    for signed in records[start+1:end+1]:
        value=signed['body']['page'];chain=value['after_chain_sha256']
        for event in value['items']:
            chain=event_hash(chain,event);prefix_hashes[event['sequence']]=chain
    bindings={};files={}
    for item in records[start+1:end+1]:
        for event in item['body']['page']['items']:
            binding=event['binding_id'];bindings[binding]=event['sequence']
            if event['kind'] in {'withdrawn','delete'} and event['file_id'] is not None:
                files[(binding,event['file_id'])]=event['sequence']
    return {'schema_version':1,'instance_id':last['instance_id'],'stream_id':last['stream_id'],
            'checkpoint_record_sha256':anchor,'recovery_record_sha256':through,
            'checkpoint_sequence':records[start]['body']['page']['next_sequence'],
            'checkpoint_database_chain_sha256':records[start]['body']['page']['next_chain_sha256'],
            'through_sequence':last['next_sequence'],'through_database_chain_sha256':last['next_chain_sha256'],
            'close_bindings':[{'binding_id':b,'last_sequence':seq} for b,seq in sorted(bindings.items())],
            'withdraw_files':[{'binding_id':b,'file_id':f,'last_sequence':seq} for (b,f),seq in sorted(files.items())],
            'database_prefix_hashes':prefix_hashes,
            'authoritative_reconciliation_required':True,'ingress_reopen_permitted':False}

def collect(path,keyfile,container,project,max_pages=1000):
    require(re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,63}',project or '') is not None,'exact compose project required')
    require(re.fullmatch(r'[a-f0-9]{64}',container or '') is not None,'exact container ID required')
    def owned():
        result=subprocess.run(['docker','inspect',container],capture_output=True,timeout=20)
        require(result.returncode==0,'source container missing')
        value=parse(result.stdout);require(isinstance(value,list) and len(value)==1,'invalid source inventory')
        item=value[0];labels=item.get('Config',{}).get('Labels',{})
        require(item.get('Id')==container and item.get('State',{}).get('Running') is True and
                labels.get('com.docker.compose.project')==project and labels.get('com.docker.compose.service')=='nextcloud','source ownership changed')
    for _ in range(max_pages):
        owned()
        sequence=verify(path,keyfile)['sequence'] if path.exists() else '0'
        result=subprocess.run(['docker','exec','-u','www-data',container,'php','occ',
                               'integration_weknora:export-recovery-ledger','--after='+sequence],capture_output=True,timeout=60)
        require(result.returncode==0 and len(result.stdout)<=MAX_PAGE,'source journal export failed')
        value=page(parse(result.stdout));owned();append(path,keyfile,value)
        if not value['has_more']:return verify(path,keyfile)
    raise JournalError('source journal collection exceeded bounded pages')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['append','verify','collect','watch','replay-plan'])
    parser.add_argument('--journal',type=Path,required=True);parser.add_argument('--key-file',type=Path,required=True)
    parser.add_argument('--container-id');parser.add_argument('--compose-project')
    parser.add_argument('--signed-plan-output',type=Path);parser.add_argument('--checkpoint-record-sha256');parser.add_argument('--through-record-sha256')
    args=parser.parse_args()
    try:
        if args.mode=='append':
            data=sys.stdin.buffer.read(MAX_PAGE+1);require(len(data)<=MAX_PAGE,'oversized input page')
            result={'record_sha256':append(args.journal,args.key_file,parse(data))}
        elif args.mode=='verify':result=verify(args.journal,args.key_file)
        elif args.mode in ('collect','watch'):
            result=collect(args.journal,args.key_file,args.container_id,args.compose_project)
            while args.mode=='watch':
                time.sleep(5)
                result=collect(args.journal,args.key_file,args.container_id,args.compose_project)
        else:result=replay_plan(args.journal,args.key_file,args.checkpoint_record_sha256,args.through_record_sha256)
        if args.signed_plan_output is not None:
            require(args.mode=='replay-plan','signed plan output is only valid for replay-plan')
            plain(args.signed_plan_output.parent,True)
            data=canonical(result);key=key_bytes(args.key_file)
            envelope={'plan':data.decode('ascii'),'hmac_sha256':hmac.new(key,data,hashlib.sha256).hexdigest()}
            fd=os.open(args.signed_plan_output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as target:
                target.write(json.dumps(envelope,separators=(',',':')).encode()+b'\n');target.flush();os.fsync(target.fileno())
        print(json.dumps(result,sort_keys=True));return 0
    except (JournalError,OSError,subprocess.TimeoutExpired):
        print('Recovery journal refused; check private inputs and retained evidence',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
