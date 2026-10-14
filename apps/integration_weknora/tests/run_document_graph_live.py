#!/usr/bin/env python3
"""Owned, bounded actual NC document/index/graph/state-inbox integration."""
import argparse, json, os, shutil, subprocess, tempfile, time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--source-repo',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--http-read',action='store_true');p.add_argument('--rag',action='store_true');p.add_argument('--history',action='store_true');p.add_argument('--agent',action='store_true');p.add_argument('--agent-preflight',action='store_true');p.add_argument('--direct-network',action='store_true',help='Ignore proxy variables baked into the isolated test image');p.add_argument('--model-config',type=Path);p.add_argument('--model-allowed-host',action='append',default=[]);a=p.parse_args()
if a.agent and not a.rag:p.error('--agent requires --rag')
if a.agent_preflight and not a.agent:p.error('--agent-preflight requires --agent')
if a.history and not a.rag:p.error('--history requires --rag')
if a.rag and not a.model_config:p.error('--rag requires --model-config')
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
 if a.model_config:
  if a.model_config.stat().st_mode & 0o077:raise ValueError('Model configuration must be private (0600)')
  shutil.copyfile(a.model_config,Path(private)/'models.json');(Path(private)/'models.json').chmod(0o600)
 run(*command,'prepare');run('docker','cp',nc+':/tmp/document-graph-live.json',str(fixture));fixture.chmod(0o600)
 log=(a.evidence/'document-graph-live.log').open('wb')
 http_proc=None;http_log=None
 build='gofmt -w internal/application/service/knowledge_machine_pipeline_test.go internal/application/service/nextcloud_document_graph_pipeline_test.go internal/application/service/nextcloud_live_models_test.go internal/application/service/nextcloud_live_rag_test.go internal/application/service/nextcloud_live_history_test.go internal/application/service/nextcloud_live_agent_test.go'
 if a.http_read:
  build+=' internal/handler/nextcloud_processed_live_test.go && go test -p 1 -tags=sqlite,sqlite_fts5 -c -o /tmp/processed-http.test ./internal/handler'
 build+=' && go test -p 1 -tags=sqlite,sqlite_fts5 ./internal/application/service -run "^TestNextcloudLiveDocumentToGraphAndInbox$" -count=1 -v -timeout='+('300s' if a.agent else '180s')

 network_env=[]
 if a.direct_network:
  for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):network_env+=['-e',key+'=']
 proc=subprocess.Popen(['docker','run','--rm','--name',actor,'--label','codex.task=auto-space-prd-20261012','--cpus=1','--memory=2g','--network','weknora-prd-live-processing','-v',str(a.source_repo.resolve())+':/app','-v',private+':/fixture','-v','weknora-go-mod:/go/pkg/mod','-v','weknora-go-build:/root/.cache/go-build','-w','/app','-e','GOMAXPROCS=1','-e','SSRF_WHITELIST='+','.join(a.model_allowed_host),'-e','WEKNORA_DOCUMENT_GRAPH_LIVE_FIXTURE=/fixture/fixture.json','-e','WEKNORA_DOCUMENT_GRAPH_AGENT_PREFLIGHT='+('1' if a.agent_preflight else '0'),'-e','WEKNORA_DOCUMENT_GRAPH_AGENT='+('1' if a.agent else '0'),'-e','WEKNORA_DOCUMENT_GRAPH_HISTORY='+('1' if a.history else '0'),'-e','WEKNORA_DOCUMENT_GRAPH_RAG='+('1' if a.rag else '0'),'-e','WEKNORA_DOCUMENT_GRAPH_MODEL_CONFIG='+('/fixture/models.json' if a.model_config else ''),'-e','WEKNORA_DOCUMENT_GRAPH_HTTP='+('1' if a.http_read else '0'),*network_env,'weknora-go-test:1.26-sqlite','timeout','480s','sh','-c',build],stdout=log,stderr=subprocess.STDOUT)
 try:
  deadline=time.monotonic()+360
  while not Path(str(fixture)+'.ready').exists():
   if proc.poll() is not None: raise RuntimeError('Go fixture terminated before pairing; inspect retained log')
   if time.monotonic()>deadline: raise TimeoutError('Pair readiness deadline')
   time.sleep(.2)
  run('docker','cp',str(fixture)+'.ready',nc+':/tmp/document-graph-live.json.ready');run('docker','exec',nc,'chown','www-data:www-data','/tmp/document-graph-live.json.ready');run(*command,'commit');Path(str(fixture)+'.committed').touch()
  access_deadline=time.monotonic()+(210 if a.agent else 90)
  while not Path(str(fixture)+'.access-ready').exists():
   if proc.poll() is not None: raise RuntimeError('Go fixture terminated before permission check; inspect retained log')
   if time.monotonic()>access_deadline: raise TimeoutError('Permission readiness deadline')
   time.sleep(.2)
  if a.http_read:
   assert Path(str(fixture)+'.http-input').exists()
   http_log=(a.evidence/'document-graph-live-http.log').open('wb')
   http_proc=subprocess.Popen(['docker','exec','-w','/app/internal/handler',actor,'/tmp/processed-http.test','-test.run=^TestNextcloudProcessedLiveHTTPReadAndRevocation$','-test.v','-test.timeout=120s'],stdout=http_log,stderr=subprocess.STDOUT)
   http_deadline=time.monotonic()+60
   while not Path(str(fixture)+'.http-positive').exists():
    if http_proc.poll() is not None:raise RuntimeError('HTTP positive verification failed; inspect retained log')
    if proc.poll() is not None:raise RuntimeError('Producer stopped during HTTP verification')
    if time.monotonic()>http_deadline:raise TimeoutError('HTTP positive verification deadline')
    time.sleep(.2)
  with (a.evidence/'document-graph-live-revoke.log').open('wb') as out: run(*command,'revoke',stdout=out,stderr=subprocess.STDOUT)
  Path(str(fixture)+'.revoked').touch()
  if a.http_read:
   if http_proc.wait(timeout=60):raise RuntimeError('HTTP revocation verification failed; inspect retained log')
   assert Path(str(fixture)+'.http-done').exists()
  code=proc.wait(timeout=45)
  if code: raise RuntimeError('Actual document pipeline failed; inspect retained log')
  run('docker','cp',str(fixture)+'.result',nc+':/tmp/document-graph-live.json.result');run('docker','exec',nc,'chown','www-data:www-data','/tmp/document-graph-live.json.result')
  with (a.evidence/'document-graph-live-nc.log').open('wb') as out: run(*command,'verify',stdout=out,stderr=subprocess.STDOUT)
  if a.http_read:print('Same processed generation: Gin detail/list/preview/download positive and real LDAPS share revocation passed; original bytes and lease release verified')
  print('Actual NC document -> native index -> scoped graph -> NC inbox passed; '+('configured real models' if a.model_config else 'controlled models'))
 finally:
  if proc.poll() is None:
   subprocess.run(['docker','stop','-t','3',actor],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
   proc.wait(timeout=15)
  if http_proc is not None and http_proc.poll() is None:http_proc.wait(timeout=15)
  if http_log is not None:http_log.close()
  log.close()
  run(*command,'cleanup')
