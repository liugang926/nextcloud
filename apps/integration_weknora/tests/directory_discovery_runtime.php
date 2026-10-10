<?php
declare(strict_types=1);
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
\OC_App::loadApp('user_ldap');
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\DirectoryDiscoveryService;
use OCA\IntegrationWeknora\Service\IdentityMappingService;
function discoveryCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
putenv('WEKNORA_EVENT_DEV_HTTP=1'); putenv('WEKNORA_LOCAL_COMPOSE=1');
putenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=1'); putenv('WEKNORA_EVENT_ALLOWED_ORIGINS=http://test-weknora');
$services = (new Application())->getContainer(); $uid = 'auto-prd-admin';
$services->get(IdentityMappingService::class)->create('prd-directory', '12345678-abcd-1234-abcd-123456789abc', $uid, $uid);
$scope = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid)->newFolder('discovery-test-' . bin2hex(random_bytes(4)));
$directory = $services->get(DirectoryPublicationService::class);
$directory->saveRule(['id' => 'discovery_' . bin2hex(random_bytes(4)), 'owner_uid' => $uid, 'container_file_id' => $scope->getId(),
    'tenant_id' => '7', 'processing_template_id' => 'template-1', 'weknora_origin' => 'http://test-weknora', 'base_url' => 'http://test-nextcloud',
    'discovery' => 'direct_children', 'delegates' => [$uid], 'automation_uid' => $uid, 'auto_enabled' => true], 0);
$folder = $scope->newFolder('DAV-created');
$db = \OCP\Server::get(\OCP\IDBConnection::class);
function discoveryRow($db, int $id): array|false {
    $q = $db->getQueryBuilder(); $r = $q->select('*')->from('weknora_dir_discover')
        ->where($q->expr()->eq('file_id', $q->createNamedParameter($id)))->executeQuery();
    try { return $r->fetchAssociative(); } finally { $r->closeCursor(); }
}
$queued = discoveryRow($db, $folder->getId());
discoveryCheck($queued !== false && $queued['attempt_count'] == 0, 'Successful native mkdir did not persist discovery without remote work');
$discovery = $services->get(DirectoryDiscoveryService::class);
$discovery->enqueue($folder->getId());
discoveryCheck(discoveryRow($db, $folder->getId())['operation_id'] === $queued['operation_id'], 'Repeated node hint changed its operation UUID');
$discovery->deliverDue(100);
$status = $directory->show($uid, $folder->getId());
discoveryCheck($status['operation']['operation_id'] === $queued['operation_id'] && $status['operation']['state'] === 'pending',
    'Discovery did not persist its exact bounded worker intent');
discoveryCheck(discoveryRow($db, $folder->getId()) === false, 'Intent accepted but discovery queue remained duplicated');
$choice = ['operation_id' => 'cccccccc-2222-4333-8444-' . bin2hex(random_bytes(6)), 'mode' => 'existing', 'name' => 'Employee target', 'knowledge_base_id' => 'employee-empty-kb'];
$explicit = $directory->create($uid, $scope->getId(), 'Explicit choice', $choice);
$discovery->deliverDue(100);
discoveryCheck($directory->show($uid, $explicit['folder_id'])['operation']['operation_id'] === $choice['operation_id'],
    'Background default replaced a persisted employee target choice');
echo "Native directory event persistence, duplicate hint UUIDs, bounded worker intents and explicit target precedence passed\n";
