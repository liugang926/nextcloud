<?php

declare(strict_types=1);

// Run only in the disposable Nextcloud test installation. This deliberately
// exercises actual PostgreSQL/Nextcloud services, migrations, locks and source ETags.
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable test runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
\OC_App::loadApp('user_ldap');
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });

use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\EventConnectionService;
use OCA\IntegrationWeknora\Service\FilePublicationStateService;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;
use OCA\IntegrationWeknora\Service\StatusProjectionService;
use OCA\IntegrationWeknora\Service\EmployeeFileStatusService;
use OCA\IntegrationWeknora\Service\StatusProtocol;

function assertRuntime(bool $pass, string $why): void { if (!$pass) { throw new RuntimeException($why); } }
function expectConflict(callable $action): void {
    try { $action(); } catch (DomainException $e) { return; }
    throw new RuntimeException('Expected source/revision conflict');
}

$app = new Application();
$container = $app->getContainer();
$uid = 'auto-prd-admin';
$home = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid);
$root = $home->newFolder('stage-test-' . bin2hex(random_bytes(4)));
$file = $root->newFile('document.txt', 'Original source remains unchanged.');
$binding = 'status_' . bin2hex(random_bytes(8));
$registry = $container->get(BindingRegistryService::class);
$registry->save($binding, 'Status integration test', $uid, $root->getId());
$pairings = $container->get(SourcePairingRegistryService::class);
$operation = 'aaaaaaaa-bbbb-cccc-dddd-' . bin2hex(random_bytes(6));
$prepared = $pairings->prepare($binding, $operation, '7', 'kb-' . $binding, $uid);
$instance = \OCP\Server::get(\OCP\IConfig::class)->getSystemValueString('instanceid');
$pairings->commit($binding, $prepared['pairing']['key_id'], $operation, $instance, '7', 'kb-' . $binding, 'source-' . $binding);
putenv('WEKNORA_EVENT_DEV_HTTP=1');
putenv('WEKNORA_EVENT_ALLOWED_ORIGINS=http://test-weknora');
$connections = $container->get(EventConnectionService::class);
$connection = 'connection-' . $binding;
$connections->configure($binding, $binding, $instance, $connection, 'current',
    'synthetic_connection_secret_0123456789abcdef', 'http://test-weknora/api/v1/integrations/nextcloud/events');
$projection = $container->get(StatusProjectionService::class);
$epoch = $registry->requirePublicationActive($binding);
$etag = $file->getEtag();
$contentRevision = $container->get(\OCA\IntegrationWeknora\Service\SourceContentRevisionService::class)->current($file->getId());
$now = time();
$state = ['schema_version' => 2, 'event_id' => 'event-' . $binding, 'connection_id' => $connection,
    'instance_id' => $instance, 'binding_id' => $binding, 'pair_operation_id' => $operation,
    'tenant_id' => '7', 'knowledge_base_id' => 'kb-' . $binding, 'data_source_id' => 'source-' . $binding,
    'file_id' => (string)$file->getId(), 'source_etag' => $etag, 'source_content_revision' => $contentRevision, 'publication_epoch' => (string)$epoch,
    'head_revision' => '2', 'status_revision' => '5', 'knowledge_id' => 'candidate-1', 'build_attempt' => '1',
    'processing_config_revision' => '1', 'sync_state' => 'ingested',
    'stages' => array_fill_keys(['parse', 'chunk', 'embedding', 'index'],
        ['state' => 'succeeded', 'started_at' => $now - 10, 'completed_at' => $now - 1]),
    'graph' => ['enabled' => true, 'required' => false, 'state' => 'partial_failed', 'expected' => 2,
        'succeeded' => 1, 'failed' => 1, 'skipped' => 0, 'cancelled' => 0, 'pending' => 0,
        'persisted' => false, 'completed_at' => $now - 1, 'error_code' => 'graph_extract_failed'],
    'publication_state' => 'published', 'published_source_etag' => $etag, 'permission_revision' => '1',
    'last_permission_sync_at' => $now - 1, 'occurred_at' => $now - 1, 'last_verified_at' => $now - 1,
    'withdrawal_state' => 'none', 'permission_reclaim_state' => 'none', 'gc_state' => 'cleanup_pending',
    'error_code' => null, 'qa_available' => false];
$raw = json_encode($state, JSON_THROW_ON_ERROR);
assertRuntime($projection->accept($raw)['applied'], 'Terminal-first event not applied');
assertRuntime($projection->accept($raw)['duplicate'], 'Duplicate not persisted idempotently');
$current = $projection->forFile($binding, $file->getId(), $etag, $epoch);
assertRuntime($current['knowledge_state'] === 'ready' && $current['graph']['state'] === 'partial_failed', 'Independent stage result lost');
assertRuntime($file->getContent() === 'Original source remains unchanged.' && $file->getEtag() === $etag,
    'Status changed source bytes or ETag');
