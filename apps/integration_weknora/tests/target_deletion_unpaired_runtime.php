<?php
declare(strict_types=1);
// Actual disposable NC/PG fixture only. The orchestrator must first retain a
// production WK reserve with this original UUID while NC has not paired it.
// No remote operation, target observation, deletion witness or receipt is forged.
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1); require '/var/www/html/lib/base.php'; \OC_App::loadApp('user_ldap');
set_exception_handler(static function(Throwable $e): void { fwrite(STDERR, $e->getMessage() . "\n"); exit(1); });
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\DirectoryOnboardingClient;
use OCA\IntegrationWeknora\Service\DirectoryTargetClosureService;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\IdentityMappingService;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;
use OCA\IntegrationWeknora\Service\PublicationRecoveryLedger;
use OCA\IntegrationWeknora\Service\TargetDeletionProtocol;
function unpairedCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
function unpairedDeny(callable $fn): void { try { $fn(); } catch (DomainException|\OCA\IntegrationWeknora\Service\BindingPublicationStoppedException $e) { return; } throw new RuntimeException('Permanent target reopened'); }
function unpairedOriginal(string $uid, int $fileId): array {
    $nodes = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid)->getById($fileId);
    $file = $nodes[0] ?? null;
    unpairedCheck($file instanceof \OCP\Files\File, 'Actual source file required');
    return ['file_id' => $file->getId(), 'etag' => $file->getEtag(), 'body_sha256' => hash('sha256', $file->getContent())];
}
function unpairedKeys(\OCP\IDBConnection $db, string $binding): array {
    $q = $db->getQueryBuilder(); $r = $q->select('key_id', 'token_sha256')->from('weknora_machine_key')
        ->where($q->expr()->eq('binding_id', $q->createNamedParameter($binding)))->orderBy('key_id')->executeQuery();
    try { $machine = $r->fetchAllAssociative(); } finally { $r->closeCursor(); }
    $q = $db->getQueryBuilder(); $r = $q->select('connection_id', 'key_id')->from('weknora_event_conn')
        ->where($q->expr()->eq('binding_id', $q->createNamedParameter($binding)))->orderBy('connection_id')->executeQuery();
    try { $events = $r->fetchAllAssociative(); } finally { $r->closeCursor(); }
    return ['machine' => $machine, 'events' => $events];
}
$path = $argv[1] ?? ''; $stage = $argv[2] ?? '';
if (!is_file($path) || is_link($path) || (fileperms($path) & 0o077) ||
    !in_array($stage, ['before', 'unverified', 'deleted'], true)) { throw new RuntimeException('Private actual descriptor/stage required'); }
$f = json_decode(file_get_contents($path), true, 16, JSON_THROW_ON_ERROR);
foreach (['operation_id', 'user_id'] as $field) { if (!is_string($f[$field] ?? null) || $f[$field] === '') { throw new RuntimeException('Invalid actual unpaired descriptor'); } }
unpairedCheck(is_int($f['file_id'] ?? null) && $f['file_id'] > 0, 'Actual source file ID required');
$app = new Application(); $c = $app->getContainer(); $db = \OCP\Server::get(\OCP\IDBConnection::class);
$directory = $c->get(DirectoryPublicationService::class); $bindings = $c->get(BindingRegistryService::class); $pairs = $c->get(SourcePairingRegistryService::class);
$q = $db->getQueryBuilder(); $r = $q->select('*')->from('weknora_dir_intent')->where($q->expr()->eq('operation_id', $q->createNamedParameter($f['operation_id'])))->executeQuery();
try { $intent = $r->fetchAssociative(); } finally { $r->closeCursor(); }
unpairedCheck($intent !== false && $intent['state'] === 'pending' && (int)$intent['root_file_id'] > 0 && (int)$intent['lease_until'] <= time(), 'Available real pending intent required');
unpairedCheck($pairs->status($intent['binding_id']) === null, 'This regression must remain unpaired, not a preparation or indexed-source exit');
$request = json_decode($intent['request_json'], true, 16, JSON_THROW_ON_ERROR);
$identity = $c->get(IdentityMappingService::class)->forUser($f['user_id']);
unpairedCheck($request['actor_directory_id'] === $identity['directory_id'] && $request['actor_object_guid'] === $identity['object_guid'], 'Current actual actor must be original reserve actor');
$rule = null;
foreach ($directory->rules() as $entry) {
    if ($entry['id'] !== $intent['rule_id']) { continue; }
    foreach ($entry['targets'] ?? [['tenant_id' => $entry['tenant_id'], 'processing_template_id' => $entry['processing_template_id']]] as $target) {
        if ($target['tenant_id'] === (string)$request['tenant_id'] && $target['processing_template_id'] === $request['processing_template_id']) { $rule = array_replace($entry, $target); }
    }
}
unpairedCheck($rule !== null, 'Current exact target rule required');
$client = $c->get(DirectoryOnboardingClient::class);
$currentActor = ['actor_directory_id' => $identity['directory_id'], 'actor_object_guid' => $identity['object_guid']];
$original = unpairedOriginal($f['user_id'], $f['file_id']); $keys = unpairedKeys($db, $intent['binding_id']);
$binding = null; foreach ($bindings->listBindings() as $entry) { if ($entry['id'] === $intent['binding_id']) { $binding = $entry; break; } }
$identityTuple = $binding === null ? null : ['id' => $binding['id'], 'owner_uid' => $binding['owner_uid'], 'root_file_id' => $binding['root_file_id']];
if ($stage === 'before') {
    $observed = $client->request($rule, 'GET', '/operations/' . $f['operation_id'], $currentActor);
    $target = TargetDeletionProtocol::observation($observed, $intent, $request, null,
        \OCP\Server::get(\OCP\IConfig::class)->getSystemValueString('instanceid'), $binding['sync_revision'] ?? 0);
    unpairedCheck($target['state'] === 'present', 'Real WK reserve before pairing required');
    unpairedCheck(DirectoryTargetClosureService::lookup($db, 'operation_id', $f['operation_id']) === null, 'Initial target already closed');
    $f['baseline'] = ['original' => $original, 'keys' => $keys, 'binding' => $identityTuple,
        'request_sha256' => hash('sha256', $intent['request_json']), 'local_revision' => (int)$intent['revision'],
        'remote_tuple' => array_intersect_key($target, array_flip(['operation_id', 'instance_id', 'binding_id', 'tenant_id', 'knowledge_base_id']))];
    file_put_contents($path, json_encode($f, JSON_THROW_ON_ERROR)); chmod($path, 0600);
    echo "Actual WK reserve retained while NC pairing is absent; baseline saved privately\n"; exit;
}
unpairedCheck(is_array($f['baseline'] ?? null) && $f['baseline']['request_sha256'] === hash('sha256', $intent['request_json']), 'Actual original baseline required');
if ($stage === 'deleted') {
    // Establish the branch trigger through the real client, not by a fake 409.
    $rejected = false;
    try { $client->request($rule, 'POST', '/operations', $request); } catch (Throwable $e) { $rejected = true; }
    unpairedCheck($rejected, 'Actual closed target must reject Reserve before NC recovery branch');
    $observed = $client->request($rule, 'GET', '/operations/' . $f['operation_id'], $currentActor);
    $target = TargetDeletionProtocol::observation($observed, $intent, $request, null,
        \OCP\Server::get(\OCP\IConfig::class)->getSystemValueString('instanceid'), $binding['sync_revision'] ?? 0);
    unpairedCheck($target['state'] === 'target_deleted', 'Actual authenticated 200 durable WK deletion required');
    unpairedCheck(array_intersect_key($target, $f['baseline']['remote_tuple']) === $f['baseline']['remote_tuple'], 'Original remote target tuple changed');
}
// unverified requires a real server/actor/authentication/TLS/404 failure arranged
// by the orchestrator. No remote proof, origin, credential or scope is replaced.
$result = $directory->retry($f['user_id'], $f['operation_id']);
unpairedCheck($pairs->status($intent['binding_id']) === null && unpairedKeys($db, $intent['binding_id']) === $f['baseline']['keys'] &&
    unpairedOriginal($f['user_id'], $f['file_id']) === $f['baseline']['original'], 'Failed reserve recovery changed pair, credentials or originals');
