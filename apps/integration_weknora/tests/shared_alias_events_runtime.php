<?php
declare(strict_types=1);
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });
\OC_App::loadApp('user_ldap');
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\ManifestSnapshotService;
use OCA\IntegrationWeknora\Service\SourceContentRevisionService;
use OCP\Server;
use OCP\Share\IShare;
function aliasCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
$services = (new Application())->getContainer(); $ownerUid = 'prd-sourceowner'; $readerUid = 'prd-alice';
$users = Server::get(\OCP\IUserManager::class); $owner = $users->get($ownerUid); $reader = $users->get($readerUid);
aliasCheck($owner !== null && $reader !== null && $owner->getBackendClassName() === 'LDAP', 'Real owned LDAP fixture missing');
Server::get(\OCP\IUserSession::class)->setUser($owner);
$files = Server::get(\OCP\Files\IRootFolder::class); $root = $files->getUserFolder($ownerUid)->newFolder('Shared alias proof ' . bin2hex(random_bytes(4)));
$binding = 'alias_' . bin2hex(random_bytes(8));
$registry = $services->get(BindingRegistryService::class); $registry->save($binding, 'Shared alias events', $ownerUid, $root->getId());
$file = $root->newFile('shared.txt', 'Synthetic original.'); $fileId = $file->getId();
$manager = Server::get(\OCP\Share\IManager::class);
$share = $manager->newShare()->setNode($root)->setShareType(IShare::TYPE_USER)->setSharedBy($ownerUid)->setSharedWith($readerUid)
    ->setPermissions(\OCP\Constants::PERMISSION_READ | \OCP\Constants::PERMISSION_UPDATE | \OCP\Constants::PERMISSION_CREATE | \OCP\Constants::PERMISSION_DELETE);
$manager->createShare($share);
Server::get(\OCP\IUserSession::class)->setUser($reader);
\OC_Util::tearDownFS(); \OC_Util::setupFS($readerUid);
$aliasRoot = $files->getUserFolder($readerUid)->getById($root->getId())[0];
$aliasFile = $aliasRoot->getById($fileId)[0];
aliasCheck($aliasFile->getPath() !== $file->getPath() && $aliasFile->getId() === $fileId, 'Test did not use a distinct real shared mount');
$db = Server::get(\OCP\IDBConnection::class);
function aliasEvents($db, string $binding, int $after = 0): array {
    $q = $db->getQueryBuilder(); $r = $q->select('*')->from('weknora_outbox')
        ->where($q->expr()->eq('binding_id', $q->createNamedParameter($binding)))
        ->andWhere($q->expr()->gt('id', $q->createNamedParameter($after)))->orderBy('id')->executeQuery();
    try { return $r->fetchAllAssociative(); } finally { $r->closeCursor(); }
}
$events = aliasEvents($db, $binding); $beforeId = (int)end($events)['id'];
$versions = $services->get(SourceContentRevisionService::class); $beforeRevision = $versions->current($fileId);
$snapshots = $services->get(ManifestSnapshotService::class);
$savedSnapshot = $snapshots->save($binding, hash('sha256', 'synthetic-source-snapshot'), $root->getEtag(),
    $snapshots->publicationRevision($binding), $registry->requirePublicationActive($binding), []);
