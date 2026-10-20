<?php
declare(strict_types=1);
require_once __DIR__ . '/../lib/Service/SourcePairingRegistryService.php';
require_once __DIR__ . '/../lib/Service/IdentityMappingService.php';
require_once __DIR__ . '/../lib/Service/ManagerAuthorizationProtocol.php';
use OCA\IntegrationWeknora\Service\ManagerAuthorizationProtocol as Protocol;

$scope = ['instance_id' => 'instance', 'operation_id' => '00112233-4455-4677-8899-aabbccddeeff',
    'tenant_id' => '77', 'knowledge_base_id' => 'kb.with-dot', 'data_source_id' => 'source',
    'processing_template_id' => 'template', 'directory_id' => 'ad.example.test',
    'object_guid' => '11223344-5566-4788-99aa-bbccddeeff00', 'purpose_sha256' => str_repeat('a', 64)];
$rejected = 0;
function rejectManagerScope(callable $fn): void {
    global $rejected;
    try { $fn(); } catch (InvalidArgumentException $e) { $rejected++; return; }
    throw new RuntimeException('Invalid manager control accepted');
}
if (Protocol::request('binding', $scope) !== $scope) { throw new RuntimeException('Exact manager scope refused'); }
foreach (Protocol::FIELDS as $field) {
    foreach ([null, true, 77, '', []] as $value) {
        $bad = $scope; $bad[$field] = $value;
        rejectManagerScope(fn() => Protocol::request('binding', $bad));
    }
    $bad = $scope; unset($bad[$field]); rejectManagerScope(fn() => Protocol::request('binding', $bad));
}
foreach (['0', '077', '+77', '77.0', '18446744073709551616'] as $value) {
    $bad = $scope; $bad['tenant_id'] = $value; rejectManagerScope(fn() => Protocol::request('binding', $bad));
}
foreach (['operation_id', 'object_guid', 'purpose_sha256'] as $field) {
    $bad = $scope; $bad[$field] = strtoupper($bad[$field]); rejectManagerScope(fn() => Protocol::request('binding', $bad));
}
$bad = $scope; $bad['unknown'] = 'ignored'; rejectManagerScope(fn() => Protocol::request('binding', $bad));
rejectManagerScope(fn() => Protocol::request('../foreign', $scope));
$max = $scope; $max['tenant_id'] = '18446744073709551615';
if (Protocol::request('binding', $max) !== $max) { throw new RuntimeException('Canonical uint64 scope truncated'); }
foreach ([[true, 'authorized'], [false, 'manager_denied'], [false, 'manager_unavailable']] as [$allow, $reason]) {
    $decision = Protocol::decision('binding', $scope, $allow, $reason);
    foreach ($scope as $field => $value) {
        if ($decision[$field] !== $value) { throw new RuntimeException('Manager decision changed scope'); }
    }
    if ($decision['allow'] !== $allow || $decision['reason'] !== $reason ||
        $decision['binding_id'] !== 'binding' || !is_int($decision['checked_at']) || $decision['checked_at'] < time() - 1) {
        throw new RuntimeException('Manager decision lacks exact current controls');
    }
}
rejectManagerScope(fn() => Protocol::decision('binding', $scope, true, 'manager_denied'));
rejectManagerScope(fn() => Protocol::decision('binding', $scope, false, 'authorized'));
rejectManagerScope(fn() => Protocol::decision('binding', $scope, false, 'unknown'));
foreach ([false, 0, '0'] as $open) { Protocol::requireRecoveryOpen(['closed' => $open]); }
$recoveryRejected = 0;
foreach ([false, null, [], ['closed' => true], ['closed' => 1], ['closed' => '1'],
    ['closed' => null], ['closed' => '00'], ['closed' => 2], ['closed' => 'unknown']] as $row) {
    try { Protocol::requireRecoveryOpen($row); }
    catch (UnexpectedValueException $e) { $recoveryRejected++; continue; }
    throw new RuntimeException('Unknown/closed manager recovery gate accepted');
}
echo json_encode(['controlled_contract' => 'PASS', 'invalid_scopes_rejected' => $rejected,
    'unknown_or_closed_recovery_controls_rejected' => $recoveryRejected,
    'actual_manager_or_READ_or_model_acceptance' => false], JSON_THROW_ON_ERROR) . "\n";
