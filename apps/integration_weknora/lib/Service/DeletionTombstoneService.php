<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IDBConnection;

/** Exact source-version tombstones prevent delayed tasks from reopening content. */
final class DeletionTombstoneService {
    public function __construct(private IDBConnection $db) {
    }

    public function record(string $bindingId, int $fileId, ?string $etag,
        ?string $contentRevision, string $reasonCode): void {
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $bindingId) || $fileId < 1 ||
            !preg_match('/\A[a-z_]{1,64}\z/D', $reasonCode) ||
            ($etag !== null && !preg_match('/\A[A-Za-z0-9._:-]{1,256}\z/D', $etag)) ||
            ($contentRevision !== null && ($contentRevision === '' || strlen($contentRevision) > 19 ||
                !ctype_digit($contentRevision)))) {
            throw new \InvalidArgumentException('Invalid deletion tombstone');
        }
        $now = time();
        $this->db->insertIgnoreConflict('weknora_delete_tomb', [
            'binding_id' => $bindingId,
            'file_id' => $fileId,
            'source_etag' => $etag,
            'source_content_revision' => $contentRevision === null ? null : (int)$contentRevision,
            'reason_code' => $reasonCode,
            'generation' => 1,
            'observed_at' => $now,
            'access_closed' => 1,
            'cleanup_state' => 'unverified',
            'cleanup_revision' => 0,
            'updated_at' => $now,
        ]);
        $query = $this->db->getQueryBuilder();
        $query->update('weknora_delete_tomb')
            ->set('source_etag', $query->createNamedParameter($etag))
            ->set('source_content_revision', $query->createNamedParameter(
                $contentRevision === null ? null : (int)$contentRevision))
            ->set('reason_code', $query->createNamedParameter($reasonCode))
            ->set('generation', $query->createFunction('generation + 1'))
            ->set('observed_at', $query->createNamedParameter($now))
            ->set('access_closed', $query->createNamedParameter(1))
            ->set('cleanup_state', $query->createNamedParameter('unverified'))
            ->set('updated_at', $query->createNamedParameter($now))
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->andWhere($query->expr()->eq('file_id', $query->createNamedParameter($fileId)))
            ->executeStatement();
    }

    /** A newer completed source revision is a new upload/restore generation. */
    public function blocks(string $bindingId, int $fileId, ?string $contentRevision,
        ?string $etag = null): bool {
        $row = $this->find($bindingId, $fileId);
        if ($row === null) {
            return false;
        }
        $tombstoneRevision = $row['source_content_revision'];
        if ($tombstoneRevision === null || $contentRevision === null) {
            return true;
        }
        try {
            $revision = (string)$contentRevision;
            if (!ctype_digit($revision) || strlen($revision) > 19) {
                return true;
            }
            $tombstone = (int)$tombstoneRevision;
            $current = (int)$revision;
            return $current <= $tombstone ||
                ($current === $tombstone && $etag !== null &&
                    is_string($row['source_etag']) && !hash_equals((string)$row['source_etag'], $etag));
        } catch (\Throwable) {
            return true;
        }
    }

    public function find(string $bindingId, int $fileId): ?array {
        $query = $this->db->getQueryBuilder();
        $result = $query->select('*')->from('weknora_delete_tomb')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->andWhere($query->expr()->eq('file_id', $query->createNamedParameter($fileId)))
            ->setMaxResults(1)->executeQuery();
        try {
            $row = $result->fetchAssociative();
            return $row === false ? null : $row;
        } finally {
            $result->closeCursor();
        }
    }

    public function updateCleanup(string $bindingId, int $fileId, string $state,
        int $statusRevision): void {
        $normalized = match ($state) {
            'pending', 'cleanup_pending' => 'pending',
            'blocked', 'cleanup_blocked' => 'blocked',
            'running' => 'running',
            'partial', 'cleanup_partial' => 'partial',
            'verified', 'completed', 'cleanup_verified' => 'verified',
            'withdrawn' => 'withdrawn',
            'not_required' => 'withdrawn',
            'closed', 'access_closed' => 'access_closed',
            'unverified' => 'unverified',
            default => null,
        };
        if ($normalized === null || $statusRevision < 1) {
            throw new \InvalidArgumentException('Invalid cleanup state');
        }
        $current = $this->find($bindingId, $fileId);
        if ($current === null || (int)($current['cleanup_revision'] ?? 0) > $statusRevision) {
            throw new \DomainException('Cleanup revision is stale');
        }
        if ((int)($current['cleanup_revision'] ?? 0) === $statusRevision &&
            (string)$current['cleanup_state'] !== $normalized) {
            throw new \DomainException('Cleanup revision conflicts with persisted state');
        }
        $query = $this->db->getQueryBuilder();
        $updated = $query->update('weknora_delete_tomb')
            ->set('cleanup_state', $query->createNamedParameter($normalized))
            ->set('access_closed', $query->createNamedParameter(1))
            ->set('cleanup_revision', $query->createNamedParameter($statusRevision))
            ->set('updated_at', $query->createNamedParameter(time()))
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->andWhere($query->expr()->eq('file_id', $query->createNamedParameter($fileId)))
            ->andWhere($query->expr()->lte('cleanup_revision',
                $query->createNamedParameter($statusRevision)))
            ->executeStatement();
        if ($updated !== 1) {
            throw new \DomainException('Deletion tombstone is unavailable');
        }
    }
}
