<?php

declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/EventReceiverPolicy.php';
require_once __DIR__ . '/../lib/Service/EventAppliedStatusService.php';

use OCA\IntegrationWeknora\Service\EventAppliedStatusService;

$secret = '0123456789abcdef0123456789abcdef';
$connectionId = '8c43deee-99c9-412f-9488-a9dfd3b453c9';
$query = 'connection_id=' . $connectionId;
$canonical = EventAppliedStatusService::canonicalRequest(
    $query, '1700000000', str_repeat('a', 32), $connectionId, 'current');
$signature = hash_hmac('sha256', $canonical, $secret);
if ($signature !== '20e611023face80ba3e681e4aabe8a72ed9cff0fb76d9f9d40b51a0a843ad54b' ||
    substr_count($canonical, "\n") !== 8 || str_ends_with($canonical, "\n")) {
    throw new RuntimeException('WeKnora GET status HMAC contract changed');
}

$changed = EventAppliedStatusService::canonicalRequest(
    $query . '&extra=1', '1700000000', str_repeat('a', 32), $connectionId, 'current');
if (hash_equals($signature, hash_hmac('sha256', $changed, $secret))) {
    throw new RuntimeException('Query is missing from status signature');
}
echo "event status signature contract passed\n";
