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
            'consumer_acknowledgement_available' => false,
        ];
    }
}
