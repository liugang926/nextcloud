<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

/** Converts source-side diagnostics into stable, non-sensitive alert identities. */
final class EventHealthEvaluator {
    private const DELIVERY_AGE_SECONDS = 300;
    private const APPLICATION_AGE_SECONDS = 300;
    private const STATUS_AGE_SECONDS = 300;

    /**
     * @param array<string, mixed> $snapshot OperationalStatusService::snapshot()
     * @return list<array{binding_id: string, code: string}>
     */
    public static function evaluate(array $snapshot): array {
        $alerts = [];
        if (($snapshot['binding_roots_available'] ?? false) !== true) {
            $alerts[] = ['binding_id' => '', 'code' => 'binding_root_unavailable'];
        }

        foreach ($snapshot['event_connections'] ?? [] as $connection) {
            $bindingId = (string)$connection['binding_id'];
            if ($connection['status'] === 'paused') {
                $alerts[] = ['binding_id' => $bindingId, 'code' => 'sender_paused'];
            }
            if ((int)$connection['outbox_pending_delivery_hints'] > 0 &&
                (int)$connection['oldest_outbox_pending_delivery_age_seconds'] >= self::DELIVERY_AGE_SECONDS) {
                $alerts[] = ['binding_id' => $bindingId, 'code' => 'delivery_overdue'];
            }
            if ($connection['status'] !== 'active') {
                continue;
            }
            $checkedAt = (int)$connection['applied_checked_at'];
            if ($checkedAt === 0 &&
                (int)$snapshot['checked_at'] - (int)$connection['connection_created_at'] >= self::STATUS_AGE_SECONDS) {
                $alerts[] = ['binding_id' => $bindingId, 'code' => 'applied_status_unverified'];
            }
            if ($checkedAt > 0 && (string)$connection['applied_error_code'] !== '') {
                $alerts[] = ['binding_id' => $bindingId, 'code' => 'applied_status_failed'];
            }
            if ($checkedAt > 0 &&
                (int)$snapshot['checked_at'] - $checkedAt >= self::STATUS_AGE_SECONDS) {
                $alerts[] = ['binding_id' => $bindingId, 'code' => 'applied_status_stale'];
            }
            if ($checkedAt > 0 &&
                (string)$connection['applied_error_code'] === '' &&
                (int)$snapshot['checked_at'] - $checkedAt < self::STATUS_AGE_SECONDS &&
                (int)$connection['outbox_pending_application_hints'] > 0 &&
                (int)$connection['oldest_outbox_pending_application_age_seconds'] >= self::APPLICATION_AGE_SECONDS) {
                $alerts[] = ['binding_id' => $bindingId, 'code' => 'application_overdue'];
            }
        }
        $processing = $snapshot['processing'] ?? null;
        if (is_array($processing)) {
            if (($processing['stale_status_count'] ?? 0) > 0) {
                $alerts[] = ['binding_id' => '', 'code' => 'processing_status_stale'];
            }
            if (($processing['permission_unverified_count'] ?? 0) > 0) {
                $alerts[] = ['binding_id' => '', 'code' => 'processing_permission_unverified'];
            }
            if (($processing['graph_failure_count'] ?? 0) > 0) {
                $alerts[] = ['binding_id' => '', 'code' => 'graph_processing_failed'];
            }
            if (($processing['oldest_pending_directory_age_seconds'] ?? 0) >= 300) {
                $alerts[] = ['binding_id' => '', 'code' => 'directory_onboarding_overdue'];
            }
            if (($processing['oldest_directory_discovery_age_seconds'] ?? 0) >= 10) {
                $alerts[] = ['binding_id' => '', 'code' => 'directory_discovery_overdue'];
            }
        }
        return $alerts;
    }
}
