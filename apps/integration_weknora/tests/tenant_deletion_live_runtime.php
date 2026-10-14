<?php
declare(strict_types=1);
// Uses an actual KB-closed operation followed by actual Tenant deletion. Only
// the polling schedule is advanced; no remote/owner/control proof is fabricated.
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1); require '/var/www/html/lib/base.php'; \OC_App::loadApp('user_ldap');
set_exception_handler(static function(Throwable $e): void { fwrite(STDERR, $e->getMessage() . "\n"); exit(1); });
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\DirectoryOnboardingClient;
use OCA\IntegrationWeknora\Service\DirectoryTargetClosureService;
use OCA\IntegrationWeknora\Service\DirectoryTenantDeletionService;
use OCA\IntegrationWeknora\Service\TenantDeletionProtocol;
use OCA\IntegrationWeknora\Service\TargetDeletionProtocol;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\IdentityMappingService;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;
use OCA\IntegrationWeknora\Service\PublicationRecoveryLedger;
function ownerLiveCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
function ownerLiveOriginal(string $uid, int $id): array {
    $nodes = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid)->getById($id); $file = $nodes[0] ?? null;
    ownerLiveCheck($file instanceof \OCP\Files\File, 'Actual retained file required');
    return ['id' => $file->getId(), 'etag' => $file->getEtag(), 'sha256' => hash('sha256', $file->getContent())];
}
function ownerLiveCredentials(\OCP\IDBConnection $db, string $binding): string {
    $hashes = [];
    foreach (['weknora_machine_key', 'weknora_event_conn'] as $table) {
        $q = $db->getQueryBuilder(); $r = $q->select('*')->from($table)->where($q->expr()->eq('binding_id', $q->createNamedParameter($binding)))->executeQuery();
        try { $rows = $r->fetchAllAssociative(); } finally { $r->closeCursor(); }
        foreach ($rows as $row) { ksort($row); $hashes[] = hash('sha256', json_encode($row, JSON_THROW_ON_ERROR)); }
    }
    sort($hashes); return hash('sha256', json_encode($hashes, JSON_THROW_ON_ERROR));
}
$path = $argv[1] ?? ''; $stage = $argv[2] ?? '';
if (!is_file($path) || is_link($path) || (fileperms($path) & 0o077) || !in_array($stage, ['before', 'unverified', 'deleted'], true)) { throw new RuntimeException('Private actual descriptor/stage required'); }
$f = json_decode(file_get_contents($path), true, 16, JSON_THROW_ON_ERROR);
foreach (['operation_id', 'user_id'] as $field) { if (!is_string($f[$field] ?? null) || $f[$field] === '') { throw new RuntimeException('Invalid actual closed-operation descriptor'); } }
ownerLiveCheck(is_int($f['file_id'] ?? null) && $f['file_id'] > 0, 'Actual source file ID required');
$app = new Application(); $c = $app->getContainer(); $db = \OCP\Server::get(\OCP\IDBConnection::class); $config = \OCP\Server::get(\OCP\IConfig::class);
$directory = $c->get(DirectoryPublicationService::class); $bindings = $c->get(BindingRegistryService::class); $pairs = $c->get(SourcePairingRegistryService::class);
$q = $db->getQueryBuilder(); $r = $q->select('*')->from('weknora_dir_intent')->where($q->expr()->eq('operation_id', $q->createNamedParameter($f['operation_id'])))->executeQuery();
try { $intent = $r->fetchAssociative(); } finally { $r->closeCursor(); }
ownerLiveCheck($intent !== false && $intent['state'] === 'target_deleted', 'Actual permanently KB-closed intent required');
$rootFact = DirectoryTargetClosureService::lookup($db, 'operation_id', $f['operation_id']);
ownerLiveCheck($rootFact !== null && $rootFact['target']['reason'] === 'knowledge_base_deleted', 'Actual earlier KB closure required; do not replace with Tenant closure fixture');
$request = json_decode($intent['request_json'], true, 16, JSON_THROW_ON_ERROR); $rule = null;
foreach ($directory->rules() as $entry) { if ($entry['id'] === $intent['rule_id']) { foreach ($entry['targets'] ?? [['tenant_id' => $entry['tenant_id'], 'processing_template_id' => $entry['processing_template_id']]] as $choice) { if ($choice['tenant_id'] === (string)$request['tenant_id'] && $choice['processing_template_id'] === $request['processing_template_id']) { $rule = array_replace($entry, $choice); } } } }
ownerLiveCheck($rule !== null, 'Exact existing origin/target rule required');
$identity = $c->get(IdentityMappingService::class)->forUser($f['user_id']); $actor = ['actor_directory_id' => $identity['directory_id'], 'actor_object_guid' => $identity['object_guid']];
$originSHA = TenantDeletionProtocol::originHash($rule['weknora_origin']);
if ($stage === 'before') {
    $response = $c->get(DirectoryOnboardingClient::class)->request($rule, 'GET', '/operations/' . $f['operation_id'], $actor);
    ownerLiveCheck(TargetDeletionProtocol::target($response['target'] ?? []) === $rootFact['target'] && !array_key_exists('tenant_deletion', $response), 'Actual KB-closed/Tenant-live baseline required');
    ownerLiveCheck(DirectoryTenantDeletionService::lookup($db, $originSHA, (string)$request['tenant_id']) === null, 'Independent Tenant fact already retained');
    $f['baseline'] = ['root_fact' => $rootFact, 'intent_revision' => (int)$intent['revision'], 'pair' => $pairs->status($intent['binding_id']),
        'original' => ownerLiveOriginal($f['user_id'], $f['file_id']), 'credentials_sha256' => ownerLiveCredentials($db, $intent['binding_id']),
        'other_binding_epoch' => isset($f['other_binding_id']) ? $bindings->requirePublicationActive($f['other_binding_id']) : null];
    file_put_contents($path, json_encode($f, JSON_THROW_ON_ERROR)); chmod($path, 0600);
    echo "Actual KB closure before later Tenant deletion baseline retained privately\n"; exit;
}
ownerLiveCheck(is_array($f['baseline'] ?? null), 'Actual before-stage evidence required');
// Only NC polling timestamps change. The actual private worker still obtains a
// fresh delegate/LDAP actor, configured approved client GET200, and typed fact.
$db->insertIgnoreConflict('weknora_deletion_probe', ['operation_id' => $f['operation_id'], 'next_check_at' => 0, 'lease_until' => 0]);
$q = $db->getQueryBuilder(); $q->update('weknora_deletion_probe')->set('next_check_at', $q->createNamedParameter(0))->set('lease_until', $q->createNamedParameter(0))
    ->where($q->expr()->eq('operation_id', $q->createNamedParameter($f['operation_id'])))->executeStatement();
