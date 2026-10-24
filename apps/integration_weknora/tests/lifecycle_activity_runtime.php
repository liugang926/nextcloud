<?php

declare(strict_types=1);

if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') {
    throw new RuntimeException('Disposable runtime required');
}

define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
\OC_App::loadApp('integration_weknora');
set_exception_handler(static function (Throwable $exception): void {
    fwrite(STDERR, (string)$exception . "\n");
    exit(1);
});

use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\ActivityService;
use OCA\IntegrationWeknora\Service\ApprovedOriginPolicy;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\ChangeOutboxService;
use OCA\IntegrationWeknora\Service\ConnectionService;
use OCA\IntegrationWeknora\Service\DeletionTombstoneService;
use OCA\IntegrationWeknora\Service\EmployeeFileStatusService;
use OCA\IntegrationWeknora\Service\EventConnectionService;
use OCA\IntegrationWeknora\Service\FilePublicationStateService;
use OCA\IntegrationWeknora\Service\ScopedCredentialStore;
use OCA\IntegrationWeknora\Service\SourceContentRevisionService;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;
use OCA\IntegrationWeknora\Service\StatusProjectionService;
use OCP\IConfig;
use OCP\IDBConnection;
use OCP\IUserManager;
use OCP\Security\ICrypto;

function checkLifecycle(bool $condition, string $message): void {
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

$services = (new Application())->getContainer();
$config = $services->get(IConfig::class);
$crypto = $services->get(ICrypto::class);
$db = $services->get(IDBConnection::class);
$uid = (string)(getenv('WEKNORA_TEST_USER') ?: '');
if ($uid === '') {
    $users = $services->get(IUserManager::class);
    foreach (['devadmin', 'auto-prd-admin', 'admin'] as $candidate) {
        if ($users->get($candidate) !== null) {
            $uid = $candidate;
            break;
        }
    }
    $candidates = $users->search('', 1);
    $candidate = $candidates[0] ?? null;
    if ($uid === '' && $candidate !== null) {
        $uid = $candidate->getUID();
    }
    if ($uid === '') {
        throw new RuntimeException('Runtime test requires an enabled local account');
    }
}
$home = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid);
$root = $home->newFolder('lifecycle-test-' . bin2hex(random_bytes(4)));
$file = $root->newFile('activity.txt', 'current source bytes');
$bindingId = 'lifecycle_' . bin2hex(random_bytes(8));
$bindings = $services->get(BindingRegistryService::class);
$bindings->save($bindingId, 'Lifecycle activity', $uid, $root->getId());

$revisions = $services->get(SourceContentRevisionService::class);
$revisions->begin($file->getId(), [$bindingId]);
$revisions->complete($file->getId(), [$bindingId]);
$revision = $revisions->current($file->getId());

$pairs = $services->get(SourcePairingRegistryService::class);
$operationId = 'aaaaaaaa-0000-4000-8000-' . bin2hex(random_bytes(6));
$prepared = $pairs->prepare($bindingId, $operationId, '10000',
    'lifecycle-kb-' . bin2hex(random_bytes(6)), $uid,
    'Lifecycle Space', 'Lifecycle Knowledge Base');
checkLifecycle($prepared['pairing']['tenant_name'] === 'Lifecycle Space' &&
    $prepared['pairing']['knowledge_base_name'] === 'Lifecycle Knowledge Base',
    'Pair display names were not persisted');
$fileStatus = $services->get(EmployeeFileStatusService::class)->forUser($uid, $file->getId(), false);
checkLifecycle(is_array($fileStatus) &&
    $fileStatus['space_name'] === 'Lifecycle Space' &&
    $fileStatus['knowledge_base_name'] === 'Lifecycle Knowledge Base',
    'File status did not inherit space and knowledge base display names');
$instanceId = $config->getSystemValueString('instanceid');
$dataSourceId = 'lifecycle-ds-' . bin2hex(random_bytes(6));
$pairs->commit($bindingId, $prepared['pairing']['key_id'], $operationId, $instanceId,
    '10000', $prepared['pairing']['knowledge_base_id'], $dataSourceId);
putenv('WEKNORA_EVENT_DEV_HTTP=1');
putenv('WEKNORA_EVENT_ALLOWED_ORIGINS=http://test-weknora');
$connectionId = 'lifecycle_connection_' . bin2hex(random_bytes(8));
$keyId = 'lifecycle_key_' . bin2hex(random_bytes(4));
$secret = bin2hex(random_bytes(32));
$connections = $services->get(EventConnectionService::class);
$connections->configure(
    $bindingId, $bindingId, $instanceId, $connectionId, $keyId, $secret,
    'http://test-weknora/api/v1/integrations/nextcloud/events');
$paused = $connections->row($bindingId);
$pausedUpdate = $db->getQueryBuilder();
$pausedUpdate->update('weknora_event_conn')
    ->set('status', $pausedUpdate->createNamedParameter('paused'))
    ->set('attempt_count', $pausedUpdate->createNamedParameter(1))
    ->set('last_error_code', $pausedUpdate->createNamedParameter('receiver_unauthorized'))
    ->where($pausedUpdate->expr()->eq('binding_id',
        $pausedUpdate->createNamedParameter($bindingId)))
    ->executeStatement();
