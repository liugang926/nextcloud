<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IConfig;
use OCP\IDBConnection;

/**
 * Persisted file-change hints. The global row lock makes auto-increment order
 * match commit order, so a reader cannot checkpoint past an uncommitted event.
 * File events still are not atomic with every storage backend; a complete
 * manifest reconciliation is required to recover missed events.
 */
final class ChangeOutboxService {
    public const PAGE_SIZE = 200;
    public const MIN_RETENTION_DAYS = 30;
    private const PRUNE_BATCH_SIZE = 1000;

    private const TYPES = [
        'upsert' => true,
        'metadata' => true,
        'delete' => true,
        'subtree_scan' => true,
        'subtree_moved' => true,
        'subtree_deleted' => true,
        'reconcile' => true,
    ];

    public function __construct(private IDBConnection $db, private IConfig $config) {
    }

    public function append(
        string $bindingId,
        ?int $fileId,
        string $type,
        ?string $oldPath = null,
        ?string $path = null,
        ?string $etag = null,
    ): int {
        self::assertBindingId($bindingId);
        if ($fileId !== null && $fileId < 1) {
            throw new \InvalidArgumentException('Invalid file ID');
        }
        if (!isset(self::TYPES[$type]) || ($type === 'delete' && $fileId === null)) {
            throw new \InvalidArgumentException('Invalid change type or identity');
        }
        if ($etag !== null && strlen($etag) > 255) {
            throw new \InvalidArgumentException('ETag is too long');
        }

        $this->db->beginTransaction();
        try {
            $this->lockOutbox();

            $insert = $this->db->getQueryBuilder();
            $insert->insert('weknora_outbox')->values([
                'binding_id' => $insert->createNamedParameter($bindingId),
                'file_id' => $insert->createNamedParameter($fileId),
                'event_type' => $insert->createNamedParameter($type),
                'old_path' => $insert->createNamedParameter($oldPath),
                'path' => $insert->createNamedParameter($path),
                'etag' => $insert->createNamedParameter($etag),
                'created_at' => $insert->createNamedParameter(time()),
            ]);
            $insert->executeStatement();
            $id = $this->db->lastInsertId('weknora_outbox');
            $this->db->commit();
            try {
                // Wake a healthy idle sender after releasing the global
                // outbox lock. A sender may hold its own row while doing a
                // bounded HTTP request; no file writer should hold up all
                // other bindings' outbox commits while waiting for that row.
                $wake = $this->db->getQueryBuilder();
                $wake->update('weknora_event_conn')
                    ->set('next_attempt_at', $wake->createNamedParameter(0))
                    ->where($wake->expr()->eq('binding_id', $wake->createNamedParameter($bindingId)))
                    ->andWhere($wake->expr()->eq('status', $wake->createNamedParameter('active')))
                    ->andWhere($wake->expr()->eq('last_error_code', $wake->createNamedParameter('')))
                    ->executeStatement();
            } catch (\Throwable $exception) {
                // The hint is already durable. Scheduled polling still
                // catches it if this opportunistic wake fails.
            }
            return $id;
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /**
     * @return array{expired: bool, items: list<array<string, mixed>>, next_id: int, has_more: bool}
     */
    public function page(string $bindingId, int $afterId): array {
        self::assertBindingId($bindingId);
        if ($afterId < 0) {
            throw new \InvalidArgumentException('Invalid change cursor');
        }
        // The floor and page must be one locked observation. Otherwise a
        // prune between the two SELECTs could return a false empty page.
        $this->db->beginTransaction();
        try {
            $this->lockOutbox();
            $page = $this->readPage($bindingId, $afterId);
            $this->db->commit();
            return $page;
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /**
     * Delete at most one batch of expired events per binding per invocation.
     * A newer event in a binding stops that binding's deletion prefix, even
     * when a later event has an older timestamp. This keeps floor_id honest.
     */
    public function pruneExpired(?int $now = null): int {
        $cutoff = $this->retentionCutoff($now);
        $bindingsQuery = $this->db->getQueryBuilder();
        $bindingsQuery->select('binding_id')->from('weknora_outbox')
            ->where($bindingsQuery->expr()->lt('created_at',
                $bindingsQuery->createNamedParameter($cutoff)))
            ->groupBy('binding_id')->orderBy('binding_id', 'ASC');
        $result = $bindingsQuery->executeQuery();
        try {
            $bindingIds = $result->fetchFirstColumn();
        } finally {
            $result->closeCursor();
        }

        $removed = 0;
        foreach ($bindingIds as $bindingId) {
            $removed += $this->pruneBindingPrefix((string)$bindingId, $cutoff);
        }
        return $removed;
    }

    /** The scoped entry point also keeps local integration tests isolated. */
    public function pruneExpiredBinding(string $bindingId, ?int $now = null): int {
        self::assertBindingId($bindingId);
        return $this->pruneBindingPrefix($bindingId, $this->retentionCutoff($now));
    }

    private function retentionCutoff(?int $now): int {
        $rawDays = $this->config->getAppValue('integration_weknora', 'outbox_retention_days', '30');
        $configuredDays = filter_var($rawDays, FILTER_VALIDATE_INT, [
            'options' => ['min_range' => 0, 'max_range' => 36500],
        ]);
        if ($configuredDays === false) {
            // An invalid operator setting must not unexpectedly shorten a
            // requested retention period.
            throw new \UnexpectedValueException('Invalid outbox retention period');
        }
        $days = max(self::MIN_RETENTION_DAYS, $configuredDays);
        return ($now ?? time()) - ($days * 86400);
    }

    private function pruneBindingPrefix(string $bindingId, int $cutoff): int {
        $this->db->beginTransaction();
        try {
            $this->lockOutbox();
            // Only the separately verified consumer-applied watermark allows
            // a connected binding's hints to expire. A durable 202 receipt
            // never establishes publication. Pairing takes the same outbox lock
            // when it checks the floor, avoiding a first-pairing prune race.
            $senderQuery = $this->db->getQueryBuilder();
            $senderQuery->select('received_id', 'applied_id', 'applied_error_code')
                ->from('weknora_event_conn')
                ->where($senderQuery->expr()->eq('binding_id',
                    $senderQuery->createNamedParameter($bindingId)));
            $senderResult = $senderQuery->executeQuery();
            try {
                $sender = $senderResult->fetchAssociative();
            } finally {
                $senderResult->closeCursor();
            }
            $query = $this->db->getQueryBuilder();
            $query->select('id', 'created_at')->from('weknora_outbox')
                ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
                ->orderBy('id', 'ASC')->setMaxResults(self::PRUNE_BATCH_SIZE);
            $result = $query->executeQuery();
            try {
                $rows = $result->fetchAllAssociative();
            } finally {
                $result->closeCursor();
            }

            $count = 0;
            $lastId = 0;
            foreach ($rows as $row) {
                if ((int)$row['created_at'] >= $cutoff ||
                    ($sender !== false &&
                        ((string)$sender['applied_error_code'] !== '' ||
                         (int)$row['id'] > min((int)$sender['received_id'],
                            (int)$sender['applied_id'])))) {
                    break;
                }
                $lastId = (int)$row['id'];
                $count++;
            }
            if ($count === 0) {
                $this->db->commit();
                return 0;
            }

            $floorQuery = $this->db->getQueryBuilder();
            $floorQuery->select('floor_id')->from('weknora_change_floor')
                ->where($floorQuery->expr()->eq('binding_id',
                    $floorQuery->createNamedParameter($bindingId)));
            $floorResult = $floorQuery->executeQuery();
            try {
                $floor = $floorResult->fetchOne();
            } finally {
                $floorResult->closeCursor();
            }
            if ($floor === false) {
                $this->db->insertIgnoreConflict('weknora_change_floor', [
                    'binding_id' => $bindingId,
                    'floor_id' => $lastId,
                ]);
            }
            $update = $this->db->getQueryBuilder();
            $update->update('weknora_change_floor')
                ->set('floor_id', $update->createNamedParameter($lastId))
                ->where($update->expr()->eq('binding_id', $update->createNamedParameter($bindingId)))
                ->andWhere($update->expr()->lt('floor_id', $update->createNamedParameter($lastId)))
                ->executeStatement();

            $delete = $this->db->getQueryBuilder();
            $deleted = $delete->delete('weknora_outbox')
                ->where($delete->expr()->eq('binding_id', $delete->createNamedParameter($bindingId)))
                ->andWhere($delete->expr()->lte('id', $delete->createNamedParameter($lastId)))
                ->executeStatement();
            if ($deleted !== $count) {
                throw new \UnexpectedValueException('Outbox deletion prefix changed');
            }
            $this->db->commit();
            return $deleted;
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /** @return array{expired: bool, items: list<array<string, mixed>>, next_id: int, has_more: bool} */
    private function readPage(string $bindingId, int $afterId): array {
        $floorQuery = $this->db->getQueryBuilder();
        $floorQuery->select('floor_id')->from('weknora_change_floor')
            ->where($floorQuery->expr()->eq('binding_id', $floorQuery->createNamedParameter($bindingId)));
        $floorResult = $floorQuery->executeQuery();
        try {
            $floor = $floorResult->fetchOne();
        } finally {
            $floorResult->closeCursor();
        }
        if ($afterId < ($floor === false ? 0 : (int)$floor)) {
            // The consumer may use this checkpoint only after a successful
            // complete manifest reconciliation. Events above the floor stay
            // replayable, including those created during that scan.
            return ['expired' => true, 'items' => [], 'next_id' => (int)$floor, 'has_more' => false];
        }

        $query = $this->db->getQueryBuilder();
        $query->select('id', 'file_id', 'event_type', 'old_path', 'path', 'etag', 'created_at')
            ->from('weknora_outbox')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->andWhere($query->expr()->gt('id', $query->createNamedParameter($afterId)))
            ->orderBy('id', 'ASC')
            ->setMaxResults(self::PAGE_SIZE + 1);
        $result = $query->executeQuery();
        try {
            $rows = $result->fetchAllAssociative();
        } finally {
            $result->closeCursor();
        }
        $hasMore = count($rows) > self::PAGE_SIZE;
        if ($hasMore) {
            array_pop($rows);
        }
        $items = [];
        $nextId = $afterId;
        foreach ($rows as $row) {
            $nextId = (int)$row['id'];
            $items[] = [
                'event_id' => (string)$row['id'],
                'file_id' => $row['file_id'] === null ? null : (int)$row['file_id'],
                'type' => $row['event_type'],
                'old_path' => $row['old_path'],
                'path' => $row['path'],
                'etag' => $row['etag'],
                'created_at' => (int)$row['created_at'],
            ];
        }
        return ['expired' => false, 'items' => $items, 'next_id' => $nextId, 'has_more' => $hasMore];
    }

    private function lockOutbox(): void {
        $this->db->insertIgnoreConflict('weknora_outbox_lock', ['id' => 1]);
        $lock = $this->db->getQueryBuilder();
        $lock->select('id')->from('weknora_outbox_lock')
            ->where($lock->expr()->eq('id', $lock->createNamedParameter(1)))
            ->forUpdate();
        $result = $lock->executeQuery();
        try {
            if ($result->fetchOne() === false) {
                throw new \UnexpectedValueException('Outbox lock is missing');
            }
        } finally {
            $result->closeCursor();
        }
    }

    private static function assertBindingId(string $bindingId): void {
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $bindingId)) {
            throw new \InvalidArgumentException('Invalid binding ID');
        }
    }
}
