#!/usr/bin/env python3
"""Verify trusted gate initialization using an owned offline NC/PG fixture.

The caller owns Nextcloud/PostgreSQL cleanup. This probe creates and removes its
own Samba AD container. It uses trusted LDAPS and authenticated recovery files;
it never inserts an open gate or enables a development identity bypass.
"""
import argparse
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
AD_IMAGE = 'sha256:8180956cd2b86aac26b14279d02dc1f18749fd5fc488697dedd960941ef43cf4'
SPEC = importlib.util.spec_from_file_location('recovery_journal', ROOT / 'scripts/ops/publication-recovery-journal.py')
journal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(journal)

PHP = r'''<?php
declare(strict_types=1);
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
foreach (['WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY','WEKNORA_LOCAL_COMPOSE','WEKNORA_EVENT_DEV_HTTP'] as $key) {
 if (getenv($key) === '1') { throw new RuntimeException('Identity bypass is forbidden'); }
}
define('OC_CONSOLE',1); require '/var/www/html/lib/base.php';
\OC_App::loadApp('user_ldap');
spl_autoload_register(static function(string $class):void {
 $prefix='OCA\\IntegrationWeknora\\';
 if (str_starts_with($class,$prefix)) { $file='/var/www/html/custom_apps/integration_weknora/lib/'.str_replace('\\','/',substr($class,strlen($prefix))).'.php'; if(is_file($file)){require_once $file;} }
});
$c=(new \OCA\IntegrationWeknora\AppInfo\Application())->getContainer();
$db=\OCP\Server::get(\OCP\IDBConnection::class);
$config=\OCP\Server::get(\OCP\IConfig::class);
$controls=$c->get(\OCA\IntegrationWeknora\Service\ControlRecoveryService::class);
$bindings=$c->get(\OCA\IntegrationWeknora\Service\BindingRegistryService::class);
$mode=$argv[1];
if($mode==='missing-gate') {
 $q=$db->getQueryBuilder();$r=$q->select('closed')->from('weknora_control_gate')->executeQuery();
 try{$gate=$r->fetchAllAssociative();}finally{$r->closeCursor();}
 if($gate!==[]){throw new RuntimeException('Gate fixture must start absent');}
 $reflection=new ReflectionClass(\OCA\IntegrationWeknora\Service\DirectoryPublicationService::class);
 $service=$reflection->newInstanceWithoutConstructor();
 foreach(['db'=>$db,'config'=>$config,'recovery'=>$controls] as $key=>$value){$reflection->getProperty($key)->setValue($service,$value);}
 try{$reflection->getMethod('currentManagerMetadata')->invoke($service,'gate-missing',[]);throw new RuntimeException('Missing gate accepted');}
 catch(UnexpectedValueException $e){if($e->getMessage()!=='Manager recovery control unavailable'){throw $e;}}
 echo json_encode(['missing_gate_manager_metadata_denied'=>true]);exit;
}
if($mode==='setup') {
 if($bindings->listBindings()!==[]){throw new RuntimeException('Unexpected existing binding');}
 $uid='gate-publisher';$user=\OCP\Server::get(\OCP\IUserManager::class)->get($uid);
 if($user===null||$user->getBackendClassName()!=='LDAP'||!\OCP\Server::get(\OCP\IGroupManager::class)->isAdmin($uid)){throw new RuntimeException('Live LDAP admin missing');}
 $identity=$c->get(\OCA\IntegrationWeknora\Service\IdentityMappingService::class)->forUser($uid);
 \OC_Util::setupFS($uid);
 $home=\OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid);
 $folder=$home->newFolder('TrustedRecovery');$file=$folder->newFile('probe.txt','trusted recovery source bytes');
 $bindings->save('trusted-gate-source','Trusted recovery',$uid,$folder->getId());
 $c->get(\OCA\IntegrationWeknora\Service\SourceContentRevisionService::class)->current($file->getId());
 $c->get(\OCA\IntegrationWeknora\Service\DirectoryGrantSnapshotService::class)->snapshot($folder,$uid,$uid);
 echo json_encode(['live_ldap_admin'=>true,'identity'=>$identity,'root_file_id'=>$folder->getId(),'file_id'=>$file->getId()]);exit;
}
if($mode==='state') {
 $q=$db->getQueryBuilder();$r=$q->select('*')->from('weknora_control_gate')->executeQuery();
 try{$gates=$r->fetchAllAssociative();}finally{$r->closeCursor();}
 echo json_encode(['inventory'=>$controls->metadataInventory(),'gates'=>$gates,'bindings'=>$bindings->listBindings()]);exit;
}
throw new RuntimeException('Unknown gate probe action');
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('nc', 'pg', 'label'): parser.add_argument('--' + key, required=True)
    parser.add_argument('--ad-image', default=AD_IMAGE, help='Preloaded Samba AD image; resolved to an immutable local ID, never pulled')
    args = parser.parse_args()
    if not re.fullmatch(r'manager-upgrade-[a-f0-9]{12}', args.label):
        raise RuntimeError('Exact owned upgrade fixture label required')
    os.umask(0o077)
    secret_values = []

    def run(*values, data=None, check=True, timeout=180):
        reply = subprocess.run(values, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, cwd=ROOT)
        if check and reply.returncode:
            error = (reply.stderr + reply.stdout).decode(errors='replace')[-3500:]
            for value in secret_values: error = error.replace(value, '[redacted]')
            raise RuntimeError('Owned gate fixture command failed: ' + error)
        return reply

    def owned(name):
        item = json.loads(run('docker', 'inspect', name).stdout)[0]
        assert item['Config'].get('Labels', {}).get('codex.task') == args.label
        assert not any((item['HostConfig'].get('PortBindings') or {}).values())
        assert not item['HostConfig'].get('DeviceRequests')
        assert not any(m['Type'] == 'bind' for m in item['Mounts'])
        assert not any(k.split('=', 1)[0] in {'WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY', 'WEKNORA_LOCAL_COMPOSE', 'WEKNORA_EVENT_DEV_HTTP'} and k.endswith('=1') for k in item['Config'].get('Env', []))
        return item

    pg = owned(args.pg); nc = owned(args.nc)
    assert pg['HostConfig']['NetworkMode'] == 'none'
    assert nc['HostConfig']['NetworkMode'] in {'container:' + args.pg, 'container:' + pg['Id']}
    ad = args.label + '-gate-ad'
    created = False
    current_stage = 'starting'
    stages = []
    env = ('-e', 'WEKNORA_PRD_TEST_RUNTIME=1', '-e', 'LDAPTLS_CACERT=/etc/ssl/certs/ca-certificates.crt')

    def nc_exec(*values, **kw):
        return run('docker', 'exec', '-u', '33:33', *env, args.nc, *values, **kw)

    def occ(*values):
        return nc_exec('php', 'occ', *values).stdout.decode()

    def php(mode):
        return json.loads(nc_exec('php', '/tmp/manager-gate-probe.php', mode).stdout)

    try:
        ad_image = json.loads(run('docker', 'image', 'inspect', args.ad_image).stdout)[0]['Id']
        run('docker', 'run', '-d', '--pull=never', '--name', ad, '--label', 'codex.task=' + args.label,
            '--network=container:' + args.pg, '--cpus=.5', '--memory=768m', '--memory-swap=768m',
            '--pids-limit=256', '--security-opt=no-new-privileges', '--entrypoint=/bin/bash', ad_image,
            '-c', 'exec sleep infinity')
        created = True; owned(ad)
        password = secrets.token_urlsafe(24) + '!aA9'; secret_values.append(password)
        provision = (ROOT / 'scripts/ops/fixtures/ldaps/provision.py').read_text().replace('"SyntheticPrdAdmin2026!"', repr(password))
        run('docker', 'exec', '-i', ad, 'tee', '/tmp/provision.py', data=provision.encode())
        run('docker', 'exec', '-i', ad, 'tee', '/tmp/start.sh', data=(ROOT / 'scripts/ops/fixtures/ldaps/start.sh').read_bytes())
        run('docker', 'exec', ad, 'rm', '-f', '/etc/samba/smb.conf')
        current_stage = 'provisioning_ad'
        run('docker', 'exec', '-e', 'WEKNORA_PRD_TEST_RUNTIME=1', ad, 'python3', '/tmp/provision.py', timeout=180)
        run('docker', 'exec', '-d', '-e', 'WEKNORA_PRD_TEST_RUNTIME=1', ad, 'bash', '/tmp/start.sh')
        for _ in range(90):
            ready = run('docker', 'exec', ad, 'ldapsearch', '-x', '-H', 'ldaps://prd-ad', '-D',
                'Administrator@PRD.EXAMPLE.TEST', '-w', password, '-b', 'DC=prd,DC=example,DC=test',
                '-s', 'base', '-o', 'TLS_CACERT=/var/lib/samba/private/tls/ca.pem', 'dn', check=False, timeout=10)
            if ready.returncode == 0: break
            time.sleep(1)
        else: raise RuntimeError('Owned trusted LDAPS did not become ready')
        current_stage = 'configuring_nc_ldap'
        with tempfile.TemporaryDirectory(prefix='manager-trusted-gate-') as scratch:
            private = Path(scratch).resolve(); private.chmod(0o700)
            ca = private / 'ca.crt'
            run('docker', 'cp', ad + ':/var/lib/samba/private/tls/ca.pem', str(ca))
            run('docker', 'cp', str(ca), args.nc + ':/usr/local/share/ca-certificates/manager-gate-ad.crt')
            run('docker', 'exec', args.nc, 'update-ca-certificates')
            publisher_password = secrets.token_urlsafe(24) + '!aA9'; secret_values.append(publisher_password)
            run('docker', 'exec', ad, 'samba-tool', 'user', 'create', 'gate-publisher', publisher_password)
            occ('app:enable', 'user_ldap')
            output = occ('ldap:create-empty-config'); match = re.search(r"configID ['\"]?(s[0-9]+)", output)
            if not match: raise RuntimeError('LDAP configuration ID missing')
            config_id = match.group(1)
            settings = {
                'ldapHost': 'ldaps://prd-ad', 'ldapPort': '636',
                'ldapAgentName': 'Administrator@PRD.EXAMPLE.TEST', 'ldapAgentPassword': password,
                'ldapBase': 'DC=prd,DC=example,DC=test', 'ldapBaseUsers': 'CN=Users,DC=prd,DC=example,DC=test',
                'ldapBaseGroups': 'CN=Users,DC=prd,DC=example,DC=test',
                'ldapUserFilter': '(&(objectClass=user)(!(objectClass=computer)))',
                'ldapLoginFilter': '(&(objectClass=user)(sAMAccountName=%uid))',
                'ldapGroupFilter': '(objectClass=group)', 'ldapGroupMemberAssocAttr': 'member',
                'ldapUserDisplayName': 'sAMAccountName', 'ldapGroupDisplayName': 'cn',
                'ldapExpertUsernameAttr': 'sAMAccountName', 'ldapExpertUUIDUserAttr': 'objectGUID',
                'ldapExpertUUIDGroupAttr': 'objectGUID', 'ldapNestedGroups': '1',
                'ldapCacheTTL': '0', 'ldapConnectionTimeout': '3', 'ldapTLS': '0',
                'turnOffCertCheck': '0', 'ldapConfigurationActive': '1',
            }
            for key, value in settings.items(): occ('ldap:set-config', config_id, key, value)
            occ('ldap:test-config', config_id)
            users = json.loads(occ('user:list', '--output=json'))
            assert 'gate-publisher' in users
            occ('group:adduser', 'admin', 'gate-publisher')
            occ('config:app:set', 'integration_weknora', 'ad_directory_id', '--value=manager-disposable-ad')
            run('docker', 'exec', '-i', args.nc, 'tee', '/tmp/manager-gate-probe.php', data=PHP.encode())
            assert php('missing-gate')['missing_gate_manager_metadata_denied'] is True
            setup = php('setup'); assert setup['live_ldap_admin'] is True
            stages.extend(['missing_gate_manager_path_denied', 'trusted_ldaps_current_admin_and_source_owner'])
            print(json.dumps({'trusted_gate_progress': stages[-1]}), flush=True)

            key = private / 'key'; key.write_bytes(secrets.token_bytes(32)); key.chmod(0o600)
            log = private / 'journal'
            first = json.loads(occ('integration_weknora:export-recovery-ledger', '--after=0'))
            assert not first['has_more']
            anchor = journal.append(log, key, first)
            # A genuine externally retained current checkpoint, with no rollback,
            # is sufficient to initialize closure while keeping all sources stopped.
            plan = journal.replay_plan(log, key, anchor, anchor)
            run('docker', 'exec', args.nc, 'mkdir', '-m', '700', '/tmp/manager-private-recovery')
            run('docker', 'exec', args.nc, 'chown', '33:33', '/tmp/manager-private-recovery')
            remote = '/tmp/manager-private-recovery/'
            # docker cp cannot reliably address tmpfs destinations on all Docker
            # Desktop versions. Write stdin as the config owner, without a shell.
            run('docker', 'exec', '-i', '-u', '33:33', args.nc, 'tee', remote + 'key', data=key.read_bytes())
            nc_exec('chmod', '600', remote + 'key')

            def write_plan(name, value, bad_hmac=False):
                raw = journal.canonical(value)
                envelope = {'plan': raw.decode('ascii'), 'hmac_sha256': '0' * 64 if bad_hmac else hmac.new(key.read_bytes(), raw, hashlib.sha256).hexdigest()}
                path = private / name; path.write_text(json.dumps(envelope, separators=(',', ':'))); path.chmod(0o600)
                run('docker', 'exec', '-i', '-u', '33:33', args.nc, 'tee', remote + name, data=path.read_bytes())
                nc_exec('chmod', '600', remote + name)

            def cli(name, *values, check=True):
                return nc_exec('php', 'custom_apps/integration_weknora/appinfo/recovery-console.php',
                    '--plan=' + remote + name, '--key-file=' + remote + 'key', *values, check=check)

            def denied_unchanged(name, *values):
                before = php('state')
                result = cli(name, *values, check=False)
                assert result.returncode != 0, 'Invalid recovery input accepted'
                assert php('state') == before, 'Denied recovery command changed business control metadata'

            write_plan('closure', plan); write_plan('bad-hmac', plan, True)
            occ('maintenance:mode', '--on')
            current_stage = 'signed_closure'
            denied_unchanged('bad-hmac')
            assert php('state')['gates'] == []
            result = json.loads(cli('closure').stdout)
            assert result['closed_bindings'] == 1 and result['ingress_reopen_permitted'] is False
            state = php('state'); assert len(state['gates']) == 1 and state['gates'][0]['closed'] in (True, 1, '1')
            assert state['bindings'][0]['publication_state'] == 'stopped'
            original_sha = state['gates'][0]['plan_sha256']
            stages.extend(['bad_hmac_no_business_metadata_mutation', 'signed_external_checkpoint_creates_closed_gate'])

            tail_page = json.loads(cli('closure', '--export-after=' + plan['through_sequence']).stdout)
            assert not tail_page['has_more']
            tail_pin = journal.append(log, key, tail_page)
            tail = journal.replay_plan(log, key, anchor, tail_pin)
            write_plan('tail', tail)
            state = php('state'); inventory = state['inventory']['sha256']
            opts = ('--reconcile-controls', '--control-plan-sha256=' + original_sha,
                '--control-inventory-sha256=' + inventory, '--actor=gate-publisher')
            current_stage = 'negative_reconcile'
            wrong_tail = dict(tail); wrong_tail['through_database_chain_sha256'] = 'f' * 64
            write_plan('wrong-tail', wrong_tail); denied_unchanged('wrong-tail', *opts)
            denied_unchanged('closure', *opts)
            denied_unchanged('tail', *(('--control-inventory-sha256=' + 'f' * 64) if x.startswith('--control-inventory-sha256=') else x for x in opts))
            denied_unchanged('tail', *(('--control-plan-sha256=' + 'f' * 64) if x.startswith('--control-plan-sha256=') else x for x in opts))
            nc_exec('chmod', '644', remote + 'tail')
            denied_unchanged('tail', *opts)
            nc_exec('chmod', '600', remote + 'tail')
            denied_unchanged('tail', *(('--actor=auto-prd-admin') if x.startswith('--actor=') else x for x in opts))
            run('docker', 'exec', ad, 'samba-tool', 'user', 'disable', 'gate-publisher')
            denied_unchanged('tail', *opts)
            run('docker', 'exec', ad, 'samba-tool', 'user', 'enable', 'gate-publisher')
            stages.extend(['wrong_authenticated_tail_no_business_metadata_mutation', 'wrong_inventory_no_business_metadata_mutation',
                'stale_authenticated_tail_no_business_metadata_mutation', 'wrong_original_plan_no_business_metadata_mutation',
                'non_private_plan_no_business_metadata_mutation',
                'local_admin_without_ldap_no_business_metadata_mutation', 'live_disabled_ad_admin_no_business_metadata_mutation'])
            assert php('state')['inventory']['sha256'] == inventory
            current_stage = 'positive_reconcile'
            reconciled = json.loads(cli('tail', *opts).stdout)
            assert reconciled['controls_reconciled'] is True and reconciled['sources_remain_stopped'] is True
            assert reconciled['ingress_reopen_permitted'] is False
            assert reconciled['automation_credentials_reauthorization_required'] is True
            assert len(reconciled['sources']) == 1 and reconciled['sources'][0]['files'] == 1
            final = php('state')
            assert final['gates'][0]['closed'] in (False, 0, '0')
            assert final['bindings'][0]['publication_state'] == 'stopped'
            assert final['bindings'] == state['bindings']
            for table, captured in state['inventory']['tables'].items():
                if table != 'weknora_control_gate':
                    assert final['inventory']['tables'][table] == captured, 'Reconciliation changed unrelated control metadata'
            stages.append('signed_latest_tail_live_ldap_current_inventory_opens_global_only')
            occ('maintenance:mode', '--off')
            print(json.dumps({'trusted_gate_probe': 'PASS', 'stages': stages, 'live_ldaps': True,
                'development_identity_bypass': False, 'sources_remain_stopped': True,
                'paired_weknora_acceptance': False, 'no_host_ports': True, 'no_external_route': True,
                'checkpoint_record_sha256': anchor, 'latest_record_sha256': tail_pin,
                'original_control_plan_sha256': original_sha, 'pinned_control_inventory_sha256': inventory,
                'production_modified': False}, sort_keys=True), flush=True)
    except Exception:
        print(json.dumps({'trusted_gate_probe': 'FAIL', 'stage': current_stage}), flush=True)
        raise
    finally:
        if created:
            owned(ad)
            run('docker', 'rm', '-fv', ad)
            print(json.dumps({'owned_ad_cleanup': 'PASS'}), flush=True)


if __name__ == '__main__': main()
