import json
import os
import subprocess
import tempfile
from pathlib import Path
if os.environ.get('WEKNORA_PRD_TEST_RUNTIME') != '1':
    raise SystemExit('Disposable test runtime opt-in required')
NC = os.environ.get('NEXTCLOUD_PRD_TEST_CONTAINER', 'nextcloud-auto-space-prd-test-20261010')
AD = os.environ.get('WEKNORA_PRD_AD_CONTAINER', 'weknora-auto-space-ad-20261010')
for name in (NC, AD):
    details = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
    if details['Config'].get('Labels', {}).get('codex.task') != 'auto-space-prd-20261010':
        raise SystemExit('Refusing an unowned runtime container')
    if any(details['HostConfig'].get('PortBindings', {}).values()):
        raise SystemExit('Disposable recipe requires no host-published service ports')
with tempfile.TemporaryDirectory(prefix='weknora-prd-public-ca-') as directory:
    certificate = Path(directory) / 'ca.crt'
    subprocess.run(['docker', 'cp', AD + ':/var/lib/samba/private/tls/ca.pem', str(certificate)], check=True)
    subprocess.run(['docker', 'cp', str(certificate), NC + ':/usr/local/share/ca-certificates/prd-ad.crt'], check=True)
subprocess.run(['docker', 'exec', NC, 'update-ca-certificates'], check=True, stdout=subprocess.DEVNULL)
config = {
    'ldapHost':'ldaps://prd-ad', 'ldapPort':'636',
    'ldapBase':'DC=prd,DC=example,DC=test', 'ldapBaseUsers':'CN=Users,DC=prd,DC=example,DC=test',
    'ldapBaseGroups':'CN=Users,DC=prd,DC=example,DC=test',
    'ldapAgentName':'Administrator@PRD.EXAMPLE.TEST', 'ldapAgentPassword':'SyntheticPrdAdmin2026!',
    'ldapUserFilter':'(&(objectClass=user)(!(objectClass=computer)))',
    'ldapLoginFilter':'(&(objectClass=user)(sAMAccountName=%uid))',
    'ldapGroupFilter':'(objectClass=group)', 'ldapGroupMemberAssocAttr':'member',
    'ldapUserDisplayName':'sAMAccountName', 'ldapGroupDisplayName':'cn',
    'ldapUuidUserAttribute':'objectGUID', 'ldapUuidGroupAttribute':'objectGUID',
    'ldapExpertUsernameAttr':'sAMAccountName', 'ldapExpertUUIDUserAttr':'objectGUID',
    'ldapExpertUUIDGroupAttr':'objectGUID', 'ldapNestedGroups':'1',
    'ldapCacheTTL':'0', 'ldapConnectionTimeout':'5', 'ldapTLS':'0', 'turnOffCertCheck':'0', 'ldapConfigurationActive':'1',
}
for key, value in config.items():
    subprocess.run(['docker', 'exec', '--user', 'www-data', NC, 'php', 'occ', 'ldap:set-config', 's01', key, value], check=True, stdout=subprocess.DEVNULL)
subprocess.run(['docker', 'exec', '--user', 'www-data', NC, 'php', 'occ', 'ldap:test-config', 's01'], check=True)
subprocess.run(['docker', 'exec', '--user', 'www-data', NC, 'php', 'occ', 'config:app:set', 'integration_weknora', 'ad_directory_id', '--value=prd-samba-ad'], check=True, stdout=subprocess.DEVNULL)
print('Disposable Nextcloud trusted LDAPS configuration ready')