$afterBinding = null; foreach ($bindings->listBindings() as $entry) { if ($entry['id'] === $intent['binding_id']) { $afterBinding = $entry; break; } }
$afterIdentity = $afterBinding === null ? null : ['id' => $afterBinding['id'], 'owner_uid' => $afterBinding['owner_uid'], 'root_file_id' => $afterBinding['root_file_id']];
unpairedCheck($afterIdentity === $f['baseline']['binding'], 'Reserve failure created or reassociated a source');
if ($stage === 'unverified') {
    unpairedCheck($result['state'] === 'pending' && $result['error_code'] !== 'target_deleted' &&
        DirectoryTargetClosureService::lookup($db, 'operation_id', $f['operation_id']) === null, 'Unknown Reserve/status became terminal');
    echo "Actual unpaired Reserve/status failure remained pending without creating pair, target receipt or source credentials\n"; exit;
}
unpairedCheck($result['state'] === 'target_deleted' && $result['error_code'] === 'target_deleted' &&
    (int)$result['revision'] === $f['baseline']['local_revision'] + 1, 'Unpaired actual deletion did not converge with one terminal CAS');
$fact = DirectoryTargetClosureService::lookup($db, 'operation_id', $f['operation_id']);
unpairedCheck($fact !== null && $fact['target'] === $target, 'Unpaired closure did not retain exact actual WK proof');
if ($afterBinding !== null) { unpairedCheck($afterBinding['publication_state'] === 'stopped' && !$afterBinding['sync_enabled'], 'Existing unpaired binding remained enabled'); }
unpairedDeny(fn() => $directory->retry($f['user_id'], $f['operation_id']));
$shown = $directory->show($f['user_id'], (int)$intent['root_file_id']);
unpairedCheck(($shown['operation']['state'] ?? '') === 'target_deleted' && !$shown['sync_enabled'], 'Unpaired Files terminal projection missing');
$ledger = new PublicationRecoveryLedger($db, \OCP\Server::get(\OCP\IConfig::class)); $cursor = 0; $seen = false;
for ($pageNo = 0; $pageNo < 256; $pageNo++) {
    $page = $ledger->page($cursor);
    foreach ($page['items'] as $event) { if ($event['kind'] === 'target_deleted' && ($event['target_closure'] ?? null) === $fact) { $seen = true; } }
    if ($seen || !$page['has_more']) { break; } $cursor = (int)$page['next_sequence'];
}
unpairedCheck($seen, 'Unpaired permanent closure missing from independently exportable journal');
echo "Actual failed Reserve -> same original authenticated GET200 -> unpaired terminal CAS/root journal passed; pairing, originals and credentials retained\n";
