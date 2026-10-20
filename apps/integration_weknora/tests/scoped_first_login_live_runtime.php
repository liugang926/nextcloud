<?php
declare(strict_types=1);

// Run only in the task-owned NC runtime. Private transport never goes to stdout.
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Owned isolated runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
\OC_App::loadApp('user_ldap');
$step = $argv[1] ?? '';
set_exception_handler(static function (Throwable $e) use ($step): void {
    fwrite(STDERR, json_encode(['step'=>$step, 'status'=>'FAILED', 'kind'=>get_class($e)], JSON_THROW_ON_ERROR)."\n");
    exit(1);
});
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\DirectoryGrantSnapshotService;
use OCA\IntegrationWeknora\Service\IdentityMappingService;
use OCA\IntegrationWeknora\Service\SourceDecommissionService;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;

function scopedCheck(bool $condition, string $message): void { if (!$condition) { throw new RuntimeException($message); } }
function scopedWrite(string $path, array $value): void {
    scopedCheck(file_put_contents($path, json_encode($value, JSON_THROW_ON_ERROR), LOCK_EX) !== false, 'Private transport write failed');
    scopedCheck(chmod($path, 0600), 'Private transport permissions failed');
}
$path = getenv('WEKNORA_SCOPED_FIRST_LOGIN_TRANSPORT');
$actor = getenv('WEKNORA_SCOPED_FIRST_LOGIN_ACTOR');
scopedCheck(is_string($path) && preg_match('~/tmp/weknora-scoped-first-login-[0-9a-f]{12}\.json\z~D', $path) === 1, 'Private owned transport path required');
scopedCheck(is_string($actor) && preg_match('/\Aweknora-prd-scoped-first-login-[0-9a-f]{12}\z/D', $actor) === 1, 'Owned actor required');
$origin = 'http://'.$actor.':8089';
putenv('WEKNORA_EVENT_DEV_HTTP=1');
putenv('WEKNORA_EVENT_ALLOWED_ORIGINS='.$origin);
// Explicitly disable the older synthetic-account fixture bypass.
putenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=0');
$c = (new Application())->getContainer();
$config = \OCP\Server::get(\OCP\IConfig::class);
$users = \OCP\Server::get(\OCP\IUserManager::class);
$ownerUID = 'prd-sourceowner'; $readerUID = 'prd-alice';
$owner = null; $reader = null;
if ($step !== 'cleanup') {
    $users->search($ownerUID, 10, 0); $users->search($readerUID, 10, 0);
    foreach ([$ownerUID, $readerUID] as $uid) {
        $user = $users->get($uid);
        scopedCheck($user !== null && $user->isEnabled() && $user->getBackendClassName() === 'LDAP', 'Actual active LDAP account required');
    }
    \OCP\Server::get(\OCP\IUserSession::class)->setUser($users->get($ownerUID));
    $identities = $c->get(IdentityMappingService::class);
    // forUser performs fresh LDAP objectGUID verification in the actual provider.
    $owner = $identities->forUser($ownerUID); $reader = $identities->forUser($readerUID);
    scopedCheck($owner['directory_id'] === $reader['directory_id'], 'Actual directory identities differ');
}
$dirs = $c->get(DirectoryPublicationService::class);
$bindings = $c->get(BindingRegistryService::class);
$pairs = $c->get(SourcePairingRegistryService::class);
$shares = \OCP\Server::get(\OCP\Share\IManager::class);
$home = $step === 'cleanup' ? null : \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($ownerUID);
if ($step === 'prepare') {
    scopedCheck(!file_exists($path), 'Owned fixture already exists');
    $scope = $home->newFolder('scoped-first-login-'.bin2hex(random_bytes(6)));
    $root = $scope->newFolder('Original');
    $share = $shares->createShare($shares->newShare()->setNode($root)
        ->setShareType(\OCP\Share\IShare::TYPE_USER)->setSharedBy($ownerUID)
        ->setSharedWith($readerUID)->setPermissions(\OCP\Constants::PERMISSION_READ));
    $operation = DirectoryPublicationService::uuid();
    $ruleID = 'scoped_login_'.str_replace('-', '', $operation);
    $credentialID = $ruleID.'_77'; $token = bin2hex(random_bytes(32));
    $f = ['Actor'=>$actor, 'Origin'=>$origin, 'RuleID'=>$ruleID, 'ScopeID'=>$scope->getId(),
        'Instance'=>$config->getSystemValueString('instanceid'), 'Directory'=>$owner['directory_id'],
        'OwnerGUID'=>$owner['object_guid'], 'ReaderGUID'=>$reader['object_guid'],
        'OwnerUID'=>$ownerUID, 'ReaderUID'=>$readerUID, 'RootID'=>$root->getId(),
        'ShareID'=>$share->getFullId(), 'Credential'=>$credentialID, 'Token'=>$token,
        'Operation'=>$operation, 'Binding'=>'dir_'.str_replace('-', '', $operation),
        'BaseURL'=>'http://nextcloud'];
    // Persist ownership first, so cleanup can close partial preparation safely.
    scopedWrite($path, $f);
    $credentials = $config->getSystemValue('weknora_onboarding_credentials', []);
    scopedCheck(is_array($credentials) && !isset($credentials[$credentialID]), 'Credential collision');
    $credentials[$credentialID] = ['tenant_id'=>'77', 'weknora_origin'=>$origin, 'token'=>$token];
    $config->setSystemValue('weknora_onboarding_credentials', $credentials);
    $dirs->saveRule(['id'=>$ruleID, 'owner_uid'=>$ownerUID, 'container_file_id'=>$scope->getId(),
        'tenant_id'=>'77', 'processing_template_id'=>'scoped-login-template', 'weknora_origin'=>$origin,
        'base_url'=>'http://nextcloud', 'discovery'=>'direct_children', 'delegates'=>[$ownerUID],
        'auto_enabled'=>false, 'targets'=>[['tenant_id'=>'77', 'processing_template_id'=>'scoped-login-template', 'credential_id'=>$credentialID]]], 0);
    echo json_encode(['step'=>'prepare', 'status'=>'PASS', 'directory'=>$f['Directory'], 'reader_guid'=>$f['ReaderGUID'], 'operation_id'=>$operation, 'root_id'=>$root->getId()], JSON_THROW_ON_ERROR)."\n";
    exit;
}
scopedCheck(is_file($path) && (fileperms($path) & 0077) === 0, 'Private fixture missing');
$f = json_decode(file_get_contents($path), true, 32, JSON_THROW_ON_ERROR);
scopedCheck($f['Actor'] === $actor && $f['Origin'] === $origin && $f['OwnerUID'] === $ownerUID &&
    $f['ReaderUID'] === $readerUID && ($owner === null || $f['OwnerGUID'] === $owner['object_guid']) &&
    ($reader === null || $f['ReaderGUID'] === $reader['object_guid']) && str_starts_with($f['RuleID'], 'scoped_login_') &&
    $f['Binding'] === 'dir_'.str_replace('-', '', $f['Operation']), 'Exact owned fixture scope mismatch');
