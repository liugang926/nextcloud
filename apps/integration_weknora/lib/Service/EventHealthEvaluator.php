<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

/** Converts source-side diagnostics into stable, non-sensitive alert identities. */
final class EventHealthEvaluator {
    private const DELIVERY_AGE_SECONDS = 300;
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
        }
        return $alerts;
    }
}
