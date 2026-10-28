<?php
declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/RootMigrationService.php';

use OCA\IntegrationWeknora\Service\RootMigrationService;

$method = new ReflectionMethod(RootMigrationService::class, 'quarantineUnavailableStoppedRoot');
if ($method->invoke(null, ['publication_state' => 'stopped']) !== true) {
    throw new RuntimeException('Unavailable stopped root was not quarantined');
}
foreach ([
    ['publication_state' => 'active'],
    ['publication_state' => ''],
    [],
] as $binding) {
    if ($method->invoke(null, $binding) !== false) {
        throw new RuntimeException('Unavailable non-stopped root escaped blocking');
    }
}

echo "PASS: unavailable stopped roots are quarantined; active or unknown roots still block\n";
