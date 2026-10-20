<?php
declare(strict_types=1);
require_once __DIR__ . '/../lib/Service/SourcePairingRegistryService.php';
require_once __DIR__ . '/../lib/Service/TargetDeletionProtocol.php';
require_once __DIR__ . '/../lib/Service/TenantDeletionProtocol.php';
require_once __DIR__ . '/../lib/Service/DirectoryTenantDeletionService.php';
require_once __DIR__ . '/../lib/Service/PublicationRecoveryLedger.php';
use OCA\IntegrationWeknora\Service\TenantDeletionProtocol as Protocol;
use OCA\IntegrationWeknora\Service\TargetDeletionProtocol as Target;
use OCA\IntegrationWeknora\Service\DirectoryTenantDeletionService as LedgerStore;
use OCA\IntegrationWeknora\Service\PublicationRecoveryLedger as Journal;
function tenantCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
function tenantReject(callable $fn): void { try { $fn(); } catch (UnexpectedValueException $e) { return; } throw new RuntimeException('Invalid independent Tenant control was accepted'); }
$op = '00112233-4455-4677-8899-aabbccddeeff'; $origin = 'https://wk.example.test'; $now = time();
$tenant = ['schema' => 'weknora-nextcloud-tenant-deletion-v1', 'state' => 'tenant_deleted', 'operation_id' => $op,
    'instance_id' => 'instance', 'binding_id' => 'binding', 'tenant_id' => '7', 'deletion_id' => '', 'deletion_time_ms' => (string)(($now - 1) * 1000), 'closed_at' => (string)$now];
$tenant['deletion_id'] = hash('sha256', implode("\n", ['weknora-tenant-deleted-v1', '7', $tenant['deletion_time_ms']]));
$target = ['schema' => 'weknora-nextcloud-target-v1', 'state' => 'target_deleted', 'reason' => 'knowledge_base_deleted', 'operation_id' => $op,
    'instance_id' => 'instance', 'binding_id' => 'binding', 'tenant_id' => '7', 'knowledge_base_id' => 'kb.with-dot', 'operation_revision' => '4', 'closure_id' => '',
    'deletion_time_ms' => (string)(($now - 1000) * 1000), 'closed_at' => (string)($now - 999)];
