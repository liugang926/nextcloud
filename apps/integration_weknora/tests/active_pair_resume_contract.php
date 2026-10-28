<?php
declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/UnsupportedPublicationAclException.php';
require_once __DIR__ . '/../lib/Service/DirectoryOnboardingRequestException.php';
require_once __DIR__ . '/../lib/Service/DirectoryOnboardingRemoteRejectedException.php';
require_once __DIR__ . '/../lib/Service/SourcePairingRegistryService.php';
require_once __DIR__ . '/../lib/Service/DirectoryPublicationService.php';

use OCA\IntegrationWeknora\Service\DirectoryPublicationService;

$method = new ReflectionMethod(DirectoryPublicationService::class, 'activePairMatchesResume');
$request = ['tenant_id' => '7'];
$pair = [
    'state' => 'active',
    'operation_id' => '11111111-2222-3333-4444-555555555555',
    'binding_id' => 'dir_source',
    'instance_id' => 'instance',
    'tenant_id' => '7',
    'knowledge_base_id' => 'kb',
    'data_source_id' => 'ds',
    'key_id' => 'pair_key',
    'source_hash' => str_repeat('a', 64),
    'root_hash' => str_repeat('b', 64),
];
$args = [
    $pair, $request, $pair['operation_id'], $pair['binding_id'], $pair['knowledge_base_id'],
    $pair['instance_id'], $pair['source_hash'], $pair['root_hash'],
];
if ($method->invoke(null, ...$args) !== true) {
    throw new RuntimeException('Exact active pair was not reusable');
}

foreach ([
    ['state', 'pending'],
    ['binding_id', 'dir_other'],
    ['tenant_id', '8'],
    ['knowledge_base_id', 'kb_other'],
    ['data_source_id', ''],
    ['source_hash', str_repeat('c', 64)],
    ['root_hash', str_repeat('d', 64)],
] as [$field, $value]) {
    $changed = $pair;
    $changed[$field] = $value;
    $changedArgs = $args;
    $changedArgs[0] = $changed;
    if ($method->invoke(null, ...$changedArgs) !== false) {
        throw new RuntimeException("Mismatched active pair was reused at $field");
    }
}

echo "PASS: exact active source pair reuse and mismatch rejection\n";
