<?php
declare(strict_types=1);

// Runs through the installed user_ldap provider against the owned LDAPS AD DC.
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime opt-in required'); }
$mode = getenv('WEKNORA_PRD_LDAP_MODE') ?: 'allow';
define('OC_CONSOLE', 1);
if ($mode === 'bad_certificate') {
    ldap_set_option(null, LDAP_OPT_X_TLS_CACERTFILE, '/tmp/prd-untrusted-ca.crt');
    ldap_set_option(null, LDAP_OPT_X_TLS_REQUIRE_CERT, LDAP_OPT_X_TLS_DEMAND);
}
require_once '/var/www/html/lib/base.php';
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, get_class($e) . ': ' . $e->getMessage() . "\n"); exit(1); });
\OC_App::loadApp('user_ldap');
new \OCA\IntegrationWeknora\AppInfo\Application();

use OCA\IntegrationWeknora\Service\IdentityMappingService;
use OCA\IntegrationWeknora\Service\LiveLdapGuidVerifier;
use OCP\IUserManager;
use OCP\Server;

$users = Server::get(IUserManager::class);
try {
    $alice = $users->get('prd-alice');
    $bob = $users->get('prd-bob');
} catch (\OC\ServerNotAvailableException $e) {
    if (!in_array($mode, ['offline', 'bad_certificate'], true)) { throw $e; }
    echo "Real LDAPS {$mode}: user_ldap rejected directory and access closed\n"; exit(0);
}
if ($alice === null || $bob === null || $alice->getBackendClassName() !== 'LDAP') {
    throw new RuntimeException('Actual LDAP fixture users are unavailable');
}
$ldap = Server::get(LiveLdapGuidVerifier::class);
if (in_array($mode, ['offline', 'bad_certificate'], true)) {
    try { $ldap->identity($alice); }
    catch (Throwable $e) { echo "Real LDAPS {$mode}: identity unavailable and access closed\n"; exit(0); }
    throw new RuntimeException('Unavailable or untrusted directory was accepted');
}
if ($mode === 'disabled') {
    if ($ldap->identity($alice) !== null) { throw new RuntimeException('Disabled AD account accepted'); }
    echo "Real LDAPS disabled account denied\n"; exit(0);
}
$identity = Server::get(IdentityMappingService::class)->forUser('prd-alice');
$bobIdentity = Server::get(IdentityMappingService::class)->forUser('prd-bob');
$group = $ldap->groupIdentity('PrdEngineering', $alice);
if ($identity['directory_id'] !== 'prd-samba-ad' || $group['directory_id'] !== 'prd-samba-ad') {
    throw new RuntimeException('Trusted directory identity mismatch');
}
$allowed = $ldap->isEffectiveGroupMember($alice, 'PrdEngineering', $identity['directory_id'],
    $identity['object_guid'], $group['object_guid']);
if ($allowed !== ($mode !== 'revoked')) { throw new RuntimeException('Current effective group membership mismatch'); }
if ($ldap->isEffectiveGroupMember($bob, 'PrdEngineering', $bobIdentity['directory_id'],
    $bobIdentity['object_guid'], $group['object_guid'])) { throw new RuntimeException('Unrelated LDAP user inherited group'); }
if ($ldap->isEffectiveGroupMember($alice, 'PrdEngineering', $identity['directory_id'],
    '00000000-0000-0000-0000-000000000000', $group['object_guid'])) { throw new RuntimeException('Recreated GUID accepted'); }
echo "Real LDAPS {$mode}: stable identities and effective membership verified\n";
