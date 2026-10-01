<?php

declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/EmployeeFileStatusService.php';

use OCA\IntegrationWeknora\Service\EmployeeFileStatusService;

$method = new ReflectionMethod(EmployeeFileStatusService::class, 'sameRemoteObservation');
$method->setAccessible(true);
$matches = static fn (array $before, array $after): bool =>
    $method->invoke(null, $before, $after);

$before = [
    'source_state' => 'in_scope',
    '_binding_id' => 'engineering',
    '_publication_epoch' => 7,
    '_publication_revision' => 10,
    'source_etag' => 'same-etag',
];
if (!$matches($before, $before)) {
    throw new RuntimeException('Unchanged publication rejected a current status');
}
foreach ([
    ['_publication_epoch' => 9], // Stop and resume crossed the remote request.
    ['_publication_revision' => 12], // Withdraw and republish crossed it.
    ['source_state' => 'withdrawn'],
    ['_binding_id' => 'another-binding'],
    ['source_etag' => 'new-etag'],
] as $change) {
    if ($matches($before, array_replace($before, $change))) {
        throw new RuntimeException('Stale remote status accepted after a source change');
    }
}

echo "employee file status observation contract passed\n";
