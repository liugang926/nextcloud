<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IDBConnection;

/** Small, administrator-only source-side diagnostics. */
final class OperationalStatusService {
    public function __construct(
        private IDBConnection $db,
        private BindingRegistryService $bindings,
    ) {
    }

    /** @return array<string, mixed> */
    public function snapshot(): array {
        $now = time();
        $registry = $this->bindings->listBindings();
        $rootsAvailable = true;
        if ($registry !== []) {
            try {
                // requireActiveRoot checks every configured root and overlap.
                $this->bindings->requireActiveRoot($registry[0]['id']);
            } catch (\Throwable $exception) {
                $rootsAvailable = false;
            }
        }

        $eventsQuery = $this->db->getQueryBuilder();
        $eventsQuery->select($eventsQuery->func()->count('', 'retained_count'))
            ->selectAlias($eventsQuery->func()->min('created_at'), 'oldest_at')
            ->selectAlias($eventsQuery->func()->max('created_at'), 'newest_at')
            ->selectAlias($eventsQuery->func()->max('id'), 'newest_id')
            ->from('weknora_outbox');
        $eventsResult = $eventsQuery->executeQuery();
        try {
            $events = $eventsResult->fetchAssociative();
        } finally {
            $eventsResult->closeCursor();
        }
        if ($events === false) {
            throw new \UnexpectedValueException('Change journal unavailable');
        }

        $withdrawnQuery = $this->db->getQueryBuilder();
        $withdrawnQuery->select($withdrawnQuery->func()->count('', 'withdrawn_count'))
            ->from('weknora_pub_state')
            ->where($withdrawnQuery->expr()->eq('state',
                $withdrawnQuery->createNamedParameter('withdrawn')));
        $withdrawnResult = $withdrawnQuery->executeQuery();
        try {
            $withdrawn = $withdrawnResult->fetchOne();
        } finally {
            $withdrawnResult->closeCursor();
        }
        if ($withdrawn === false) {
            throw new \UnexpectedValueException('Publication state unavailable');
        }

        // List only non-sensitive sender state. Never select the encrypted
        // credential, receiver URL or file/path fields for diagnostics.
        $connectionsQuery = $this->db->getQueryBuilder();
        $connectionsQuery->select('binding_id', 'status', 'received_id', 'applied_id',
                'applied_checked_at', 'applied_error_code', 'attempt_count',
                'next_attempt_at', 'last_error_code')
            ->from('weknora_event_conn')->orderBy('binding_id', 'ASC');
        $connectionsResult = $connectionsQuery->executeQuery();
        try {
            $connectionRows = $connectionsResult->fetchAllAssociative();
        } finally {
            $connectionsResult->closeCursor();
        }

        // A hint is locally pending only while a configured sender has not
        // received a durable receipt for its ID. The existing
        // weknora_outbox_cursor(binding_id, id) index supports this join.
        // Unconfigured bindings and already received retained hints are not
        // counted as a delivery backlog.
        $pendingQuery = $this->db->getQueryBuilder();
        $pendingQuery->select('c.binding_id')
            ->selectAlias($pendingQuery->func()->count('o.id'), 'pending_count')
            ->selectAlias($pendingQuery->func()->min('o.created_at'), 'oldest_pending_at')
            ->from('weknora_event_conn', 'c')
            ->innerJoin('c', 'weknora_outbox', 'o',
                $pendingQuery->expr()->andX(
                    $pendingQuery->expr()->eq('o.binding_id', 'c.binding_id'),
                    $pendingQuery->expr()->gt('o.id', 'c.received_id'),
                ))
            ->groupBy('c.binding_id');
        $pendingResult = $pendingQuery->executeQuery();
        try {
            $pendingRows = $pendingResult->fetchAllAssociative();
        } finally {
            $pendingResult->closeCursor();
        }
        $pendingByBinding = [];
        foreach ($pendingRows as $row) {
            $pendingByBinding[(string)$row['binding_id']] = $row;
        }

        $ackAvailable = false;
        $pendingCount = 0;
        $oldestPendingAt = null;
        $connectionStatuses = [];
        foreach ($connectionRows as $row) {
            $bindingId = (string)$row['binding_id'];
            $pending = $pendingByBinding[$bindingId] ?? null;
            $count = $pending === null ? 0 : (int)$pending['pending_count'];
            $pendingAt = $pending === null ? null : (int)$pending['oldest_pending_at'];
            $pendingCount += $count;
            if ($pendingAt !== null && ($oldestPendingAt === null || $pendingAt < $oldestPendingAt)) {
                $oldestPendingAt = $pendingAt;
            }
            $ackAvailable = $ackAvailable ||
                ((string)$row['status'] === 'active' &&
                 (int)$row['applied_checked_at'] > 0 &&
                 (string)$row['applied_error_code'] === '');
            $connectionStatuses[] = [
                'binding_id' => $bindingId,
                'status' => (string)$row['status'],
                'received_through_event_id' => (string)$row['received_id'],
                'applied_through_event_id' => (string)$row['applied_id'],
                'applied_checked_at' => (int)$row['applied_checked_at'],
                'applied_error_code' => (string)$row['applied_error_code'],
                'attempt_count' => (int)$row['attempt_count'],
                'next_attempt_at' => (int)$row['next_attempt_at'],
                'last_error_code' => (string)$row['last_error_code'],
                'outbox_pending_delivery_hints' => $count,
                'oldest_outbox_pending_delivery_age_seconds' => $pendingAt === null
                    ? null : max(0, $now - $pendingAt),
            ];
        }

        $oldest = $events['oldest_at'] === null ? null : (int)$events['oldest_at'];
        return [
            'checked_at' => $now,
            'binding_count' => count($registry),
            'binding_roots_available' => $rootsAvailable,
            'retained_change_hints' => (int)$events['retained_count'],
            'oldest_retained_hint_age_seconds' => $oldest === null ? null : max(0, $now - $oldest),
            'newest_change_hint_at' => $events['newest_at'] === null ? null : (int)$events['newest_at'],
            'newest_change_hint_id' => $events['newest_id'] === null ? null : (int)$events['newest_id'],
            'explicit_withdrawal_count' => (int)$withdrawn,
            'consumer_acknowledgement_available' => $ackAvailable,
            'outbox_pending_delivery_hints' => $pendingCount,
            'oldest_outbox_pending_delivery_age_seconds' => $oldestPendingAt === null
                ? null : max(0, $now - $oldestPendingAt),
            'event_connections' => $connectionStatuses,
        ];
    }
}
