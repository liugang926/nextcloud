<?php
declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/RootMigrationService.php';
require_once __DIR__ . '/../lib/Service/MachineKeyRegistryService.php';

use OCA\IntegrationWeknora\Service\RootMigrationService;

$method = new ReflectionMethod(RootMigrationService::class, 'exactStalePrepared');
$hash = str_repeat('a', 64);
$binding = [
    'id' => 'dir_source',
    'owner_uid' => 'owner',
    'root_file_id' => 2429,
    'publication_state' => 'active',
    'publication_epoch' => 6,
];
$pair = [
    'operation_id' => '11111111-2222-3333-4444-555555555555',
    'binding_id' => 'dir_source',
    'instance_id' => 'instance',
    'tenant_id' => '7',
    'knowledge_base_id' => 'kb',
    'data_source_id' => 'ds',
    'source_hash' => hash('sha256', "dir_source\0owner\0" . '2429'),
    'root_hash' => $hash,
    'state' => 'active',
];
$intent = [
    'operation_id' => $pair['operation_id'],
    'binding_id' => 'dir_source',
    'root_key' => str_repeat('b', 64),
    'root_file_id' => 2429,
    'rule_id' => 'rule',
    'owner_uid' => 'owner',
    'state' => 'blocked',
    'error_code' => 'publication_stopped',
    'request_json' => json_encode([
        'operation_id' => $pair['operation_id'],
        'tenant_id' => '7',
        'instance_id' => 'instance',
        'binding_id' => 'dir_source',
        'knowledge_base_id' => 'kb',
    ], JSON_THROW_ON_ERROR),
];
$move = [
    'operation_id' => '99999999-2222-3333-4444-555555555555',
    'pair_operation_id' => $pair['operation_id'],
    'binding_id' => 'dir_source',
    'instance_id' => 'instance',
    'tenant_id' => '7',
    'knowledge_base_id' => 'kb',
    'data_source_id' => 'ds',
    'physical_root_key' => $intent['root_key'],
    'root_file_id' => 2429,
    'rule_id' => 'rule',
    'owner_uid' => 'owner',
    'old_root_hash' => $hash,
    'new_root_hash' => '',
    'prepared_epoch' => 5,
    'state' => 'prepared',
];

if ($method->invoke(null, $move, $binding, $pair, $intent, $hash) !== true) {
    throw new RuntimeException('Exact stale prepared root move was not recoverable');
}
foreach ([
    ['binding', 'publication_epoch', 5],
    ['binding', 'publication_state', 'stopped'],
    ['pair', 'state', 'pending'],
    ['pair', 'root_hash', str_repeat('c', 64)],
    ['pair', 'source_hash', str_repeat('e', 64)],
    ['intent', 'state', 'active'],
    ['intent', 'error_code', 'other'],
    ['move', 'physical_root_key', str_repeat('d', 64)],
    ['move', 'root_file_id', 2430],
    ['move', 'pair_operation_id', '22222222-2222-3333-4444-555555555555'],
    ['move', 'new_root_hash', $hash],
] as [$target, $field, $value]) {
    $changedMove = $move;
    $changedBinding = $binding;
    $changedPair = $pair;
    $changedIntent = $intent;
    ${'changed' . ucfirst($target)}[$field] = $value;
    if ($method->invoke(null, $changedMove, $changedBinding, $changedPair, $changedIntent, $hash) !== false) {
        throw new RuntimeException("Stale root move was superseded with mismatch at $target.$field");
    }
}

echo "PASS: exact stale root move supersede and mismatch rejection\n";
