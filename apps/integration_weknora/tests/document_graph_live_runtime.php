<?php
declare(strict_types=1);
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
\OC_App::loadApp('user_ldap');
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, get_class($e) . ': ' . $e->getMessage() . "\n"); exit(1); });
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\EventConnectionService;
$c = (new Application())->getContainer();
$bindings = $c->get(BindingRegistryService::class);
$pairs = $c->get(SourcePairingRegistryService::class);
$path = '/tmp/document-graph-live.json';
$step = $argv[1] ?? 'prepare';
$uid = 'prd-sourceowner';
$users = \OCP\Server::get(\OCP\IUserManager::class);
$users->search($uid, 10, 0);
$users->search('prd-alice', 10, 0);
\OCP\Server::get(\OCP\IUserSession::class)->setUser(\OCP\Server::get(\OCP\IUserManager::class)->get($uid));
if ($step === 'prepare') {
    if (is_file($path)) {
        $previous = json_decode(file_get_contents($path), true, 32, JSON_THROW_ON_ERROR);
        $old = $previous['Pair'];
        if (!str_starts_with($old['binding_id'], 'docgraph_')) { throw new RuntimeException('Not an owned fixture'); }
        $status = $pairs->status($old['binding_id']);
        if (($status['state'] ?? '') === 'pending') { $pairs->abort($old['binding_id'], $old['operation_id']); }
    }

    $root = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid)->newFolder('document-graph-' . bin2hex(random_bytes(6)));
    $file = $root->newFile('source.md', 'Alice is a software engineer on Project Cedar. Alice knows Bob, the database engineer on Project Cedar. Bob knows Alice. They work together to build a document search system. Project Cedar stores project documents and supports retrieval.');
    $identity = $c->get(\OCA\IntegrationWeknora\Service\IdentityMappingService::class)->forUser('prd-alice');
    $shares = \OCP\Server::get(\OCP\Share\IManager::class);
    $share = $shares->createShare($shares->newShare()->setNode($root)->setShareType(\OCP\Share\IShare::TYPE_USER)->setSharedBy($uid)->setSharedWith('prd-alice')->setPermissions(\OCP\Constants::PERMISSION_READ));
    $binding = 'docgraph_' . bin2hex(random_bytes(8));
    $bindings->save($binding, 'Owned full document graph test', $uid, $root->getId());
    $bindings->setPublicationState($binding, 'stopped', $uid);
    $bindings->setPublicationState($binding, 'active', $uid);
    $op = DirectoryPublicationService::uuid();
    $kb = 'docgraph-kb-' . $op;
    $prepared = $pairs->prepare($binding, $op, '7', $kb, $uid);
    $pair = $prepared['pairing'];
    $instance = \OCP\Server::get(\OCP\IConfig::class)->getSystemValueString('instanceid');
    $f = ['Pair' => ['operation_id'=>$op, 'knowledge_base_id'=>$kb, 'instance_id'=>$instance, 'binding_id'=>$binding, 'base_url'=>'http://nextcloud', 'publication_epoch'=>(int)$pair['publication_epoch'], 'key_id'=>$pair['key_id']],
        'Directory'=>$identity['directory_id'], 'GUID'=>$identity['object_guid'], 'ShareID'=>$share->getFullId(), 'Token'=>$prepared['token'], 'ExternalID'=>'nextcloud:'.$instance.':'.$file->getId(), 'ETag'=>$file->getEtag(), 'Connection'=>'docgraph-'.bin2hex(random_bytes(8)), 'Secret'=>bin2hex(random_bytes(32)), 'FileID'=>$file->getId(), 'RootID'=>$root->getId(), 'SHA256'=>hash('sha256',$file->getContent())];
    file_put_contents($path, json_encode($f, JSON_THROW_ON_ERROR)); chmod($path, 0600);
    echo "Prepared fresh owned original and pending source pair\n"; exit;
}
$f = json_decode(file_get_contents($path), true, 32, JSON_THROW_ON_ERROR);
$p = $f['Pair'];
if ($step === 'cleanup') {
    if (!str_starts_with($p['binding_id'], 'docgraph_')) { throw new RuntimeException('Not an owned fixture'); }
    $status = $pairs->status($p['binding_id']);
    if (($status['operation_id'] ?? '') !== $p['operation_id']) { throw new RuntimeException('Cleanup scope mismatch'); }
    if ($status['state'] === 'pending') { $pairs->abort($p['binding_id'], $p['operation_id']); }
    $bindings->setPublicationState($p['binding_id'], 'stopped', $uid);
    foreach ([$path, $path.'.ready', $path.'.result'] as $private) { if (is_file($private) && !unlink($private)) { throw new RuntimeException('Private fixture cleanup failed'); } }
    echo "Closed owned test publication and removed private transport files; original retained\n"; exit;
}
if ($step === 'commit') {
    $ready = json_decode(file_get_contents($path.'.ready'), true, 32, JSON_THROW_ON_ERROR);
    if ($ready['operation_id'] !== $p['operation_id'] || $ready['binding_id'] !== $p['binding_id'] || $ready['knowledge_base_id'] !== $p['knowledge_base_id']) { throw new RuntimeException('Prepared source scope mismatch'); }
    $pairs->commit($p['binding_id'], $p['key_id'], $p['operation_id'], $p['instance_id'], '7', $p['knowledge_base_id'], $ready['data_source_id']);
    putenv('WEKNORA_EVENT_DEV_HTTP=1'); putenv('WEKNORA_EVENT_ALLOWED_ORIGINS=http://weknora-prd-docgraph-live');
    $c->get(EventConnectionService::class)->configure($p['binding_id'], $p['binding_id'], $p['instance_id'], $f['Connection'], 'current', $f['Secret'], 'http://weknora-prd-docgraph-live/api/v1/integrations/nextcloud/events');
    echo "Committed exact pair and isolated state connection\n"; exit;
}
if ($step === 'revoke') {
    $visible = $c->get(\OCA\IntegrationWeknora\Service\EmployeeFileStatusService::class)->forUser('prd-alice', $f['FileID'], false);
    $before = $c->get(\OCA\IntegrationWeknora\Service\FolderSummaryService::class)->forUser('prd-alice', $f['RootID']);
    if ($visible === null || $visible['source_state'] !== 'in_scope' || $before === null || $before['summary']['total_visible'] !== 1) { throw new RuntimeException('Authorized LDAP user cannot see actual processed file and summary'); }
    $shares = \OCP\Server::get(\OCP\Share\IManager::class);
    $shares->deleteShare($shares->getShareById($f['ShareID']));
    $employee = $c->get(\OCA\IntegrationWeknora\Service\EmployeeFileStatusService::class)->forUser('prd-alice', $f['FileID'], false);
    $summary = $c->get(\OCA\IntegrationWeknora\Service\FolderSummaryService::class)->forUser('prd-alice', $f['RootID']);
    if ($employee !== null || ($summary !== null && $summary['summary']['total_visible'] !== 0)) { throw new RuntimeException('Revoked user retained visible state or counts'); }
    echo "Actual LDAP reader share revoked; employee status and folder summary denied\n"; exit;
}
if ($step !== 'verify') { throw new RuntimeException('Unknown step'); }
$expected = \OCA\IntegrationWeknora\Service\StatusProtocol::decode(file_get_contents($path.'.result'), time());
$db = \OCP\Server::get(\OCP\IDBConnection::class); $q=$db->getQueryBuilder();
$result=$q->select('body_json')->from('weknora_file_status')->where($q->expr()->eq('binding_id',$q->createNamedParameter($p['binding_id'])))->andWhere($q->expr()->eq('file_id',$q->createNamedParameter($f['FileID'])))->executeQuery();
try { $raw=$result->fetchOne(); } finally { $result->closeCursor(); }
$actual=json_decode((string)$raw,true,32,JSON_THROW_ON_ERROR);
function canonicalState(array $value): array { foreach ($value as &$item) { if (is_array($item)) { $item = canonicalState($item); } } unset($item); ksort($value); return $value; }
if (canonicalState($actual) !== canonicalState($expected) || $actual['publication_state'] !== 'published' || $actual['graph']['state'] !== 'succeeded' || !$actual['graph']['persisted']) { throw new RuntimeException('Inbox current state differs from actual processed output'); }
foreach (['parse','chunk','embedding','index'] as $stage) { if ($actual['stages'][$stage]['state'] !== 'succeeded') { throw new RuntimeException('Actual stage incomplete'); } }
$root=$bindings->requireActiveRoot($p['binding_id']); $file=$root->get('source.md');
if ($file->getId() !== $f['FileID'] || $file->getEtag() !== $f['ETag'] || hash('sha256',$file->getContent()) !== $f['SHA256']) { throw new RuntimeException('Original changed during state delivery'); }
echo "Actual original download -> document/index/graph workers -> signed durable inbox, exact current state and unchanged original verified\n";