if ($step !== 'cleanup') {
    $rootMatches = $home->getById($f['RootID']);
    scopedCheck(count($rootMatches) === 1 && $rootMatches[0] instanceof \OCP\Files\Folder, 'Owned root unavailable');
    $root = $rootMatches[0];
}
if ($step === 'commit') {
    $result = $dirs->associate($ownerUID, $root->getId(), ['operation_id'=>$f['Operation'],
        'mode'=>'create', 'name'=>'Owned scoped first login source', 'tenant_id'=>'77', 'processing_template_id'=>'scoped-login-template']);
    // Only actual durable retry may recover a lost ACK. No handcrafted RPC,
    // SQL-linked identity or direct synthetic ledger insertion is permitted.
    for ($attempt=0; ($result['state'] ?? '') !== 'active' && $attempt < 2; $attempt++) {
        $result = $dirs->retry($ownerUID, $f['Operation']);
    }
    scopedCheck(($result['state'] ?? '') === 'active', 'Actual onboarding/pair/grants not active');
    $pair = $pairs->status($f['Binding']);
    scopedCheck(is_array($pair) && $pair['state'] === 'active' && $pair['operation_id'] === $f['Operation'] &&
        $pair['tenant_id'] === '77' && $pair['data_source_id'] !== '' && $pair['knowledge_base_id'] !== '', 'Actual pair scope mismatch');
    $audience = $c->get(DirectoryGrantSnapshotService::class)->snapshot($root, $ownerUID, $ownerUID);
    $readerGranted = false;
    foreach ($audience as $subject) { if ($subject['kind'] === 'user' && $subject['directory_id'] === $f['Directory'] && $subject['object_guid'] === $f['ReaderGUID']) { $readerGranted = true; } }
    scopedCheck($readerGranted, 'Actual pre-login direct READ audience missing');
    $body = str_repeat("SCOPED_FIRST_LOGIN_ORIGINAL. This original belongs to the task-owned source. Its direct LDAP reader may preview it only while the Nextcloud READ share remains active.\n", 4);
    $file = $root->nodeExists('source.md') ? $root->get('source.md') : $root->newFile('source.md', $body);
    scopedCheck($file instanceof \OCP\Files\File && hash('sha256', $file->getContent()) === hash('sha256', $body), 'Actual original mismatch');
    $f['KnowledgeBaseID'] = $pair['knowledge_base_id']; $f['DataSourceID'] = $pair['data_source_id'];
    $f['FileID'] = $file->getId(); $f['FileSHA256'] = hash('sha256', $file->getContent());
    scopedWrite($path, $f);
    echo json_encode(['step'=>'commit', 'status'=>'PASS', 'operation_id'=>$f['Operation'], 'knowledge_base_id'=>$f['KnowledgeBaseID'], 'file_id'=>$f['FileID'], 'body_sha256'=>$f['FileSHA256'], 'actual_prelogin_read_audience'=>true], JSON_THROW_ON_ERROR)."\n";
    exit;
}
if ($step === 'revoke') {
    $shares->deleteShare($shares->getShareById($f['ShareID']));
    $f['ShareRevoked'] = true; scopedWrite($path, $f);
    $result = $dirs->retry($ownerUID, $f['Operation']);
    scopedCheck(($result['state'] ?? '') === 'active', 'Actual complete revoked grant publication failed');
    foreach ($c->get(DirectoryGrantSnapshotService::class)->snapshot($root, $ownerUID, $ownerUID) as $subject) {
        scopedCheck(!($subject['kind'] === 'user' && $subject['directory_id'] === $f['Directory'] && $subject['object_guid'] === $f['ReaderGUID']), 'Revoked reader remains in actual audience');
    }
    scopedCheck(hash('sha256', $root->get('source.md')->getContent()) === $f['FileSHA256'], 'Revocation changed the original');
    echo json_encode(['step'=>'revoke', 'status'=>'PASS', 'operation_id'=>$f['Operation'], 'actual_complete_grants_after_read_revoke'=>true, 'body_sha256'=>$f['FileSHA256']], JSON_THROW_ON_ERROR)."\n";
    exit;
}
if ($step === 'cleanup') {
    $pair = $pairs->status($f['Binding']);
    if ($pair !== null) {
        scopedCheck($pair['operation_id'] === $f['Operation'], 'Cleanup pair ownership mismatch');
        if ($pair['state'] === 'pending') { $pairs->abort($f['Binding'], $f['Operation']); }
        elseif ($pair['state'] === 'active') {
            // Persist a real withdrawal intent before dropping the temporary
            // onboarding credential. This prevents cron from reopening it.
            $c->get(SourceDecommissionService::class)->begin($f['Binding'], DirectoryPublicationService::uuid(), $ownerUID);
        }
        $bindings->setPublicationState($f['Binding'], 'stopped', $ownerUID);
    }
    foreach ($dirs->rules() as $rule) {
        if ($rule['id'] !== $f['RuleID']) { continue; }
        scopedCheck($rule['owner_uid'] === $ownerUID && $rule['container_file_id'] === $f['ScopeID'] && $rule['weknora_origin'] === $origin, 'Cleanup rule ownership mismatch');
        $rule['delegates'] = []; $rule['auto_enabled'] = false;
        $dirs->saveRule($rule, $rule['revision']);
    }
    if (!($f['ShareRevoked'] ?? false)) {
        $share = $shares->getShareById($f['ShareID']);
        scopedCheck($share->getNodeId() === $f['RootID'] && $share->getSharedBy() === $ownerUID && $share->getSharedWith() === $readerUID, 'Cleanup share ownership mismatch');
        $shares->deleteShare($share);
    }
    $credentials = $config->getSystemValue('weknora_onboarding_credentials', []);
    if (isset($credentials[$f['Credential']])) {
        scopedCheck($credentials[$f['Credential']]['weknora_origin'] === $origin && hash_equals($f['Token'], $credentials[$f['Credential']]['token']), 'Cleanup credential ownership mismatch');
        unset($credentials[$f['Credential']]); $config->setSystemValue('weknora_onboarding_credentials', $credentials);
    }
    scopedCheck(unlink($path), 'Private transport cleanup failed');
    echo json_encode(['step'=>'cleanup', 'status'=>'PASS', 'operation_id'=>$f['Operation'], 'publication_closed'=>true, 'original_retained'=>true], JSON_THROW_ON_ERROR)."\n";
    exit;
}
throw new InvalidArgumentException('Unsupported fixture step');
