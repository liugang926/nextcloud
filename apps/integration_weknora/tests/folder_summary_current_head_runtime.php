<?php
declare(strict_types=1);
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
\OC_App::loadApp('user_ldap');
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\EventConnectionService;
use OCA\IntegrationWeknora\Service\FilePublicationStateService;
use OCA\IntegrationWeknora\Service\FolderSummaryService;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;
use OCA\IntegrationWeknora\Service\StatusProjectionService;
function headCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
$services = (new Application())->getContainer(); $uid = 'auto-prd-admin';
$home = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid);
$root = $home->newFolder('summary-head-' . bin2hex(random_bytes(4)));
$file = $root->newFile('Current document.txt', 'Current original content.'); $etag = $file->getEtag();
$binding = 'head_' . bin2hex(random_bytes(8)); $registry = $services->get(BindingRegistryService::class);
$registry->save($binding, 'Summary current head', $uid, $root->getId());
$operation = 'abababab-2222-4333-8444-' . bin2hex(random_bytes(6));
$pairs = $services->get(SourcePairingRegistryService::class);
$prepare = $pairs->prepare($binding, $operation, '7', 'kb-' . $binding, $uid);
$instance = \OCP\Server::get(\OCP\IConfig::class)->getSystemValueString('instanceid');
$pairs->commit($binding, $prepare['pairing']['key_id'], $operation, $instance, '7', 'kb-' . $binding, 'ds-' . $binding);
putenv('WEKNORA_EVENT_DEV_HTTP=1'); putenv('WEKNORA_EVENT_ALLOWED_ORIGINS=http://test-weknora');
$connection = 'connection-' . $binding;
$services->get(EventConnectionService::class)->configure($binding, $binding, $instance, $connection, 'current',
    'synthetic_summary_secret_0123456789abcdef', 'http://test-weknora/api/v1/integrations/nextcloud/events');
$now = time(); $epoch = $registry->requirePublicationActive($binding);
$state = ['schema_version' => 2, 'event_id' => 'published-' . $binding, 'connection_id' => $connection,
    'instance_id' => $instance, 'binding_id' => $binding, 'pair_operation_id' => $operation,
    'tenant_id' => '7', 'knowledge_base_id' => 'kb-' . $binding, 'data_source_id' => 'ds-' . $binding,
    'file_id' => (string)$file->getId(), 'source_etag' => $etag, 'source_content_revision' => $services->get(\OCA\IntegrationWeknora\Service\SourceContentRevisionService::class)->current($file->getId()), 'publication_epoch' => (string)$epoch,
    'head_revision' => '1', 'status_revision' => '2', 'knowledge_id' => 'candidate-' . $binding,
    'build_attempt' => '1', 'processing_config_revision' => '1', 'sync_state' => 'ingested',
    'stages' => array_fill_keys(['parse', 'chunk', 'embedding', 'index'], ['state' => 'succeeded', 'started_at' => $now - 10, 'completed_at' => $now - 1]),
    'graph' => ['enabled' => true, 'required' => false, 'state' => 'partial_failed', 'expected' => 2,
        'succeeded' => 1, 'failed' => 1, 'skipped' => 0, 'cancelled' => 0, 'pending' => 0,
        'persisted' => false, 'completed_at' => $now - 1, 'error_code' => 'graph_extract_failed'],
    'publication_state' => 'published', 'published_source_etag' => $etag, 'permission_revision' => '1',
    'last_permission_sync_at' => $now - 1, 'occurred_at' => $now - 1, 'last_verified_at' => $now - 1,
    'withdrawal_state' => 'none', 'permission_reclaim_state' => 'none', 'gc_state' => 'unverified',
    'error_code' => null, 'qa_available' => false];
$projection = $services->get(StatusProjectionService::class);
$projection->accept(json_encode($state, JSON_THROW_ON_ERROR));
$summary = $services->get(FolderSummaryService::class);
$fresh = $summary->forUser($uid, $root->getId())['summary'];
headCheck($fresh['total_visible'] === 1 && $fresh['current_completed'] === 1 && $fresh['graph_counts']['partial_failed'] === 1,
    'Optional graph failure hid current document readiness in bulk summary');
$db = \OCP\Server::get(\OCP\IDBConnection::class); $q = $db->getQueryBuilder();
$q->update('weknora_file_status')->set('last_verified_at', $q->createNamedParameter($now - 301))
    ->set('fresh_until', $q->createNamedParameter($now - 1))
    ->where($q->expr()->eq('binding_id', $q->createNamedParameter($binding)))->executeStatement();
$expired = $summary->forUser($uid, $root->getId())['summary'];
headCheck($expired['current_completed'] === 0 && $expired['knowledge_counts']['unverified'] === 1 &&
    $expired['graph_counts']['unverified'] === 1 && $expired['status_stale'], 'Expired success counted as current ready/graph result');
$snapshot = array_replace($state, ['event_id' => 'fresh-snapshot-' . $binding, 'last_verified_at' => time()]);
$projection->accept(json_encode($snapshot, JSON_THROW_ON_ERROR), time());
headCheck($summary->forUser($uid, $root->getId())['summary']['current_completed'] === 1, 'Current signed snapshot did not restore freshness');
$file->putContent('A changed source version with a different byte length.');
$file->getStorage()->getUpdater()->update($file->getInternalPath());
$current = $db->getQueryBuilder();
$r = $current->select('etag')->from('filecache')->where($current->expr()->eq('fileid', $current->createNamedParameter($file->getId())))->executeQuery();
try { $currentEtag = $r->fetchOne(); } finally { $r->closeCursor(); }
headCheck($currentEtag !== $etag, 'Test source update did not advance the authoritative Nextcloud filecache ETag');
$changed = $summary->forUser($uid, $root->getId())['summary'];
headCheck($changed['current_completed'] === 0 && $changed['knowledge_counts']['unverified'] === 1,
    'Old ETag success counted after source update');
$services->get(FilePublicationStateService::class)->withdraw($binding, $file->getId(), $uid);
headCheck($summary->forUser($uid, $root->getId())['summary']['total_visible'] === 0,
    'Withdrawn source retained a processed-file summary count');
echo "Bulk summary current ETag/head, optional graph independence, stale success exclusion and signed freshness compensation passed\n";
