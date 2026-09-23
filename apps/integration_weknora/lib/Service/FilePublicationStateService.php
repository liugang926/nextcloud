<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IDBConnection;

/**
 * Persistent publication decisions keyed by binding and Nextcloud file ID.
 * A withdrawn file stays excluded across scans and app restarts until an
 * administrator explicitly republishes it.
 */
final class FilePublicationStateService {
    private const STATE_ELIGIBLE = 'eligible';
    private const STATE_WITHDRAWN = 'withdrawn';

    /** @var array<string, array<int, true>> */
    private array $excludedByBinding = [];

    public function __construct(private IDBConnection $db) {
    }

    public function isExcluded(string $bindingId, int $fileId): bool {
        self::assertIdentity($bindingId, $fileId);
        if (!isset($this->excludedByBinding[$bindingId])) {
            $query = $this->db->getQueryBuilder();
            $query->select('file_id', 'state')
                ->from('weknora_pub_state')
                ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)));
            $result = $query->executeQuery();
            try {
                $excluded = [];
                while (($row = $result->fetchAssociative()) !== false) {
                    if ($row['state'] === self::STATE_WITHDRAWN) {
                        $excluded[(int)$row['file_id']] = true;
                    } elseif ($row['state'] !== self::STATE_ELIGIBLE) {
                        throw new \UnexpectedValueException('Invalid publication state');
                    }
                }
                $this->excludedByBinding[$bindingId] = $excluded;
            } finally {
                $result->closeCursor();
            }
        }
        return isset($this->excludedByBinding[$bindingId][$fileId]);
    }

    public function getState(string $bindingId, int $fileId): string {
        self::assertIdentity($bindingId, $fileId);
        $query = $this->db->getQueryBuilder();
        $query->select('state')
            ->from('weknora_pub_state')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->andWhere($query->expr()->eq('file_id', $query->createNamedParameter($fileId)));
        $result = $query->executeQuery();
        try {
            $state = $result->fetchOne();
        } finally {
            $result->closeCursor();
        }
        if ($state === false) {
            return self::STATE_ELIGIBLE;
        }
        if ($state !== self::STATE_ELIGIBLE && $state !== self::STATE_WITHDRAWN) {
            // Unknown stored states must never expose source content.
            throw new \UnexpectedValueException('Invalid publication state');
        }
        return $state;
    }

    public function hasRecordedState(string $bindingId, int $fileId): bool {
        self::assertIdentity($bindingId, $fileId);
        $query = $this->db->getQueryBuilder();
        $query->select('id')
            ->from('weknora_pub_state')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->andWhere($query->expr()->eq('file_id', $query->createNamedParameter($fileId)));
        $result = $query->executeQuery();
        try {
            return $result->fetchOne() !== false;
        } finally {
            $result->closeCursor();
        }
    }

    public function withdraw(string $bindingId, int $fileId, string $actorUid): void {
        $this->setState($bindingId, $fileId, $actorUid, self::STATE_WITHDRAWN);
    }

    public function republish(string $bindingId, int $fileId, string $actorUid): void {
        $this->setState($bindingId, $fileId, $actorUid, self::STATE_ELIGIBLE);
    }

    private function setState(string $bindingId, int $fileId, string $actorUid, string $state): void {
        self::assertIdentity($bindingId, $fileId);
        if ($actorUid === '' || strlen($actorUid) > 64) {
            throw new \InvalidArgumentException('Invalid actor');
        }

        $timestamp = time();
        $this->db->beginTransaction();
        try {
            // PostgreSQL aborts a transaction after a uniqueness error, so
            // insert-if-absent before the update instead of setValues().
            $this->db->insertIgnoreConflict('weknora_pub_state', [
                'binding_id' => $bindingId,
                'file_id' => $fileId,
                'state' => $state,
                'actor_uid' => $actorUid,
                'updated_at' => $timestamp,
            ]);
            $update = $this->db->getQueryBuilder();
            $update->update('weknora_pub_state')
                ->set('state', $update->createNamedParameter($state))
                ->set('actor_uid', $update->createNamedParameter($actorUid))
                ->set('updated_at', $update->createNamedParameter($timestamp))
                ->where($update->expr()->eq('binding_id', $update->createNamedParameter($bindingId)))
                ->andWhere($update->expr()->eq('file_id', $update->createNamedParameter($fileId)));
            $update->executeStatement();

            $query = $this->db->getQueryBuilder();
            $query->insert('weknora_pub_audit')->values([
                'binding_id' => $query->createNamedParameter($bindingId),
                'file_id' => $query->createNamedParameter($fileId),
                'action' => $query->createNamedParameter($state),
                'actor_uid' => $query->createNamedParameter($actorUid),
                'created_at' => $query->createNamedParameter($timestamp),
            ]);
            $query->executeStatement();
            $this->db->commit();
            if (isset($this->excludedByBinding[$bindingId])) {
                if ($state === self::STATE_WITHDRAWN) {
                    $this->excludedByBinding[$bindingId][$fileId] = true;
                } else {
                    unset($this->excludedByBinding[$bindingId][$fileId]);
                }
            }
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    private static function assertIdentity(string $bindingId, int $fileId): void {
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $bindingId) || $fileId < 1) {
            throw new \InvalidArgumentException('Invalid publication source');
        }
    }
}
