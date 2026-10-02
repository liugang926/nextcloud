<?php

declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/EventHealthEvaluator.php';

use OCA\IntegrationWeknora\Service\EventHealthEvaluator;

function expectAlerts(array $actual, array $expected): void {
    if ($actual !== $expected) {
        throw new RuntimeException('Unexpected event health alert identities: ' . json_encode($actual));
    }
}

$now = 1_800_000_000;
$healthy = [
    'checked_at' => $now,
    'binding_roots_available' => true,
    'event_connections' => [[
        'binding_id' => 'engineering',
        'connection_created_at' => $now - 299,
        'status' => 'active',
        'outbox_pending_delivery_hints' => 1,
        'oldest_outbox_pending_delivery_age_seconds' => 299,
        'outbox_pending_application_hints' => 0,
        'oldest_outbox_pending_application_age_seconds' => null,
        'applied_checked_at' => $now - 299,
        'applied_error_code' => '',
    ]],
];
expectAlerts(EventHealthEvaluator::evaluate($healthy), []);

$neverChecked = $healthy;
$neverChecked['event_connections'][0]['applied_checked_at'] = 0;
$neverChecked['event_connections'][0]['connection_created_at'] = $now - 300;
expectAlerts(EventHealthEvaluator::evaluate($neverChecked), [
    ['binding_id' => 'engineering', 'code' => 'applied_status_unverified'],
]);

$applicationStuck = $healthy;
$applicationStuck['event_connections'][0]['outbox_pending_delivery_hints'] = 0;
$applicationStuck['event_connections'][0]['oldest_outbox_pending_delivery_age_seconds'] = null;
$applicationStuck['event_connections'][0]['outbox_pending_application_hints'] = 2;
$applicationStuck['event_connections'][0]['oldest_outbox_pending_application_age_seconds'] = 299;
$applicationStuck['event_connections'][0]['receiver_url'] = 'https://private.example.invalid/source';
$applicationStuck['event_connections'][0]['secret_ciphertext'] = 'never-log-this';
expectAlerts(EventHealthEvaluator::evaluate($applicationStuck), []);
$applicationStuck['event_connections'][0]['oldest_outbox_pending_application_age_seconds'] = 300;
expectAlerts(EventHealthEvaluator::evaluate($applicationStuck), [
    ['binding_id' => 'engineering', 'code' => 'application_overdue'],
]);
$applicationStuck['event_connections'][0]['applied_error_code'] = 'status_transport_error';
expectAlerts(EventHealthEvaluator::evaluate($applicationStuck), [
    ['binding_id' => 'engineering', 'code' => 'applied_status_failed'],
]);
$applicationStuck['event_connections'][0]['applied_error_code'] = '';
$applicationStuck['event_connections'][0]['status'] = 'paused';
expectAlerts(EventHealthEvaluator::evaluate($applicationStuck), [
    ['binding_id' => 'engineering', 'code' => 'sender_paused'],
]);
$applicationStuck['event_connections'][0]['status'] = 'active';
$applicationStuck['event_connections'][0]['applied_checked_at'] = $now - 300;
expectAlerts(EventHealthEvaluator::evaluate($applicationStuck), [
    ['binding_id' => 'engineering', 'code' => 'applied_status_stale'],
]);
$applicationStuck['event_connections'][0]['applied_checked_at'] = $now - 1;
$applicationStuck['event_connections'][0]['outbox_pending_application_hints'] = 0;
$applicationStuck['event_connections'][0]['oldest_outbox_pending_application_age_seconds'] = null;
expectAlerts(EventHealthEvaluator::evaluate($applicationStuck), []);

$unhealthy = $healthy;
$unhealthy['binding_roots_available'] = false;
$unhealthy['event_connections'][0]['status'] = 'paused';
$unhealthy['event_connections'][0]['oldest_outbox_pending_delivery_age_seconds'] = 300;
$unhealthy['event_connections'][0]['applied_checked_at'] = $now - 300;
$unhealthy['event_connections'][0]['applied_error_code'] = 'status_transport_error';
expectAlerts(EventHealthEvaluator::evaluate($unhealthy), [
    ['binding_id' => '', 'code' => 'binding_root_unavailable'],
    ['binding_id' => 'engineering', 'code' => 'sender_paused'],
    ['binding_id' => 'engineering', 'code' => 'delivery_overdue'],
]);

$unhealthy['event_connections'][0]['status'] = 'active';
expectAlerts(EventHealthEvaluator::evaluate($unhealthy), [
    ['binding_id' => '', 'code' => 'binding_root_unavailable'],
    ['binding_id' => 'engineering', 'code' => 'delivery_overdue'],
    ['binding_id' => 'engineering', 'code' => 'applied_status_failed'],
    ['binding_id' => 'engineering', 'code' => 'applied_status_stale'],
]);

echo "event health evaluator contract passed\n";
