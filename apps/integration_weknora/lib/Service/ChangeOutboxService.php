<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IDBConnection;

/**
 * Persisted file-change hints. The global row lock makes auto-increment order
 * match commit order, so a reader cannot checkpoint past an uncommitted event.
 * File events still are not atomic with every storage backend; a complete
 * manifest reconciliation is required to recover missed events.
 */
final class ChangeOutboxService {
    public const PAGE_SIZE = 200;

    private const TYPES = [
        'upsert' => true,
        'metadata' => true,
        'delete' => true,
        'subtree_scan' => true,
        'subtree_moved' => true,
        'subtree_deleted' => true,
        'reconcile' => true,
    ];

    public function __construct(private IDBConnection $db) {
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

    private static function assertBindingId(string $bindingId): void {
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $bindingId)) {
            throw new \InvalidArgumentException('Invalid binding ID');
        }
    }
}
