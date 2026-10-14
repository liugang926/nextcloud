#!/usr/bin/env python3
"""Real task-owned WebDAV bytes, signed manifest/content, precise cleanup."""
import argparse, base64, concurrent.futures, hashlib, hmac, http.client, json, os
import pathlib, resource, secrets, statistics, subprocess, sys, threading, time, urllib.parse, xml.etree.ElementTree as ET
NC='nextcloud-auto-space-prd-test-20261010'
LABEL='auto-space-prd-20261010'
OWNER='auto-prd-admin'
HOST='nextcloud'
IMAGE='python:3.12-alpine'
NETWORK='nextcloud-auto-space-prd-test-20261010'
FILE_BYTES=10_000_000
FILES=10_000
PHP_BOOT="set_exception_handler(static function(Throwable $e):void{fwrite(STDERR,get_class($e).': '.$e->getMessage().PHP_EOL);exit(1);});define('OC_CONSOLE',1);require '/var/www/html/lib/base.php';\\OC_App::loadApp('user_ldap');$app=new \\OCA\\IntegrationWeknora\\AppInfo\\Application();$services=$app->getContainer();set_exception_handler(static function(Throwable $e):void{fwrite(STDERR,get_class($e).': '.$e->getMessage().PHP_EOL);exit(1);});"

def docker(*args, capture=True):
    return subprocess.run(['docker',*args], check=True, text=True, stdout=subprocess.PIPE if capture else None).stdout

def owned():
    info=json.loads(docker('inspect',NC))[0]
    if info['Config'].get('Labels',{}).get('codex.task') != LABEL or any(info['HostConfig'].get('PortBindings',{}).values()):
        raise RuntimeError('Refusing an unowned or host-published Nextcloud runtime')
    network=json.loads(docker('network','inspect',NETWORK))[0]
    if network.get('Labels',{}).get('codex.task')!=LABEL:
        raise RuntimeError('Refusing an unowned pressure-test network')
    docker('image','inspect',IMAGE) # Fail if unavailable; never silently pull an image.