$late = array_replace($state, ['event_id' => 'late-' . $binding, 'head_revision' => '1', 'status_revision' => '4']);
assertRuntime(!$projection->accept(json_encode($late, JSON_THROW_ON_ERROR))['applied'], 'Late worker overwrote head');
$snapshot = array_replace($state, ['event_id' => 'snapshot-' . $binding, 'last_verified_at' => $now]);
assertRuntime(!$projection->accept(json_encode($snapshot, JSON_THROW_ON_ERROR), $now)['applied'], 'Same revision verification rewrote state');
assertRuntime($projection->forFile($binding, $file->getId(), $etag, $epoch)['last_verified_at'] === $now, 'Fresh signed snapshot did not refresh proof');
$wrong = array_replace($state, ['event_id' => 'wrong-' . $binding, 'knowledge_base_id' => 'unrelated-kb']);
expectConflict(fn () => $projection->accept(json_encode($wrong, JSON_THROW_ON_ERROR)));
$authenticated = $connections->row($binding);
$connections->configure($binding, $binding, $instance, $connection, 'rotated',
    'synthetic_rotated_connection_secret_0123456789abcdef', 'http://test-weknora/api/v1/integrations/nextcloud/events');
expectConflict(fn () => $projection->accept($raw, null, $authenticated));
$connections->configure($binding, $binding, $instance, $connection, 'current',
    'synthetic_connection_secret_0123456789abcdef', 'http://test-weknora/api/v1/integrations/nextcloud/events');
$employee = $container->get(EmployeeFileStatusService::class);
assertRuntime($employee->forUser($uid, $file->getId(), false)['knowledge_state'] === 'ready', 'Session-visible status failed');
$transport = $root->newFile('transport.txt', 'Synthetic metadata transport test only.');
$transportState = array_replace($state, ['event_id' => 'transport-' . $binding,
    'file_id' => (string)$transport->getId(), 'source_etag' => $transport->getEtag(),
    'source_content_revision' => $container->get(\OCA\IntegrationWeknora\Service\SourceContentRevisionService::class)->current($transport->getId()),
    'published_source_etag' => $transport->getEtag(), 'knowledge_id' => 'candidate-transport']);
file_put_contents('/tmp/weknora-stage-status-fixture.json', json_encode([
    'state' => $transportState, 'key_id' => 'current',
    'secret' => 'synthetic_connection_secret_0123456789abcdef'], JSON_THROW_ON_ERROR));
$revisions = $container->get(\OCA\IntegrationWeknora\Service\SourceContentRevisionService::class);
$revisions->begin($file->getId(), [$binding]);
expectConflict(fn () => $projection->accept($raw));
assertRuntime($employee->forUser($uid, $file->getId(), false)['knowledge_state'] === 'unverified', 'Writing fence left old status ready');
$revisions->complete($file->getId(), [$binding]);
assertRuntime($file->getEtag() === $etag, 'Content revision tests must retain the same ETag');
expectConflict(fn () => $projection->accept($raw));
$rebuilt = array_replace($state, ['event_id' => 'rebuilt-' . $binding, 'head_revision' => '3', 'status_revision' => '6',
    'knowledge_id' => 'candidate-current-revision', 'build_attempt' => '2', 'source_content_revision' => $revisions->current($file->getId())]);
assertRuntime($projection->accept(json_encode($rebuilt, JSON_THROW_ON_ERROR))['applied'], 'New content revision with repeated ETag was not accepted');
assertRuntime($employee->forUser($uid, $file->getId(), false)['knowledge_state'] === 'ready', 'Rebuilt current revision is not ready');
$polls = $container->get(\OCA\IntegrationWeknora\Service\StatusPollQueue::class);
$db = \OCP\Server::get(\OCP\IDBConnection::class);
$lost = $db->getQueryBuilder();
$lost->delete('weknora_status_pending')->where($lost->expr()->eq('binding_id', $lost->createNamedParameter($binding)))
    ->andWhere($lost->expr()->eq('file_id', $lost->createNamedParameter($transport->getId())))->executeStatement();
$polls->discover(time());
$pending = $db->getQueryBuilder();
$result = $pending->select('file_id')->from('weknora_status_pending')->where($pending->expr()->eq('binding_id', $pending->createNamedParameter($binding)))
    ->andWhere($pending->expr()->eq('file_id', $pending->createNamedParameter($transport->getId())))->executeQuery();
try { assertRuntime((int)$result->fetchOne() === $transport->getId(), 'Missing first push was not discoverable independently'); }
finally { $result->closeCursor(); }
$db->beginTransaction();
\OCA\IntegrationWeknora\Service\StatusPollQueue::enqueueInTransaction($db, $binding, $transport->getId());
$db->commit();
$query = $db->getQueryBuilder();
$res = $query->select('*')->from('weknora_status_pending')->where($query->expr()->eq('binding_id', $query->createNamedParameter($binding)))
    ->andWhere($query->expr()->eq('file_id', $query->createNamedParameter($transport->getId())))->executeQuery();
try { $claim = $res->fetchAssociative(); } finally { $res->closeCursor(); }
$db->beginTransaction();
\OCA\IntegrationWeknora\Service\StatusPollQueue::enqueueInTransaction($db, $binding, $transport->getId());
$db->commit();
$polls->complete($claim);
$r = $query->executeQuery();
try { assertRuntime($r->fetchAssociative() !== false, 'Old ACK consumed a newer poll intent'); } finally { $r->closeCursor(); }
$container->get(FilePublicationStateService::class)->withdraw($binding, $file->getId(), $uid);
expectConflict(fn () => $projection->accept($raw));
assertRuntime($employee->forUser($uid, $file->getId(), false)['source_state'] === 'withdrawn', 'Withdrawn metadata reopened source');
assertRuntime($file->getContent() === 'Original source remains unchanged.' && $file->getEtag() === $etag,
    'Withdrawal/status wrote source file');
echo "Nextcloud real PostgreSQL migrations, status inbox, current head, source gates and unchanged ETag passed\n";