$worker = new ReflectionMethod(DirectoryPublicationService::class, 'observeTenantDeletionMetadataDue');
$worker->invoke($directory, 3, microtime(true) + 30);
$fact = DirectoryTenantDeletionService::lookup($db, $originSHA, (string)$request['tenant_id']);
ownerLiveCheck(DirectoryTargetClosureService::lookup($db, 'operation_id', $f['operation_id']) === $f['baseline']['root_fact'] &&
    $pairs->status($intent['binding_id']) === $f['baseline']['pair'] && ownerLiveOriginal($f['user_id'], $f['file_id']) === $f['baseline']['original'] &&
    ownerLiveCredentials($db, $intent['binding_id']) === $f['baseline']['credentials_sha256'], 'Tenant metadata changed root receipt/source/pair/credentials/originals');
$q = $db->getQueryBuilder(); $r = $q->select('state', 'revision')->from('weknora_dir_intent')->where($q->expr()->eq('operation_id', $q->createNamedParameter($f['operation_id'])))->executeQuery();
try { $afterIntent = $r->fetchAssociative(); } finally { $r->closeCursor(); }
ownerLiveCheck($afterIntent['state'] === 'target_deleted' && (int)$afterIntent['revision'] === $f['baseline']['intent_revision'], 'Metadata polling resumed/revised terminal root');
if (isset($f['other_binding_id'])) { ownerLiveCheck($bindings->requirePublicationActive($f['other_binding_id']) === $f['baseline']['other_binding_epoch'], 'Different live Tenant binding was stopped'); }
if ($stage === 'unverified') {
    ownerLiveCheck($fact === null, 'Fresh actor/transport failure invented independent Tenant fact');
    echo "Actual terminal metadata verification failure preserved root closure and retained independent source state\n"; exit;
}
ownerLiveCheck($fact !== null, 'Actual independent Tenant deletion was not collected; do not call PASS');
$response = $c->get(DirectoryOnboardingClient::class)->request($rule, 'GET', '/operations/' . $f['operation_id'], $actor);
$observed = TenantDeletionProtocol::observation($response, $intent, $request, $config->getSystemValueString('instanceid'), $rule['weknora_origin']);
ownerLiveCheck($observed !== null && TenantDeletionProtocol::sameNamespaceFact($fact, $observed) && TargetDeletionProtocol::target($response['target'] ?? []) === $f['baseline']['root_fact']['target'], 'Actual Tenant GET200 did not preserve old exact target closure');
$journal = new PublicationRecoveryLedger($db, $config); $cursor = 0; $seen = false;
for ($n = 0; $n < 256; $n++) {
    $page = $journal->page($cursor);
    foreach ($page['items'] as $event) { if ($event['kind'] === 'tenant_deleted' && ($event['tenant_deletion'] ?? null) === $fact && $event['file_id'] === null && $event['source_revision'] === '0') { $seen = true; } }
    if ($seen || !$page['has_more']) { break; } $cursor = (int)$page['next_sequence'];
}
ownerLiveCheck($seen, 'Independent Tenant fact missing from external journal');
try { DirectoryTenantDeletionService::requireTenantOpen($db, $rule['weknora_origin'], (string)$request['tenant_id']); throw new RuntimeException('Deleted Tenant reopened'); } catch (\OCA\IntegrationWeknora\Service\BindingPublicationStoppedException $e) {}
// An actual retained signed plan must subsequently include this owner fact,
// even if the snapshot lies between KB deletion and later Tenant deletion.
echo "Actual later Tenant deletion -> fresh terminal-only metadata -> independent namespace/journal passed; old root/source receipt unchanged\n";