$resumed = $connections->retryPaused($bindingId, $connectionId, $keyId,
    (string)$paused['received_id']);
checkLifecycle($resumed['status'] === 'active' &&
    (string)$resumed['received_through_event_id'] === (string)$paused['received_id'],
    'Exact association retry did not resume a paused sender without losing its cursor');

$tombstones = $services->get(DeletionTombstoneService::class);
$tombstones->record($bindingId, $file->getId(), $file->getEtag(), $revision, 'source_deleted');
checkLifecycle($tombstones->blocks($bindingId, $file->getId(), $revision, $file->getEtag()),
    'Exact deleted source revision was not fenced');
checkLifecycle(!$tombstones->blocks($bindingId, $file->getId(),
    (string)((int)$revision + 1), 'new-etag'),
    'A newer completed source generation remained permanently blocked');
$status = [
    'schema_version' => 2, 'qa_available' => false,
    'source_content_revision' => (string)$revision,
    'event_id' => 'evt_' . bin2hex(random_bytes(8)),
    'connection_id' => $connectionId, 'instance_id' => $instanceId,
    'binding_id' => $bindingId, 'pair_operation_id' => $operationId,
    'tenant_id' => '10000', 'knowledge_base_id' => $prepared['pairing']['knowledge_base_id'],
    'data_source_id' => $dataSourceId, 'source_etag' => $file->getEtag(),
    'file_id' => (string)$file->getId(), 'publication_epoch' => '0',
    'head_revision' => '1', 'status_revision' => '1',
    'build_attempt' => '1', 'processing_config_revision' => '1',
    'knowledge_id' => 'knowledge-deleted', 'sync_state' => 'withdrawn',
    'stages' => [
        'parse' => ['state' => 'skipped', 'started_at' => null, 'completed_at' => null],
        'chunk' => ['state' => 'skipped', 'started_at' => null, 'completed_at' => null],
        'embedding' => ['state' => 'skipped', 'started_at' => null, 'completed_at' => null],
        'index' => ['state' => 'skipped', 'started_at' => null, 'completed_at' => null],
    ],
    'graph' => ['state' => 'not_enabled', 'enabled' => false, 'required' => false,
        'persisted' => false, 'expected' => 0, 'succeeded' => 0, 'failed' => 0,
        'skipped' => 0, 'cancelled' => 0, 'pending' => 0, 'completed_at' => null,
        'error_code' => null],
    'publication_state' => 'withdrawn', 'published_source_etag' => null,
    'occurred_at' => time(), 'last_verified_at' => time(),
    'last_permission_sync_at' => time(), 'permission_revision' => 'permission-closed',
    'withdrawal_state' => 'withdrawn', 'permission_reclaim_state' => 'access_closed',
    'gc_state' => 'running', 'error_code' => null,
];
$accepted = $services->get(StatusProjectionService::class)->accept(
    json_encode($status, JSON_THROW_ON_ERROR));
checkLifecycle(($accepted['cleanup_state'] ?? null) === 'running' &&
    $tombstones->find($bindingId, $file->getId())['cleanup_state'] === 'running',
    'Deletion-only signed cleanup status was not retained');
$tombstones->updateCleanup($bindingId, $file->getId(), 'verified', 2);
try {
    $tombstones->updateCleanup($bindingId, $file->getId(), 'running', 1);
    throw new RuntimeException('Stale cleanup revision overwrote verified state');
} catch (DomainException) {
}
$cleanup = $tombstones->find($bindingId, $file->getId());
checkLifecycle($cleanup['cleanup_state'] === 'verified' &&
    (int)$cleanup['cleanup_revision'] === 2,
    'Verified cleanup proof was not monotonic');

$outbox = $services->get(ChangeOutboxService::class);
$outbox->append($bindingId, $file->getId(), 'upsert', null, $file->getPath(),
    $file->getEtag(), $file->getName());
$services->get(FilePublicationStateService::class)->withdraw($bindingId, $file->getId(), $uid);
$activity = $services->get(ActivityService::class);
$first = $activity->forScope($uid, $bindingId, null, null, 1);
checkLifecycle(count($first['items']) === 1 && is_string($first['next_cursor']),
    'Activity first page did not paginate authoritatively');
$second = $activity->forScope($uid, $bindingId, null, $first['next_cursor'], 10);
checkLifecycle(count($second['items']) >= 2,
    'Activity cursor did not return the remaining lifecycle events');
$titles = array_column(array_merge($first['items'], $second['items']), 'title');
checkLifecycle(in_array('文件已从当前发布集合撤回', $titles, true) &&
    in_array('已确认源文件删除', $titles, true),
    'Withdrawal or deletion tombstone missing from activity');
