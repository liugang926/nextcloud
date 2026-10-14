#!/usr/bin/env python3
"""Source-only driver: Root executes only in its serial, exact-owned window.
Never creates resources, builds binaries, guesses IDs, or changes SQL expiry.
It verifies an explicitly supplied existing owner and runs the dedicated parent.
"""
import argparse, hashlib, json, math, os, pathlib, subprocess, sys, time

def run(args, timeout=30):
    return subprocess.run(args, check=True, timeout=timeout, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

def write(path, value):
    raw=(json.dumps(value, indent=2)+'\n').encode()
    path.write_bytes(raw); path.chmod(0o600)
    return hashlib.sha256(raw).hexdigest()

def event(kind, **facts):
    print(json.dumps({'event':kind, **facts}), flush=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--container-id', required=True)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--owner-label-key', required=True)
    parser.add_argument('--source-head', required=True)
    parser.add_argument('--source-tree', required=True)
    parser.add_argument('--deadline-unix', required=True, type=float)
    parser.add_argument('--binary-path', required=True)
    parser.add_argument('--workdir', required=True)
    parser.add_argument('--binary-sha256', required=True)
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--private-output', required=True)
    args=parser.parse_args()
    if not math.isfinite(args.deadline_unix) or args.deadline_unix<=time.time():
        raise RuntimeError('external Root coordinated deadline is absent/exhausted')
    # The absolute UNIX value originates before resource prepare in Root. Anchor
    # remaining wall time to this monotonic watchdog immediately on entry.
    entered=time.monotonic()
    deadline=min(entered+1200,entered+(args.deadline_unix-time.time()))
    def remaining():
        value=deadline-time.monotonic()
        if value<=0:raise RuntimeError('external coordinated window exhausted')
        return value
    def bounded_run(command,timeout=30):
        return run(command,timeout=min(timeout,remaining()))
    if any(len(x)!=40 or not all(c in '0123456789abcdef' for c in x) for x in [args.source_head,args.source_tree]):
        raise RuntimeError('require exact full precompiled source HEAD/tree')
    if len(args.binary_sha256)!=64 or not all(c in '0123456789abcdef' for c in args.binary_sha256):
        raise RuntimeError('require actual full binary SHA256')
    if not args.nonce or any(c not in '0123456789abcdef-' for c in args.nonce.lower()):
        raise RuntimeError('require fresh explicit UUID or hex owner nonce')
    if not all(pathlib.PurePosixPath(p).is_absolute() for p in [args.binary_path,args.workdir,args.run_dir]):
        raise RuntimeError('require absolute container binary/workdir/run paths')
    out=pathlib.Path(args.private_output).resolve(); out.mkdir(mode=0o700, parents=True, exist_ok=False)
    if len(args.container_id)!=64 or not all(c in '0123456789abcdef' for c in args.container_id):
        raise RuntimeError('require full actual owner container ID')
    inspect=json.loads(bounded_run(['docker','inspect',args.container_id]))
    if len(inspect)!=1: raise RuntimeError('ambiguous owner')
    owner=inspect[0]
    def validate(current):
        if current['Id']!=args.container_id or not current['State']['Running']:
            raise RuntimeError('exact owner is not running')
        if current['Config'].get('Labels',{}).get(args.owner_label_key)!=args.nonce:
            raise RuntimeError('actual owner nonce label mismatch')
        if current['State'].get('OOMKilled'):raise RuntimeError('owner OOMKilled')
        host=current.get('HostConfig',{})
        if host.get('Memory')!=3*1024**3 or host.get('MemorySwap')!=3*1024**3 or host.get('NanoCpus')!=1000000000 or host.get('NetworkMode')!='none':
            raise RuntimeError('exact owner memory/swap/CPU/network policy mismatch')
        if current['Image']!=owner['Image'] or current['Mounts']!=owner['Mounts'] or current['Config']['Env']!=owner['Config']['Env'] or current['Config'].get('Entrypoint')!=owner['Config'].get('Entrypoint') or current['Config'].get('Cmd')!=owner['Config'].get('Cmd'):
            raise RuntimeError('owner image/mount/env drift')
    validate(owner)
    # Full private inspect may include environment. Public output is its SHA only.
    private_inspect_sha=write(out/'owner.initial.private.json',owner)
    image=owner['Image']; envsha=hashlib.sha256(json.dumps(owner['Config']['Env'],sort_keys=True).encode()).hexdigest()
    actualsha=bounded_run(['docker','exec',args.container_id,'sha256sum',args.binary_path]).decode().split()[0]
    actualbytes=int(bounded_run(['docker','exec',args.container_id,'stat','-c','%s',args.binary_path]).decode().strip())
    if actualsha!=args.binary_sha256:raise RuntimeError('actual helper binary SHA mismatch')
    listing=bounded_run(['docker','exec','-w',args.workdir,args.container_id,args.binary_path,'-test.list=^TestOriginalLocalDownload(CrashChild|OSKillActualSQLite)$']).decode().splitlines()
    expected=['TestOriginalLocalDownloadCrashChild','TestOriginalLocalDownloadOSKillActualSQLite']
    if any(name not in listing for name in expected):raise RuntimeError('actual Linux executable does not contain both dedicated helper entries')
    write(out/'precompiled.binary.private.json',{'source_head':args.source_head,'source_tree':args.source_tree,'binary_sha256':actualsha,'actual_binary_bytes':actualbytes,'actual_executable_test_entries':expected,'binary_path':args.binary_path,'workdir':args.workdir})
    event('owner_preflight_verified',owner_inspect_sha256=private_inspect_sha,binary_sha256=actualsha,binary_bytes=actualbytes)
    command=['docker','exec','-w',args.workdir,
      '-e','WEKNORA_ORIGINAL_DOWNLOAD_OSKILL_ACTUAL=1',
      '-e','NC_POLICY_COORDINATED_DEADLINE_UNIX='+format(args.deadline_unix,'.9f'),
      '-e','WEKNORA_ORIGINAL_DOWNLOAD_CRASH_NONCE='+args.nonce,
      '-e','WEKNORA_ORIGINAL_DOWNLOAD_CRASH_SOURCE_HEAD='+args.source_head,
      '-e','WEKNORA_ORIGINAL_DOWNLOAD_CRASH_RUN_DIR='+args.run_dir,
      args.container_id,args.binary_path,'-test.run=^TestOriginalLocalDownloadOSKillActualSQLite$',
      '-test.count=1','-test.timeout=1200s','-test.v']
    raw=open(out/'parent.private.log','xb')
    # No reset after resource prepare/preflight: use Root's externally coordinated
    # absolute deadline and this same monotonic watchdog for the parent launch.
    remaining()
    started=time.monotonic()
    child=subprocess.Popen(command,stdout=raw,stderr=subprocess.STDOUT)
    def copied(name):
        target=out/name
        proc=subprocess.run(['docker','cp',args.container_id+':'+args.run_dir+'/'+name,str(target)],timeout=min(10,remaining()),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if proc.returncode: return None
        target.chmod(0o600)
        return json.loads(target.read_text())
    try:
        parent=None; facts=None
        while time.monotonic()<deadline and child.poll() is None:
            if parent is None:parent=copied('parent.started.private.json')
            if facts is None:facts=copied('before-kill.facts.private.json')
            if parent and facts:break
            time.sleep(1)
        if not parent or not facts:raise RuntimeError('real parent/committed owner readiness missing')
        if parent['nonce']!=args.nonce or facts['ResultID'] or len(facts['Owners'])!=2 or len(facts['Leases'])!=2:
            raise RuntimeError('real ready prerequisites mismatch')
        # first.ready includes its actual OS child PID; before-kill snapshot is
        # independently collected by parent on SQL and therefore has parent PID.
        first=copied('first.ready.private.json')
        if not first or first['PID']==parent['pid'] or first['AttemptID']!=facts['AttemptID'] or first['Owners']!=facts['Owners'] or first['Leases']!=facts['Leases'] or first['OriginalFactsSHA']!=facts['OriginalFactsSHA'] or first['OriginalBodiesSHA']!=facts['OriginalBodiesSHA']:
            raise RuntimeError('OS child and real SQL attempt do not agree')
        validate(json.loads(bounded_run(['docker','inspect',args.container_id]))[0])
        runtime={'Nonce':args.nonce,'ContainerID':args.container_id,'ImageID':image,'SourceHead':args.source_head,'BinarySHA':actualsha,
          'ParentPID':parent['pid'],'Entrypoint':owner['Config'].get('Entrypoint') or [],'Command':owner['Config'].get('Cmd') or [],
          'Mounts':[json.dumps(x,sort_keys=True) for x in owner['Mounts']],'EnvironmentSHA':envsha}
        write(out/'owner.runtime.private.json',runtime)
        bounded_run(['docker','cp',str(out/'owner.runtime.private.json'),args.container_id+':'+args.run_dir+'/owner.runtime.private.json'])
        # Automated identity revalidation only; task authorization already exists.
        validate(json.loads(bounded_run(['docker','inspect',args.container_id]))[0])
        # Parent uses Process.Signal on its own actual child handle. This driver
        # audits that real child's current Linux executable/argv/environment too.
        exe_sha=bounded_run(['docker','exec',args.container_id,'sha256sum','/proc/'+str(first['PID'])+'/exe']).decode().split()[0]
        argv=bounded_run(['docker','exec',args.container_id,'cat','/proc/'+str(first['PID'])+'/cmdline'])
        child_env=bounded_run(['docker','exec',args.container_id,'cat','/proc/'+str(first['PID'])+'/environ'])
        if exe_sha!=actualsha or b'-test.run=^TestOriginalLocalDownloadCrashChild$' not in argv.split(b'\0'):
            raise RuntimeError('actual child executable/argv drift before signal')
        env_items=child_env.split(b'\0')
        if ('WEKNORA_ORIGINAL_DOWNLOAD_CRASH_MANIFEST='+args.run_dir+'/manifest.private.json').encode() not in env_items or b'WEKNORA_ORIGINAL_DOWNLOAD_CRASH_CHILD_MODE=first' not in env_items:
            raise RuntimeError('actual child dedicated mode/manifest mismatch')
        write(out/'child.procfs.beforekill.private.json',{'actual_child_pid':first['PID'],'binary_sha256':exe_sha,'argv_sha256':hashlib.sha256(argv).hexdigest(),'environment_sha256':hashlib.sha256(child_env).hexdigest()})
        identity={'Nonce':args.nonce,'ContainerID':args.container_id,'BinarySHA':actualsha,'ParentPID':parent['pid'],'ChildPID':first['PID']}
        write(out/'kill.identity-audit.private.json',identity)
        bounded_run(['docker','cp',str(out/'kill.identity-audit.private.json'),args.container_id+':'+args.run_dir+'/kill.identity-audit.private.json'])
        event('exact_child_identity_audited',nonce=args.nonce,parent_pid=parent['pid'],child_pid=first['PID'],attempt_expires_ms=facts['AttemptExpiresMS'])
        last=0
        while child.poll() is None and time.monotonic()<deadline:
            now=time.monotonic()
            if now-last>=30:
                event('fixed_window_active',elapsed_seconds=round(now-started,3));last=now
            time.sleep(1)
        if child.poll() is None:
            # Do not claim timeout is an intended crash or extend the window.
            raise RuntimeError('external coordinated window exceeded; do not classify timeout as intended SIGKILL; exact owner requires Root cleanup')
        evidence={}
        for name in ['manifest.private.json','before-kill.identity.private.json','kill.terminal.private.json','unexpired-probe.private.json','recovered.private.json','result.verified.private.json']:
            evidence[name]=copied(name)
        if child.returncode==0:
            if any(value is None for value in evidence.values()):raise RuntimeError('parent pass without all actual crash/receipt evidence')
            killed=evidence['kill.terminal.private.json']
            if killed['child_pid']!=first['PID'] or killed['signal']!=9 or not killed['process_terminal']:
                raise RuntimeError('terminal is not the audited OS child SIGKILL')
            recovered=evidence['result.verified.private.json']
            if not recovered['ResultID'] or recovered['Attempts']!=2 or len(recovered['Owners'])!=4 or len(recovered['Leases'])!=4:
                raise RuntimeError('actual recovery/result facts incomplete')
            if recovered['OriginalFactsSHA']!=facts['OriginalFactsSHA'] or recovered['OriginalBodiesSHA']!=facts['OriginalBodiesSHA']:
                raise RuntimeError('original facts/bodies changed across actual crash')
        current=json.loads(bounded_run(['docker','inspect',args.container_id]))[0]
        write(out/'owner.terminal.private.json',current)
        result={'exit_code':child.returncode,'elapsed_seconds':time.monotonic()-started,'actual_binary_bytes':actualbytes,
          'binary_sha256':actualsha,'owner_oom_killed':current['State'].get('OOMKilled'),'actual_signal_and_fact_files_require_independent_review':True,'coordinated_deadline_unix':args.deadline_unix,'runner_entry_elapsed_seconds':time.monotonic()-entered}
        write(out/'runner.result.metadata.json',result);event('parent_terminal',**result)
        return child.returncode
    finally:
        raw.close()
        # Never remove/stop guessed resources. On runner failure Root joins this
        # exact known owner under its standard resource audit/cleanup protocol.

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        event('runner_failure',error_type=type(exc).__name__,reason=str(exc) if isinstance(exc,RuntimeError) else 'private subprocess/metadata failure');sys.exit(1)
