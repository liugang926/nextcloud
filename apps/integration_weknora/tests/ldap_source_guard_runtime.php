<?php
declare(strict_types=1);
// Real user_ldap + owned LDAPS AD + real group share and Files mount views.
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
foreach (['WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY', 'WEKNORA_LOCAL_COMPOSE', 'WEKNORA_EVENT_DEV_HTTP'] as $flag) { putenv($flag . '=0'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, get_class($e) . ': ' . $e->getMessage() . "\n"); exit(1); });
\OC_App::loadApp('user_ldap');
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\EmployeeFileStatusService;
use OCA\IntegrationWeknora\Service\FolderSummaryService;
use OCA\IntegrationWeknora\Service\IdentityMappingService;
use OCA\IntegrationWeknora\Service\SourceAuthorizationService;
use OCA\IntegrationWeknora\Service\SourceContentRevisionService;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;
use OCP\Server;
use OCP\Share\IShare;
function ldapSourceCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
$services = (new Application())->getContainer(); $mode = getenv('WEKNORA_PRD_LDAP_SOURCE_MODE') ?: 'allow';
$users = Server::get(\OCP\IUserManager::class); $groups = Server::get(\OCP\IGroupManager::class);
$files = Server::get(\OCP\Files\IRootFolder::class);
$readerUid = getenv('WEKNORA_PRD_LDAP_READER_UID') ?: 'prd-alice';
$deniedUid = getenv('WEKNORA_PRD_LDAP_DENIED_UID') ?: 'prd-bob';
$sourceGroup = getenv('WEKNORA_PRD_LDAP_SOURCE_GROUP') ?: 'PrdSourceRead';
$primaryGroup = getenv('WEKNORA_PRD_LDAP_PRIMARY_GROUP') ?: 'PrdSourcePrimary';
$path = getenv('WEKNORA_PRD_LDAP_FIXTURE_FILE') ?: '/tmp/weknora-prd-source-guard-fixture.json';
if ($mode === 'setup') {
    $ownerUid = 'prd-sourceowner'; $users->search($ownerUid, 10, 0); $owner = $users->get($ownerUid);
    ldapSourceCheck($owner !== null && $owner->getBackendClassName() === 'LDAP', 'Actual source owner is not LDAP');
    $services->get(IdentityMappingService::class)->forUser($ownerUid);
    Server::get(\OCP\IUserSession::class)->setUser($owner);
    $root = $files->getUserFolder($ownerUid)->newFolder('LDAP source proof ' . bin2hex(random_bytes(4)));
    $file = $root->newFile('Protected original.txt', 'Original fixture data remains unchanged across permission changes.');
    $binding = 'ldap_source_' . bin2hex(random_bytes(8));
    $services->get(BindingRegistryService::class)->save($binding, 'LDAPS group source guard', $ownerUid, $root->getId());
    $operation = 'fefefefe-2222-4333-8444-' . bin2hex(random_bytes(6));
    $pairs = $services->get(SourcePairingRegistryService::class);
    $prepared = $pairs->prepare($binding, $operation, '7', 'kb-' . $binding, $ownerUid);
    $instance = Server::get(\OCP\IConfig::class)->getSystemValueString('instanceid');
    $pairs->commit($binding, $prepared['pairing']['key_id'], $operation, $instance, '7', 'kb-' . $binding, 'source-' . $binding);
    $matches = $groups->search($sourceGroup); $gid = null;
    foreach ($matches as $group) { if ($group->getDisplayName() === $sourceGroup) { $gid = $group->getGID(); } }
    ldapSourceCheck(is_string($gid), 'Real LDAP source group is unavailable');
    $manager = Server::get(\OCP\Share\IManager::class);
    $share = $manager->newShare()->setNode($root)->setShareType(IShare::TYPE_GROUP)->setSharedBy($ownerUid)
        ->setSharedWith($gid)->setPermissions(\OCP\Constants::PERMISSION_READ);
    $saved = $manager->createShare($share);
    file_put_contents($path, json_encode(['binding' => $binding, 'owner_uid' => $ownerUid,
        'folder_id' => $root->getId(), 'file_id' => $file->getId(), 'source_etag' => $file->getEtag(),
        'original' => $file->getContent(), 'share_id' => $saved->getFullId(), 'gid' => $gid], JSON_THROW_ON_ERROR));
    $users->search($readerUid, 10, 0); $users->search($deniedUid, 10, 0);
    $aliceIdentity = $services->get(IdentityMappingService::class)->forUser($readerUid);
    $bobIdentity = $services->get(IdentityMappingService::class)->forUser($deniedUid);
    if (getenv('WEKNORA_PRD_ROOT_AUTH_EXPORT') !== '0') {
      file_put_contents('/tmp/weknora-prd-root-auth-fixture.json', json_encode(['binding_id' => $binding,
        'key_id' => $prepared['pairing']['key_id'], 'token' => $prepared['token'], 'instance_id' => $instance,
        'publication_epoch' => (string)$services->get(BindingRegistryService::class)->requirePublicationActive($binding),
        'file_id' => (string)$file->getId(), 'source_etag' => $file->getEtag(),
        'source_content_revision' => $services->get(SourceContentRevisionService::class)->current($file->getId()),
        'directory_id' => $aliceIdentity['directory_id'], 'alice_guid' => $aliceIdentity['object_guid'], 'bob_guid' => $bobIdentity['object_guid'],
        'owner_uid' => $ownerUid, 'nextcloud_container' => 'nextcloud-auto-space-prd-test-20261010'], JSON_THROW_ON_ERROR));
      chmod('/tmp/weknora-prd-root-auth-fixture.json', 0600);
    }
    $mode = 'allow';
}
$fixture = json_decode(file_get_contents($path), true, 16, JSON_THROW_ON_ERROR);
if ($mode === 'primary' && $fixture['gid'] !== $primaryGroup) {
    $owner = $users->get($fixture['owner_uid']); Server::get(\OCP\IUserSession::class)->setUser($owner);
    $manager = Server::get(\OCP\Share\IManager::class);
    $manager->deleteShare($manager->getShareById($fixture['share_id']));
    $gid = null;
    foreach ($groups->search($primaryGroup) as $group) { if ($group->getDisplayName() === $primaryGroup) { $gid = $group->getGID(); } }
    ldapSourceCheck(is_string($gid), 'Actual primary LDAP group is unavailable');
    $root = $files->getUserFolder($fixture['owner_uid'])->getById($fixture['folder_id'])[0];
    $share = $manager->newShare()->setNode($root)->setShareType(IShare::TYPE_GROUP)->setSharedBy($fixture['owner_uid'])
        ->setSharedWith($gid)->setPermissions(\OCP\Constants::PERMISSION_READ);
    $saved = $manager->createShare($share); $fixture['share_id'] = $saved->getFullId(); $fixture['gid'] = $gid;
    file_put_contents($path, json_encode($fixture, JSON_THROW_ON_ERROR));
}
if ($mode === 'unshared') {
    Server::get(\OCP\IUserSession::class)->setUser($users->get($fixture['owner_uid']));
    $manager = Server::get(\OCP\Share\IManager::class);
    $manager->deleteShare($manager->getShareById($fixture['share_id']));
}
$alice = $services->get(IdentityMappingService::class)->forUser($readerUid);
$bob = $services->get(IdentityMappingService::class)->forUser($deniedUid);
$auth = $services->get(SourceAuthorizationService::class);
$employee = $services->get(EmployeeFileStatusService::class); $summary = $services->get(FolderSummaryService::class);
$contentRevision = $services->get(SourceContentRevisionService::class)->current($fixture['file_id']);
$result = $auth->authorize($fixture['binding'], $alice['directory_id'], $alice['object_guid'], $fixture['file_id'], $contentRevision);
$rootResult = $auth->authorizeRoot($fixture['binding'], $alice['directory_id'], $alice['object_guid']);
if ($contentRevision !== '0') {
    ldapSourceCheck(!$auth->authorize($fixture['binding'], $alice['directory_id'], $alice['object_guid'], $fixture['file_id'], '0')['allow'],
        'Legacy raw-ETag request bypassed a positive independent source revision');
}
$status = $employee->forUser($readerUid, $fixture['file_id'], false);
$aggregate = $summary->forUser($readerUid, $fixture['folder_id']);
if (in_array($mode, ['revoked', 'unshared'], true)) {
    ldapSourceCheck(!$result['allow'] && !$rootResult['allow'] && $status === null && ($aggregate === null || $aggregate['summary']['total_visible'] === 0),
        'Revoked LDAP audience retained source authorization, employee status or summary counts');
} else {
    if (!$result['allow'] || !$rootResult['allow']) {
        echo json_encode(['file_allow' => $result['allow'], 'file_reason' => $result['reason'],
            'root_allow' => $rootResult['allow'], 'root_reason' => $rootResult['reason']], JSON_THROW_ON_ERROR) . "\n";
    }
    ldapSourceCheck($result['allow'] && $rootResult['allow'] && $result['source_etag'] === $fixture['source_etag'], 'Actual root/file source authorization denied valid LDAP audience');
    ldapSourceCheck($status !== null && $status['source_state'] === 'in_scope' && !$status['qa_available'], 'Files status lost real LDAP scope or granted QA by metadata');
    ldapSourceCheck($aggregate !== null && $aggregate['summary']['total_visible'] === 1 && $aggregate['summary']['current_completed'] === 0,
        'Source-scoped summary did not filter actual visible current files');
}
ldapSourceCheck(!$auth->authorize($fixture['binding'], $bob['directory_id'], $bob['object_guid'], $fixture['file_id'], $contentRevision)['allow'] &&
    !$auth->authorizeRoot($fixture['binding'], $bob['directory_id'], $bob['object_guid'])['allow'] &&
    $employee->forUser($deniedUid, $fixture['file_id'], false) === null && $summary->forUser($deniedUid, $fixture['folder_id']) === null,
    'Unrelated LDAP user inherited source access or metadata');
$ownerRoot = $files->getUserFolder($fixture['owner_uid'])->getById($fixture['folder_id'])[0];
$original = $ownerRoot->getById($fixture['file_id'])[0];
ldapSourceCheck($original->getContent() === $fixture['original'] && $original->getEtag() === $fixture['source_etag'],
    'Permission checks changed source bytes or ETag');
echo "Real LDAPS {$mode}: group source authorization, employee metadata, visible summary, unrelated user denial and unchanged originals passed\n";