$deletedFile = $root->newFile('listener-delete.txt', 'deleted source');
$revisions->begin($deletedFile->getId(), [$bindingId]);
$revisions->complete($deletedFile->getId(), [$bindingId]);
$deletedId = $deletedFile->getId();
$deletedFile->delete();
checkLifecycle($tombstones->find($bindingId, $deletedId) !== null,
    'Confirmed file deletion did not create a durable listener tombstone');
try {
    $activity->forScope($uid, $bindingId, null,
        substr($first['next_cursor'], 0, -1) . 'x', 10);
    throw new RuntimeException('Forged activity cursor was accepted');
} catch (InvalidArgumentException) {
}

$originalCredentials = $config->getSystemValue('weknora_onboarding_credentials', null);
$store = new ScopedCredentialStore($config, $crypto);
$legacy = "legacy-secret-" . bin2hex(random_bytes(24));
$config->setSystemValue('weknora_onboarding_credentials', [
    'legacy' => ['tenant_id' => '1', 'weknora_origin' => 'https://approved.example', 'token' => $legacy],
]);
checkLifecycle($store->migrateLegacy() === 1, 'Legacy scoped credential was not migrated');
$stored = $config->getSystemValue('weknora_onboarding_credentials', []);
checkLifecycle(is_string($stored['legacy']['token'] ?? null) &&
    str_starts_with($stored['legacy']['token'], 'enc:v1:') &&
    !str_contains((string)$stored['legacy']['token'], $legacy),
    'Scoped credential remained plaintext');
checkLifecycle($store->token('legacy') === $legacy,
    'Encrypted scoped credential did not decrypt');
if (is_array($originalCredentials)) {
    $config->setSystemValue('weknora_onboarding_credentials', $originalCredentials);
} else {
    $config->deleteSystemValue('weknora_onboarding_credentials');
}
$connectionProfile = [
    'origin' => 'http://test-weknora', 'base_url' => 'http://nextcloud.test',
    'connection_id' => 'lifecycle-connection', 'connected_at' => time(),
    'contract_version' => 3, 'web_url' => 'http://test-weknora/login',
    'web_url_source' => 'connection_contract',
    'spaces' => [[
        'tenant_id' => '10000', 'name' => 'Lifecycle Space',
        'processing_template_id' => 'template-1',
        'processing_template_name' => 'Lifecycle Template',
        'credential_id' => 'lifecycle-credential',
        'visibility' => ['mode' => 'all', 'groups' => [], 'users' => []],
    ]],
];
$config->setAppValue('integration_weknora', 'connection',
    json_encode($connectionProfile, JSON_THROW_ON_ERROR));
$config->setAppValue('integration_weknora', 'connection_health', json_encode([
    'status' => 'degraded', 'binding_allowed' => false,
    'reason_code' => 'worker_unverified', 'checked_at' => time(),
], JSON_THROW_ON_ERROR));
$services->get(ConnectionService::class)->updateVisibility([[
    'tenant_id' => '10000',
    'visibility' => ['mode' => 'admin', 'groups' => [], 'users' => []],
]], $uid);
$connectionStatus = $services->get(ConnectionService::class)->status();
checkLifecycle($connectionStatus['credentials_encrypted'] === true &&
    $connectionStatus['origin_approved'] === true &&
    $connectionStatus['web_url'] === 'http://test-weknora/login' &&
    $connectionStatus['spaces'][0]['visibility']['mode'] === 'admin',
    'Connection status did not preserve encrypted credentials, approved Web URL or visibility');
$staleStore = new ScopedCredentialStore($config, $crypto);
$staleStore->replace([
    'stale-test' => [
        'tenant_id' => '10000',
        'weknora_origin' => 'http://127.0.0.1:1',
        'token' => 'stale-credential-' . bin2hex(random_bytes(24)),
        'stale' => true,
    ],
]);
$cleanupStatus = $services->get(ConnectionService::class)->cleanupStaleCredentials($uid);
checkLifecycle($cleanupStatus['stale_credential_count'] === 1,
    'Failed stale credential cleanup was not retained for a safe retry');
if (is_array($originalCredentials)) {
    $config->setSystemValue('weknora_onboarding_credentials', $originalCredentials);
} else {
    $config->deleteSystemValue('weknora_onboarding_credentials');
}
$config->deleteAppValue('integration_weknora', 'connection');
$config->deleteAppValue('integration_weknora', 'connection_health');

putenv('WEKNORA_EVENT_ALLOWED_ORIGINS=https://approved.example');
$origins = $services->get(ApprovedOriginPolicy::class);
checkLifecycle($origins->canonical('https://approved.example', true) ===
    'https://approved.example', 'Approved origin was rejected');
try {
    $origins->canonical('https://not-approved.example', true);
    throw new RuntimeException('Unapproved origin was accepted');
} catch (DomainException) {
}
checkLifecycle($origins->safeWebUrl('https://approved.example/login',
    'https://approved.example') === 'https://approved.example/login',
    'Approved WeKnora web URL was rejected');

$query = $db->getQueryBuilder();
$query->delete('weknora_delete_tomb')
    ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
    ->executeStatement();

echo "Encrypted scoped credentials, exact-origin policy, deletion tombstone generations and activity pagination passed\n";
