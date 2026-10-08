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
        return $this->getDecision($bindingId, $fileId)['state'];
    }

    /**
     * The audit ID is a local administrative decision revision, not a remote
     * publication or applied-consumer checkpoint. Zero means no linked audit
     * row is known, including decisions that predate migration 21.
     *
     * @return array{state: string, decision_audit_id: int}
     */
    public function getDecision(string $bindingId, int $fileId): array {
        self::assertIdentity($bindingId, $fileId);
        $query = $this->db->getQueryBuilder();
        $query->select('state', 'actor_uid', 'updated_at', 'decision_audit_id')
            ->from('weknora_pub_state')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->andWhere($query->expr()->eq('file_id', $query->createNamedParameter($fileId)));
        $result = $query->executeQuery();
        try {
            $row = $result->fetchAssociative();
        } finally {
            $result->closeCursor();
        }
        if ($row === false) {
            return ['state' => self::STATE_ELIGIBLE, 'decision_audit_id' => 0];
        }
        $state = (string)$row['state'];
        if ($state !== self::STATE_ELIGIBLE && $state !== self::STATE_WITHDRAWN) {
            // Unknown stored states must never expose source content.
            throw new \UnexpectedValueException('Invalid publication state');
        }
        $auditId = (int)$row['decision_audit_id'];
        if ($auditId < 0) {
            throw new \UnexpectedValueException('Invalid decision audit ID');
        }
        if ($auditId > 0) {
            $audit = $this->db->getQueryBuilder();
            $audit->select('binding_id', 'file_id', 'action', 'actor_uid', 'created_at')
                ->from('weknora_pub_audit')
                ->where($audit->expr()->eq('id', $audit->createNamedParameter($auditId)));
            $auditResult = $audit->executeQuery();
            try {
                $linked = $auditResult->fetchAssociative();
            } finally {
                $auditResult->closeCursor();
            }
            if ($linked === false || $linked['binding_id'] !== $bindingId ||
                (int)$linked['file_id'] !== $fileId || $linked['action'] !== $state ||
                $linked['actor_uid'] !== $row['actor_uid'] ||
                (int)$linked['created_at'] !== (int)$row['updated_at']) {
                throw new \UnexpectedValueException('Current publication decision audit mismatch');
            }
        }
        return ['state' => $state, 'decision_audit_id' => $auditId];
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
            // Fresh app installs do not reliably run postSchemaChange seed
            // hooks. Seed in the writer transaction before locking the row.
            $this->db->insertIgnoreConflict('weknora_bind_lock', ['id' => 1]);
            // Audit IDs are used as manifest publication revisions. Serialize
            // writers before allocating an audit ID so a later ID can never
            // commit before an earlier withdrawal on another file.
            $lock = $this->db->getQueryBuilder();
            $lock->select('id')->from('weknora_bind_lock')
                ->where($lock->expr()->eq('id', $lock->createNamedParameter(1)))
                ->forUpdate();
            $lockResult = $lock->executeQuery();
            try {
                if ($lockResult->fetchOne() === false) {
                    throw new \UnexpectedValueException('Publication ordering lock is missing');
                }
            } finally {
                $lockResult->closeCursor();
            }

            // PostgreSQL aborts a transaction after a uniqueness error, so
            // insert-if-absent before the update instead of setValues().
            $this->db->insertIgnoreConflict('weknora_pub_state', [
                'binding_id' => $bindingId,
                'file_id' => $fileId,
                'state' => $state,
                'actor_uid' => $actorUid,
                'updated_at' => $timestamp,
            ]);
            $query = $this->db->getQueryBuilder();
            $query->insert('weknora_pub_audit')->values([
                'binding_id' => $query->createNamedParameter($bindingId),
                'file_id' => $query->createNamedParameter($fileId),
                'action' => $query->createNamedParameter($state),
                'actor_uid' => $query->createNamedParameter($actorUid),
                'created_at' => $query->createNamedParameter($timestamp),
            ]);
            $query->executeStatement();
            $auditId = (int)$this->db->lastInsertId('weknora_pub_audit');
            if ($auditId < 1) {
                throw new \UnexpectedValueException('Publication audit ID was not allocated');
            }

            $update = $this->db->getQueryBuilder();
            $changed = $update->update('weknora_pub_state')
                ->set('state', $update->createNamedParameter($state))
                ->set('actor_uid', $update->createNamedParameter($actorUid))
                ->set('updated_at', $update->createNamedParameter($timestamp))
                ->set('decision_audit_id', $update->createNamedParameter($auditId))
                ->where($update->expr()->eq('binding_id', $update->createNamedParameter($bindingId)))
                ->andWhere($update->expr()->eq('file_id', $update->createNamedParameter($fileId)))
                ->executeStatement();
            if ($changed !== 1) {
                throw new \UnexpectedValueException('Publication state row changed unexpectedly');
            }
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
