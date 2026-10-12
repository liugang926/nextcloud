#!/usr/bin/env python3
"""Owned, bounded actual NC document/index/graph/state-inbox integration."""
import argparse, json, os, subprocess, tempfile, time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--source-repo',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);a=p.parse_args()
os.umask(0o077);a.evidence.mkdir(parents=True,exist_ok=True)
nc='nextcloud-auto-space-prd-test-20261010';actor='weknora-prd-docgraph-live'
info=json.loads(subprocess.check_output(['docker','inspect',nc]))[0]
assert info['Config']['Labels'].get('codex.task')=='auto-space-prd-20261010'
def run(*args,**kwargs): return subprocess.run(args,check=True,timeout=45,**kwargs)
script=Path(__file__).with_name('document_graph_live_runtime.php')
run('docker','cp',str(script),nc+':/tmp/document_graph_live_runtime.php')
command=['docker','exec','--user','www-data','-e','WEKNORA_PRD_TEST_RUNTIME=1',nc,'php','/tmp/document_graph_live_runtime.php']
with tempfile.TemporaryDirectory(prefix='document-graph-live-') as private:
 fixture=Path(private)/'fixture.json'
 run(*command,'prepare');run('docker','cp',nc+':/tmp/document-graph-live.json',str(fixture));fixture.chmod(0o600)
 log=(a.evidence/'document-graph-live.log').open('wb')
 proc=subprocess.Popen(['docker','run','--rm','--name',actor,'--label','codex.task=auto-space-prd-20261012','--cpus=1','--memory=2g','--network','weknora-prd-live-processing','-v',str(a.source_repo.resolve())+':/app','-v',private+':/fixture','-v','weknora-go-mod:/go/pkg/mod','-v','weknora-go-build:/root/.cache/go-build','-w','/app','-e','GOMAXPROCS=1','-e','WEKNORA_DOCUMENT_GRAPH_LIVE_FIXTURE=/fixture/fixture.json','weknora-go-test:1.26-sqlite','sh','-c','gofmt -w internal/application/service/knowledge_machine_pipeline_test.go internal/application/service/nextcloud_document_graph_pipeline_test.go; timeout 240s go test -p 1 -tags=sqlite,sqlite_fts5 ./internal/application/service -run "^TestNextcloudLiveDocumentToGraphAndInbox$" -count=1 -v -timeout=120s'],stdout=log,stderr=subprocess.STDOUT)
 try:
  deadline=time.monotonic()+180
  while not Path(str(fixture)+'.ready').exists():
   if proc.poll() is not None: raise RuntimeError('Go fixture terminated before pairing; inspect retained log')
   if time.monotonic()>deadline: raise TimeoutError('Pair readiness deadline')
   time.sleep(.2)
  run('docker','cp',str(fixture)+'.ready',nc+':/tmp/document-graph-live.json.ready');run('docker','exec',nc,'chown','www-data:www-data','/tmp/document-graph-live.json.ready');run(*command,'commit');Path(str(fixture)+'.committed').touch()
  access_deadline=time.monotonic()+90
  while not Path(str(fixture)+'.access-ready').exists():
   if proc.poll() is not None: raise RuntimeError('Go fixture terminated before permission check; inspect retained log')
   if time.monotonic()>access_deadline: raise TimeoutError('Permission readiness deadline')
   time.sleep(.2)
  with (a.evidence/'document-graph-live-revoke.log').open('wb') as out: run(*command,'revoke',stdout=out,stderr=subprocess.STDOUT)
  Path(str(fixture)+'.revoked').touch()
  code=proc.wait(timeout=45)
  if code: raise RuntimeError('Actual document pipeline failed; inspect retained log')
  run('docker','cp',str(fixture)+'.result',nc+':/tmp/document-graph-live.json.result');run('docker','exec',nc,'chown','www-data:www-data','/tmp/document-graph-live.json.result')
  with (a.evidence/'document-graph-live-nc.log').open('wb') as out: run(*command,'verify',stdout=out,stderr=subprocess.STDOUT)
  print('Actual NC document -> native index -> scoped graph -> NC inbox passed; controlled models')
 finally:
  if proc.poll() is None:
   subprocess.run(['docker','stop','-t','3',actor],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
   proc.wait(timeout=15)
  log.close()
  run(*command,'cleanup')