$target['closure_id'] = hash('sha256', implode("\n", ['weknora-target-deleted-v1', $op, '7', 'kb.with-dot', 'instance', 'binding', '4', 'knowledge_base_deleted', $target['deletion_time_ms']]));
$intent = ['operation_id' => $op, 'binding_id' => 'binding'];
$request = ['operation_id' => $op, 'instance_id' => 'instance', 'binding_id' => 'binding', 'tenant_id' => 7, 'knowledge_base_id' => 'kb.with-dot'];
$operation = $request + ['revision' => 6]; $response = ['operation' => $operation, 'target' => $target, 'tenant_deletion' => $tenant];
$fact = ['tenant' => $tenant, 'origin_sha256' => hash('sha256', $origin)];
tenantCheck(Protocol::observation($response, $intent, $request, 'instance', $origin) === $fact && Protocol::fact($fact) === $fact, 'Real-shaped independent observation refused');
tenantCheck(Target::target($target) === $target, 'Later Tenant deletion rewrote old KB closure ID');
$missing = $response; unset($missing['tenant_deletion']); tenantCheck(Protocol::observation($missing, $intent, $request, 'instance', $origin) === null, 'Omitted owner fact invented reopening/absence evidence');
foreach (Protocol::FIELDS as $field) {
    $bad = $response; $bad['tenant_deletion'][$field] = 'invalid'; tenantReject(fn() => Protocol::observation($bad, $intent, $request, 'instance', $origin));
}
$bad = $response; $bad['tenant_deletion']['body'] = 'forbidden'; tenantReject(fn() => Protocol::observation($bad, $intent, $request, 'instance', $origin));
$bad = $response; unset($bad['tenant_deletion']['closed_at']); tenantReject(fn() => Protocol::observation($bad, $intent, $request, 'instance', $origin));
$wrongTenant = $tenant; $wrongTenant['tenant_id'] = '8'; $wrongTenant['deletion_id'] = hash('sha256', implode("\n", ['weknora-tenant-deleted-v1', '8', $wrongTenant['deletion_time_ms']]));
$bad = $response; $bad['tenant_deletion'] = $wrongTenant; tenantReject(fn() => Protocol::observation($bad, $intent, $request, 'instance', $origin));
$otherAnchor = $fact; $otherAnchor['tenant']['operation_id'] = '10112233-4455-4677-8899-aabbccddeeff'; $otherAnchor['tenant']['binding_id'] = 'another';
tenantCheck(Protocol::sameNamespaceFact($fact, $otherAnchor), 'Same global fact from another owned anchor was altered');
tenantCheck(LedgerStore::namespaceKeyFromFact($fact) === LedgerStore::namespaceKeyFromFact($otherAnchor), 'Anchor replaced global namespace');
$foreign = $fact; $foreign['origin_sha256'] = hash('sha256', 'https://foreign.example.test');
tenantCheck(!Protocol::sameNamespaceFact($fact, $foreign) && LedgerStore::namespaceKeyFromFact($fact) !== LedgerStore::namespaceKeyFromFact($foreign), 'Same numeric Tenant on another WK server was closed');
$tenant9 = $fact; $tenant9['tenant'] = $wrongTenant; tenantCheck(Protocol::compareFacts($fact, $tenant9) < 0, 'Numeric Tenant history order changed');
// A retired old operation's target can be unknown after migration. Its valid
// owner fact does not fabricate an old-root closure or affect a new Tenant.
$retired = $response; $retired['target']['state'] = 'unverified'; $retired['target']['reason'] = 'target_unverified';
foreach (['closure_id', 'deletion_time_ms', 'closed_at'] as $field) { $retired['target'][$field] = ''; }
tenantCheck(Protocol::observation($retired, $intent, $request, 'instance', $origin) === $fact && Target::target($retired['target'])['state'] === 'unverified', 'Retired owner metadata became a root deletion');
$event = ['sequence' => '1', 'binding_id' => 'binding', 'file_id' => null, 'kind' => 'tenant_deleted', 'source_revision' => '0', 'created_at' => $now, 'tenant_deletion' => $fact];
$expected = hash('sha256', implode("\n", [Journal::ZERO_HASH, '1', 'binding', '-', 'tenant_deleted', '0', (string)$now, $fact['origin_sha256'], ...array_values($tenant)]));
tenantCheck(Journal::eventHash(Journal::ZERO_HASH, $event) === $expected && count(Protocol::journalFields($fact)) === 10, 'Tenant event ordered 17-line hash diverged');
$bad = $event; $bad['source_revision'] = '1'; tenantReject(fn() => Journal::eventHash(Journal::ZERO_HASH, $bad));
$bad = $event; $bad['binding_id'] = 'another'; tenantReject(fn() => Journal::eventHash(Journal::ZERO_HASH, $bad));
$bad = $event; $bad['file_id'] = '77'; tenantReject(fn() => Journal::eventHash(Journal::ZERO_HASH, $bad));
$bad = $event; $bad['created_at'] = $now - 301; tenantReject(fn() => Journal::eventHash(Journal::ZERO_HASH, $bad));
$targetTenant = $target; $targetTenant['reason'] = 'tenant_deleted'; $targetTenant['deletion_time_ms'] = $tenant['deletion_time_ms']; $targetTenant['closed_at'] = $tenant['closed_at'];
$targetTenant['closure_id'] = hash('sha256', implode("\n", ['weknora-target-deleted-v1', $op, '7', 'kb.with-dot', 'instance', 'binding', '4', 'tenant_deleted', $targetTenant['deletion_time_ms']]));
$root = ['target' => $targetTenant, 'root_file_id' => '77', 'root_key' => hash('sha256', "nextcloud-filecache-root-v1\0" . '77')];
tenantReject(fn() => Protocol::requireTargetCoverage([$root], [])); Protocol::requireTargetCoverage([$root], [$fact]);
echo "Independent Tenant metadata/hash/namespace, old-root immutability, retired unknown target, first-anchor equivalence and required external owner coverage passed; controlled metadata fixtures only\n";