def php(source):
    result=subprocess.run(['docker','exec','-i','-u','www-data',NC,'php'], input='<?php\n'+PHP_BOOT+source,
        check=False,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if result.returncode: raise RuntimeError(result.stderr.strip())
    return result.stdout

def markersafe(marker):
    if not marker.startswith('prd-source-pressure-') or len(marker.split('-')[-1])!=32:
        raise RuntimeError('Invalid exclusive pressure marker')
    int(marker.split('-')[-1],16)

def cleanup_trash(marker):
    """Permanently delete only this test's exact root/probe trash identities."""
    owned();markersafe(marker)
    source=r"""
\OC_App::loadApp('files_trashbin');
$uid='auto-prd-admin';$names=['%s','%s-normal-upload-probe'];
$db=\OCP\Server::get(\OCP\IDBConnection::class);$q=$db->getQueryBuilder();
$q->select('id','timestamp')->from('files_trash')->where($q->expr()->eq('user',$q->createNamedParameter($uid)))
  ->andWhere($q->expr()->in('id',$q->createNamedParameter($names,\OCP\DB\QueryBuilder\IQueryBuilder::PARAM_STR_ARRAY)));
$rows=$q->executeQuery()->fetchAllAssociative();$bytes=0;
foreach($rows as $row){if(!in_array($row['id'],$names,true)){throw new RuntimeException('Trash identity mismatch');}
  $bytes+=\OCA\Files_Trashbin\Trashbin::delete($row['id'],$uid,(int)$row['timestamp']);}
$remaining=$q->executeQuery()->fetchAllAssociative();
if($remaining!==[]){throw new RuntimeException('Exact pressure trash metadata remains');}
$base='/var/www/html/data/'.$uid.'/files_trashbin/';
foreach(['files','versions','keys'] as $part){$path=$base.$part;if(!is_dir($path)){continue;}
  foreach(new DirectoryIterator($path) as $entry){foreach($names as $name){
    if(preg_match('/^'.preg_quote($name,'/').'\.d[0-9]+$/D',$entry->getFilename())===1){throw new RuntimeException('Exact pressure trash bytes remain');}}}}
echo json_encode(['exact_trash_roots_deleted'=>count($rows),'exact_trash_metadata_and_bytes_absent'=>true],JSON_THROW_ON_ERROR);
"""%(marker,marker)
    return json.loads(php(source))

_local=threading.local()
def connection():
    if not hasattr(_local,'conn'):
        _local.conn=http.client.HTTPConnection(NC,80,timeout=180)
    return _local.conn

def request(method,path,body=None,headers=None):
    conn=connection()
    conn.request(method,path,body=body,headers={'Host':HOST,**(headers or {})})
    return conn.getresponse()

def signed_headers(path,fixture):
    timestamp=str(int(time.time()));nonce=secrets.token_hex(16)
    parts=urllib.parse.urlsplit(path)
    pairs=sorted((urllib.parse.quote(k,safe='-._~'),urllib.parse.quote(v,safe='-._~')) for k,v in urllib.parse.parse_qsl(parts.query,keep_blank_values=True))
    query='&'.join(k+'='+v for k,v in pairs)
    canonical='\n'.join(['weknora-hmac-sha256-v1','GET',parts.path,query,hashlib.sha256(b'').hexdigest(),timestamp,nonce,fixture['key_id']])
    key=hashlib.sha256(fixture['token'].encode()).digest()
    return {'Authorization':'Bearer '+fixture['token'],'X-WeKnora-Key-Id':fixture['key_id'],
        'X-WeKnora-Timestamp':timestamp,'X-WeKnora-Nonce':nonce,'X-WeKnora-Signature':hmac.new(key,canonical.encode(),hashlib.sha256).hexdigest()}

def signed_get(path,fixture,headers=None,retries=None):
    """An idle connection may expire during a coordination pause. Retry one GET."""
    try:
        return request('GET',path,headers={**signed_headers(path,fixture),**(headers or {})})
    except (http.client.RemoteDisconnected,ConnectionResetError,BrokenPipeError) as error:
        observation={'path':urllib.parse.urlsplit(path).path,'first_error_type':type(error).__name__,
            'retry_response_received':False}
        if retries is not None: retries.append(observation)
        if hasattr(_local,'conn'):
            _local.conn.close();del _local.conn
        # A new signature/nonce is required even if the server consumed the first request.
        response=request('GET',path,headers={**signed_headers(path,fixture),**(headers or {})})
        observation['retry_response_received']=True
        return response

def percentile(values,p):
    values=sorted(values)
    return values[min(len(values)-1,int((len(values)-1)*p))] if values else None

def client(args):
    marker=args.marker;markersafe(marker)
    directory=pathlib.Path('/artifacts');credentials=json.loads((directory/'credentials.json').read_text())
    basic='Basic '+base64.b64encode((OWNER+':'+credentials['password']).encode()).decode()
    root='/remote.php/dav/files/'+OWNER+'/'+marker
    if args.phase=='upload':
        response=request('MKCOL',root,headers={'Authorization':basic});raw=response.read()
        if response.status!=201: raise RuntimeError('Exclusive root creation rejected: '+str(response.status))
        payload=secrets.token_bytes(FILE_BYTES) # Real materialized, non-sparse 10 MB bytes reused across PUTs.
        digest=hashlib.sha256(payload).hexdigest();latencies=[];failures=[];start=time.monotonic()
        def upload(index):
            path=root+'/file-%05d.bin'%index;begin=time.monotonic()
            response=request('PUT',path,body=payload,headers={'Authorization':basic,'Content-Type':'application/octet-stream','Content-Length':str(len(payload))})
            body=response.read()
            if response.status!=201: raise RuntimeError('PUT status '+str(response.status))
            return time.monotonic()-begin
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures={pool.submit(upload,index):index for index in range(FILES)}
            for done,future in enumerate(concurrent.futures.as_completed(futures),1):
                try: latencies.append(future.result())
                except Exception as error: failures.append({'index':futures[future],'error_type':type(error).__name__})
                if done%100==0: print('UPLOAD',done,'/',FILES,flush=True)
        elapsed=time.monotonic()-start
        result={'phase':'actual_webdav_put','files_expected':FILES,'file_bytes':FILE_BYTES,'bytes_expected':FILE_BYTES*FILES,
            'files_succeeded':len(latencies),'failures':failures,'failure_rate':len(failures)/FILES,'wall_seconds':elapsed,
            'throughput_bytes_per_second':len(latencies)*FILE_BYTES/elapsed,'p50_seconds':percentile(latencies,.5),'p95_seconds':percentile(latencies,.95),
            'client_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,'payload_sha256':digest,
            'sparse_clone_or_metadata_fixture':False}
        (directory/'upload.json').write_text(json.dumps(result,indent=2))
        if failures: raise RuntimeError('WebDAV pressure upload had '+str(len(failures))+' failures')
    elif args.phase=='probe':
        # A separate exact marker root avoids altering the 10,000-file inventory.
        probe_root=root+'-normal-upload-probe'
        response=request('MKCOL',probe_root,headers={'Authorization':basic});response.read()
        if response.status!=201: raise RuntimeError('Exclusive probe root creation rejected: '+str(response.status))
        payload=secrets.token_bytes(100_000);latencies=[];failures=[];start=time.monotonic()
        try:
            for index in range(100):
                begin=time.monotonic()
                try:
                    response=request('PUT',probe_root+'/probe-%03d.bin'%index,body=payload,
                        headers={'Authorization':basic,'Content-Type':'application/octet-stream','Content-Length':str(len(payload))})
                    response.read()
                    if response.status!=201: raise RuntimeError('Probe PUT status '+str(response.status))
                    latencies.append(time.monotonic()-begin)
                except Exception as error: failures.append({'index':index,'error_type':type(error).__name__})
                time.sleep(.2)
            result={'phase':'normal_webdav_upload_during_pressure','file_bytes':100_000,'files_expected':100,
                'files_succeeded':len(latencies),'failures':failures,'failure_rate':len(failures)/100,
                'wall_seconds':time.monotonic()-start,'p50_seconds':percentile(latencies,.5),'p95_seconds':percentile(latencies,.95),
                'baseline_available':False,'interpretation':'Bounded normal-upload latency observed during pressure; no before/after causal claim.'}
            (directory/'normal-upload-probe.json').write_text(json.dumps(result,indent=2))
            print('NORMAL_UPLOAD_PROBE',json.dumps(result),flush=True)
        finally:
            response=request('DELETE',probe_root,headers={'Authorization':basic});response.read()
            if response.status!=204: raise RuntimeError('Exclusive probe root cleanup rejected: '+str(response.status))
    elif args.phase=='download':
        fixture=json.loads((directory/'machine.json').read_text());upload=json.loads((directory/'upload.json').read_text())
        base='/index.php/apps/integration_weknora/api/v1/bindings/'+fixture['binding_id'];cursor=None;items=[];pages=0;start=time.monotonic();generation=None;manifest_retries=[]
        while True:
            path=base+'/manifest'+('?' + urllib.parse.urlencode({'cursor':cursor}) if cursor else '')
            response=signed_get(path,fixture,retries=manifest_retries);raw=response.read()
            if response.status!=200: raise RuntimeError('Signed manifest rejected: '+str(response.status)+' '+raw[:200].decode(errors='replace'))
            data=json.loads(raw);pages+=1
            if generation is None: generation=data['generation']
            if generation!=data['generation']: raise RuntimeError('Manifest changed generation during stable read')
            items.extend(data['items'])
            if data['complete']: break
            cursor=data['next_cursor']
        manifest_seconds=time.monotonic()-start
        if len(items)!=FILES or len({item['file_id'] for item in items})!=FILES or sum(item['size'] for item in items)!=FILES*FILE_BYTES:
            raise RuntimeError('Manifest did not cover exactly the real 100 GB source')
        if any(item['size']!=FILE_BYTES or not item['etag'] or not str(item['source_content_revision']).isdigit() for item in items):
            raise RuntimeError('Manifest size/ETag/revision identity invalid')
        (directory/'manifest.json').write_text(json.dumps({'generation':generation,'pages':pages,'files':len(items),
            'bytes':sum(item['size'] for item in items),'wall_seconds':manifest_seconds,'transport_retries':manifest_retries,'items':items},indent=2))
        latencies=[];failures=[];transport_retries=[];verified_bytes=0;start=time.monotonic()
        def download(item):
            path=base+'/files/'+str(item['file_id'])+'/content?'+urllib.parse.urlencode({'content_revision':item['source_content_revision']})
            begin=time.monotonic()
            response=signed_get(path,fixture,{'If-Match':'"'+item['etag']+'"'},transport_retries)
            if response.status!=200: response.read();raise RuntimeError('Signed content rejected: '+str(response.status))
            if response.getheader('X-Nextcloud-Content-Revision')!=item['source_content_revision'] or response.getheader('ETag','').strip('"')!=item['etag']:
                response.read();raise RuntimeError('Content identity header mismatch')
            digest=hashlib.sha256();size=0
            while True:
                block=response.read(1024*1024)
                if not block: break
                size+=len(block);digest.update(block)
            if size!=FILE_BYTES or digest.hexdigest()!=upload['payload_sha256']: raise RuntimeError('Actual downloaded bytes/digest mismatch')
            return size,time.monotonic()-begin
        paused_seconds=0
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for offset in range(0,len(items),100):
                pause_started=time.monotonic()
                if (directory/'pause-download').exists(): print('DOWNLOAD_PAUSED',offset,'/',FILES,flush=True)
                while (directory/'pause-download').exists(): time.sleep(1)
                paused_seconds+=time.monotonic()-pause_started
                futures={pool.submit(download,item):item['file_id'] for item in items[offset:offset+100]}
                for future in concurrent.futures.as_completed(futures):
                    try: size,latency=future.result();verified_bytes+=size;latencies.append(latency)
                    except Exception as error: failures.append({'file_id':futures[future],'error_type':type(error).__name__})
                print('DOWNLOAD',min(offset+100,FILES),'/',FILES,'VERIFIED_BYTES',verified_bytes,'FINAL_FAILURES',len(failures),flush=True)
        elapsed=time.monotonic()-start
        result={'phase':'signed_streamed_content','files_verified':len(latencies),'bytes_verified':verified_bytes,'failures':failures,'failure_rate':len(failures)/FILES,
            'wall_seconds':elapsed,'coordination_pause_seconds':paused_seconds,'active_seconds':elapsed-paused_seconds,
            'throughput_bytes_per_second':verified_bytes/elapsed,'active_throughput_bytes_per_second':verified_bytes/(elapsed-paused_seconds),
            'p50_seconds':percentile(latencies,.5),'p95_seconds':percentile(latencies,.95),
            'client_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,'model_or_bigdoc_index_test':False}
        result['first_transport_failures']=transport_retries
        result['first_transport_failure_rate']=len(transport_retries)/FILES
        result['retry_responses_received']=sum(item['retry_response_received'] for item in transport_retries)
        (directory/'download.json').write_text(json.dumps(result,indent=2))
        if failures: raise RuntimeError('Signed content pressure read had '+str(len(failures))+' failures')
    else: raise RuntimeError('Unsupported client phase')

def host(args):
    owned();marker='prd-source-pressure-'+secrets.token_hex(16);markersafe(marker)
    artifacts=pathlib.Path(args.artifacts or ('/tmp/'+marker));artifacts.mkdir(mode=0o700)
    credentials=artifacts/'credentials.json';credentials.write_text(json.dumps({'password':args.password}));credentials.chmod(0o600)
    record={'marker':marker,'container':NC,'task_label':LABEL,'owner_uid':OWNER,'actual_source_bytes_expected':FILES*FILE_BYTES,
        'started_at_unix':int(time.time()),'worktree':str(pathlib.Path.cwd()),'artifacts_path':str(artifacts),'scope':'owned_source_upload_manifest_signed_download'}
    (artifacts/'ownership.json').write_text(json.dumps(record,indent=2))
    script=str(pathlib.Path(__file__).resolve());binding='pressure_'+secrets.token_hex(16);created=False
    sample_stop=threading.Event();memory_samples=[]
    def sample_memory():
        while not sample_stop.wait(10):
            try:
                value=docker('stats','--no-stream','--format','{{.MemUsage}}',NC).strip().split('/')[0].strip()
                units={'GiB':1024**3,'MiB':1024**2,'KiB':1024,'GB':10**9,'MB':10**6,'kB':1000,'B':1}
                for unit,multiplier in units.items():
                    if value.endswith(unit): memory_samples.append(int(float(value[:-len(unit)].strip())*multiplier));break
            except Exception: pass
    memory_thread=threading.Thread(target=sample_memory,daemon=True);memory_thread.start()
    try:
        for phase in ['upload','download']:
            if phase=='download':
                source="""
$uid='auto-prd-admin';$name='%s';$home=\\OCP\\Server::get(\\OCP\\Files\\IRootFolder::class)->getUserFolder($uid);$root=$home->get($name);
if(!$root instanceof \\OCP\\Files\\Folder || $root->getName()!==$name){throw new RuntimeException('Exclusive pressure root absent');}
$registry=$services->get(\\OCA\\IntegrationWeknora\\Service\\BindingRegistryService::class);$registry->save('%s','Owned 100GB pressure source',$uid,$root->getId());
$keys=$services->get(\\OCA\\IntegrationWeknora\\Service\\MachineKeyRegistryService::class);$token=$keys->issue('%s','%s',$uid);
$path=$root->getStorage()->getLocalFile($root->getInternalPath());$count=0;$bytes=0;$blocks=0;
foreach(new DirectoryIterator($path) as $entry){if($entry->isFile()){$count++;$s=stat($entry->getPathname());$bytes+=$s['size'];$blocks+=$s['blocks']*512;}}
if($count!==%d || $bytes!==%d || $blocks<$bytes){throw new RuntimeException('Physical byte/block inventory differs from real 100GB');}
echo json_encode(['binding_id'=>'%s','key_id'=>'%s','token'=>$token,'root_file_id'=>$root->getId(),'source_files'=>$count,'source_bytes'=>$bytes,'allocated_source_bytes'=>$blocks,'local_path'=>$path],JSON_THROW_ON_ERROR);
"""%(marker,binding,binding,'key_'+binding,FILES,FILES*FILE_BYTES,binding,'key_'+binding)
                machine=json.loads(php(source));created=True
                (artifacts/'machine.json').write_text(json.dumps(machine));(artifacts/'machine.json').chmod(0o600)
                print('PHYSICAL',json.dumps({key:machine[key] for key in ['source_files','source_bytes','allocated_source_bytes']}),flush=True)
            name=marker+'-'+phase
            command=['run','--rm','--name',name,'--label','codex.task='+LABEL,'--network',NETWORK,'--cpus','1','--memory','512m','--memory-swap','512m',
                '-v',script+':/pressure.py:ro','-v',str(artifacts)+':/artifacts',IMAGE,'python','/pressure.py','--client','--phase',phase,'--marker',marker,'--workers',str(args.workers),'--files',str(FILES),'--file-bytes',str(FILE_BYTES)]
            docker(*command,capture=False)
        record['nextcloud_container_peak_observed_memory_bytes']=max(memory_samples) if memory_samples else None
        record['memory_sampling_seconds']=10
        record['completed_at_unix']=int(time.time());record['upload']=json.loads((artifacts/'upload.json').read_text());record['download']=json.loads((artifacts/'download.json').read_text())
        machine=json.loads((artifacts/'machine.json').read_text());record['physical_inventory']={key:machine[key] for key in ['source_files','source_bytes','allocated_source_bytes']}
        record['manifest']={key:value for key,value in json.loads((artifacts/'manifest.json').read_text()).items() if key!='items'}
        (artifacts/'receipt.json').write_text(json.dumps(record,indent=2));print('RECEIPT',str(artifacts/'receipt.json'),flush=True)
    finally:
        sample_stop.set();memory_thread.join(timeout=15)
        owned();markersafe(marker)
        # Exact marker binding + exact owner root only. Never enumerate/delete siblings.
        source="""
$uid='auto-prd-admin';$name='%s';$binding='%s';$registry=$services->get(\\OCA\\IntegrationWeknora\\Service\\BindingRegistryService::class);
foreach($registry->listBindings() as $row){if($row['id']===$binding){if($row['owner_uid']!==$uid){throw new RuntimeException('Cleanup owner mismatch');}$registry->setPublicationState($binding,'stopped',$uid);$registry->remove($binding);break;}}
$home=\\OCP\\Server::get(\\OCP\\Files\\IRootFolder::class)->getUserFolder($uid);if($home->nodeExists($name)){$root=$home->get($name);if(!$root instanceof \\OCP\\Files\\Folder || $root->getName()!==$name){throw new RuntimeException('Cleanup root mismatch');}$root->delete();}
echo 'Exact owned pressure root and binding cleaned';
"""%(marker,binding)
        print(php(source).strip(),flush=True)
        physical='/var/www/html/data/'+OWNER+'/files/'+marker
        docker('exec',NC,'rm','-rf','--',physical,capture=False)
        docker('exec',NC,'test','!','-e',physical,capture=False)
        record['trash_cleanup']=cleanup_trash(marker)
        record['cleanup_completed_at_unix']=int(time.time())
        if (artifacts/'receipt.json').exists(): (artifacts/'receipt.json').write_text(json.dumps(record,indent=2))
        (artifacts/'ownership.json').write_text(json.dumps(record,indent=2))
        credentials.unlink(missing_ok=True);(artifacts/'machine.json').unlink(missing_ok=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--files',type=int,default=10000);parser.add_argument('--file-bytes',type=int,default=10000000);parser.add_argument('--client',action='store_true');parser.add_argument('--phase');parser.add_argument('--marker');parser.add_argument('--workers',type=int,default=8);parser.add_argument('--artifacts');parser.add_argument('--password',default=os.environ.get('WEKNORA_PRD_PRESSURE_PASSWORD',''))
    args=parser.parse_args();FILES=args.files;FILE_BYTES=args.file_bytes
    if FILES<1 or FILES>10000 or FILE_BYTES<1 or FILE_BYTES>10000000: raise SystemExit('Out-of-scope fixture sizes')
    if args.client: client(args)
    else:
        if os.environ.get('WEKNORA_PRD_TEST_RUNTIME')!='1' or not args.password: raise SystemExit('Disposable opt-in and synthetic password required')
        host(args)
