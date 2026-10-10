<?php
declare(strict_types=1);
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });
\OC_App::loadApp('user_ldap');
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\SourceContentRevisionService;
use OCA\IntegrationWeknora\Service\EmployeeFileStatusService;
use OCA\IntegrationWeknora\Service\FolderSummaryService;
function revisionCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
$services = (new Application())->getContainer(); $uid = 'auto-prd-admin';
$root = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid)->newFolder('same-etag-proof-' . bin2hex(random_bytes(4)));
$binding = 'same_etag_' . bin2hex(random_bytes(8));
$services->get(BindingRegistryService::class)->save($binding, 'Source revision observer', $uid, $root->getId());
$file = $root->newFile('equal-size.txt', str_repeat('A', 256));
$versions = $services->get(SourceContentRevisionService::class);
$db = \OCP\Server::get(\OCP\IDBConnection::class);
function sourceCacheObservation($db, int $id): array {
    $q = $db->getQueryBuilder(); $r = $q->select('etag', 'size', 'storage_mtime')->from('filecache')
        ->where($q->expr()->eq('fileid', $q->createNamedParameter($id)))->executeQuery();
    try { $row = $r->fetchAssociative(); if ($row === false) { throw new RuntimeException('File cache row disappeared'); } return $row; }
    finally { $r->closeCursor(); }
}
$observed = false; $attempts = 0;
for ($attempt = 0; $attempt < 20; $attempt++) {
    $attempts++;
    $before = sourceCacheObservation($db, $file->getId()); $revision = $versions->current($file->getId());
    $original = $file->getContent();
    $file->putContent(str_repeat(chr(66 + ($attempt % 20)), 256));
    // CLI FileNode writes require explicit updater refresh for the metadata
    // observation. Do not set ETags, mtimes or versions to manufacture a collision.
    $file->getStorage()->getUpdater()->update($file->getInternalPath());
    $after = sourceCacheObservation($db, $file->getId()); $nextRevision = $versions->current($file->getId());
    revisionCheck((int)$nextRevision > (int)$revision && $file->getContent() !== $original,
        'Actual Node.putContent did not advance the independent revision');
    if ($before['etag'] === $after['etag'] && $before['storage_mtime'] === $after['storage_mtime'] && $before['size'] === $after['size']) {
        $observed = true; break;
    }
}
revisionCheck($observed, 'This runtime did not reproduce an equal-ETag same-second equal-size write; report separately, do not synthesize it');
$q = $db->getQueryBuilder(); $r = $q->select('file_id')->from('weknora_status_pending')
    ->where($q->expr()->eq('binding_id', $q->createNamedParameter($binding)))
    ->andWhere($q->expr()->eq('file_id', $q->createNamedParameter($file->getId())))->executeQuery();
try { revisionCheck($r->fetchOne() !== false, 'Source update failed to schedule pull compensation'); } finally { $r->closeCursor(); }
// A failed write after its before-event deliberately has no completion.
// The durable fence keeps old derived knowledge closed without hiding the original.
$versions->begin($file->getId(), [$binding]);
try { $versions->current($file->getId()); throw new RuntimeException('Incomplete write reopened its source'); }
catch (DomainException $closed) {}
$status = $services->get(EmployeeFileStatusService::class)->forUser($uid, $file->getId(), false);
$summary = $services->get(FolderSummaryService::class)->forUser($uid, $root->getId())['summary'];
revisionCheck($status !== null && $status['source_state'] === 'in_scope' && $status['knowledge_state'] === 'unverified' &&
    $status['source_content_revision'] === null && !$status['qa_available'] && $summary['current_completed'] === 0,
    'Write fence hid the original or allowed a stale successful result');
$versions->complete($file->getId(), [$binding]);
echo json_encode(['fixture' => 'actual_nextcloud_filenode_in_place_write', 'same_second_same_size_etag_collision_observed' => true,
    'attempts' => $attempts, 'etag_unchanged' => $before['etag'] === $after['etag'],
    'revision_before' => $revision, 'revision_after' => $nextRevision, 'metadata_etag_or_mtime_forged' => false], JSON_THROW_ON_ERROR) . "\n";
echo "Real equal-ETag write, independent source revision, compensation hint and failed-write fence passed\n";