$aliasFile->putContent('Synthetic writer content through a real shared mount.');
$aliasFile->getStorage()->getUpdater()->update($aliasFile->getInternalPath());
$writes = aliasEvents($db, $binding, $beforeId); $canonicalUpsert = false;
foreach ($writes as $event) {
    if ($event['event_type'] === 'upsert' && (int)$event['file_id'] === $fileId && $event['path'] === $file->getPath() && $event['relative_path'] === 'shared.txt') { $canonicalUpsert = true; }
}
if (!$canonicalUpsert) {
    echo json_encode(['owner_path' => $file->getPath(), 'alias_path' => $aliasFile->getPath(), 'events' => array_map(static fn (array $row): array =>
        array_intersect_key($row, array_flip(['event_type', 'file_id', 'old_path', 'path', 'relative_path'])), $writes)], JSON_THROW_ON_ERROR) . "\n";
    $ownerRoots = $files->getUserFolder($ownerUid)->getById($root->getId());
    echo json_encode(['owner_roots' => count($ownerRoots), 'owner_root_readable' => $ownerRoots[0]->isReadable() ?? null,
        'owner_nodes' => array_map(static fn ($n): array => ['id' => $n->getId(), 'path' => $n->getPath(), 'readable' => $n->isReadable(),
            'in_root' => $ownerRoots[0]->isSubNode($n)], $ownerRoots[0]->getById($fileId)),
        'revision_before' => $beforeRevision, 'revision_after' => $versions->current($fileId)], JSON_THROW_ON_ERROR) . "\n";
}
aliasCheck($canonicalUpsert, 'Shared write did not enqueue the publisher canonical path');
aliasCheck((int)$versions->current($fileId) > (int)$beforeRevision && $snapshots->load($binding, $savedSnapshot) === null,
    'Shared write did not fence its physical source version or invalidate the binding manifest');
$q = $db->getQueryBuilder(); $r = $q->select('file_id')->from('weknora_status_pending')
    ->where($q->expr()->eq('binding_id', $q->createNamedParameter($binding)))
    ->andWhere($q->expr()->eq('file_id', $q->createNamedParameter($fileId)))->executeQuery();
try { aliasCheck($r->fetchOne() !== false, 'Shared physical file write omitted pull compensation'); } finally { $r->closeCursor(); }
$beforeId = (int)end($writes)['id'];
$beforeRenameRevision = $versions->current($fileId); $beforeRenamePath = $file->getPath();
$aliasFile = $aliasFile->move($aliasRoot->getPath() . '/renamed.txt');
$renames = aliasEvents($db, $binding, $beforeId); $canonicalMetadata = false;
foreach ($renames as $event) {
    if ($event['event_type'] === 'metadata' && (int)$event['file_id'] === $fileId && $event['old_path'] === $beforeRenamePath &&
        $event['relative_path'] === 'renamed.txt' && str_starts_with($event['path'], '/' . $ownerUid . '/files/')) { $canonicalMetadata = true; }
}
aliasCheck($canonicalMetadata && $versions->current($fileId) === $beforeRenameRevision,
    'Shared rename failed to preserve content revision or update canonical metadata');
$canonicalRenamedPath = $root->getById($fileId)[0]->getPath();
$beforeId = (int)end($renames)['id'];
$aliasFile->delete(); $deletes = aliasEvents($db, $binding, $beforeId); $canonicalDelete = false;
foreach ($deletes as $event) {
    if ($event['event_type'] === 'delete' && (int)$event['file_id'] === $fileId && $event['old_path'] === $canonicalRenamedPath) { $canonicalDelete = true; }
}
aliasCheck($canonicalDelete && $root->getById($fileId) === [], 'Confirmed shared-view deletion did not withdraw the exact source');
$retained = $aliasRoot->newFile('Retained original.txt', 'Removing only a received share must preserve this original.');
$retainedId = $retained->getId(); $allEvents = aliasEvents($db, $binding); $beforeId = (int)end($allEvents)['id'];
$aliasRoot->delete();
$afterUnshare = aliasEvents($db, $binding, $beforeId);
foreach ($afterUnshare as $event) {
    aliasCheck(!in_array($event['event_type'], ['delete', 'subtree_deleted'], true), 'Receiver unshare fabricated a physical source tombstone');
}
$ownerView = $files->getUserFolder($ownerUid); $ownerRoots = $ownerView->getById($root->getId());
aliasCheck(count($ownerRoots) === 1 && count($ownerRoots[0]->getById($retainedId)) === 1,
    'Receiver unshare deleted the publisher original or root');
echo "Real shared-mount write/delete canonical hints, physical revision, manifest invalidation and pull compensation passed\n";
