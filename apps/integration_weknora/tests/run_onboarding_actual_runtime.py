#!/usr/bin/env python3
"""Serial same-actor component coordinator; never creates a second Go actor."""
import argparse,json,os,subprocess,time,tempfile,shutil
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--actor',default='weknora-prd-component-go');p.add_argument('--nc',default='nextcloud-auto-space-prd-test-20261010');p.add_argument('--evidence',type=Path,required=True);a=p.parse_args()
os.umask(0o077);a.evidence.mkdir(parents=True,exist_ok=True)
actor=json.loads(subprocess.check_output(['docker','inspect',a.actor]))[0]
if actor['Name']!='/weknora-prd-component-go' or not actor['State']['Running'] or actor['HostConfig']['Memory']>2*1024**3:raise SystemExit('Exact running owned bounded actor required')
root=Path(tempfile.mkdtemp(prefix='weknora-onboarding-component-',dir='/tmp'));root.chmod(0o700)
fixture=root/'fixture.json';ready=Path(str(fixture)+'.ready')
command=['docker','exec','--user','www-data','-e','WEKNORA_PRD_TEST_RUNTIME=1',a.nc,'php','/var/www/html/custom_apps/integration_weknora/tests/onboarding_actual_runtime.php']
subprocess.run(command+['prepare'],check=True,timeout=30)
subprocess.run(['docker','cp',a.nc+':/tmp/onboarding_actual_fixture.json',str(fixture)],check=True,timeout=15);fixture.chmod(0o600)
actor_fixture='/tmp/result-host/'+root.name+'/fixture.json'
for name in ('onboarding-actual-wk.log','onboarding-actual-nc.log'):
 previous=a.evidence/name
 if previous.exists():shutil.copyfile(previous,a.evidence/(previous.stem+'-attempt-'+str(time.time_ns())+'.log'))
log=(a.evidence/'onboarding-actual-wk.log').open('wb');nc_log=(a.evidence/'onboarding-actual-nc.log').open('wb')
proc=subprocess.Popen(['docker','exec','-e','WEKNORA_ONBOARDING_LIVE_FIXTURE='+actor_fixture,a.actor,'sh','-c','cd /app && go test -p 1 ./internal/handler -run "^TestNextcloudOnboardingActualNCRuntime$" -count=1 -v -timeout=150s'],stdout=log,stderr=subprocess.STDOUT)
start=time.monotonic()
try:
 while not ready.exists():
  if proc.poll() is not None:raise RuntimeError('Go fixture failed before readiness; retained raw log')
  if time.monotonic()-start>120:raise RuntimeError('Fixture readiness deadline')
  time.sleep(.2)
 ready_at=time.monotonic();finish=subprocess.run(command+['finish'],stdout=nc_log,stderr=subprocess.STDOUT,timeout=45)
 if finish.returncode:
  subprocess.run(command+['abort'],stdout=nc_log,stderr=subprocess.STDOUT,timeout=5)
  raise RuntimeError('Actual NC phase failed; retained raw controlled RPC diagnostic and immediate explicit abort')
 code=proc.wait(timeout=max(1,180-(time.monotonic()-start)))
 if code:raise RuntimeError('Actual WK relational assertions failed; retained raw log')
 print('Actual paired component PASS; ready-to-finish-start_ms='+str(int((ready_at-start)*1000))+'; empty roots and synthetic WK actor/model inventory')
finally:
 if proc.poll() is None:
  # Stop only this exact test command via its Go timeout; do not kill or remove
  # the shared actor. It is bounded to 150 seconds by the command itself.
  try:proc.wait(timeout=max(1,165-(time.monotonic()-start)))
  except subprocess.TimeoutExpired:proc.terminate()
 log.close();nc_log.close()
 fixture.unlink(missing_ok=True);ready.unlink(missing_ok=True);root.rmdir()
