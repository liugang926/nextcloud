<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IConfig;
use OCP\IDBConnection;
use OCP\Security\ICrypto;

/** Administrator-managed sender configuration; secrets never leave this service on reads. */
final class EventConnectionService {
    public function __construct(
        private IDBConnection $db,
        private IConfig $config,
        private ICrypto $crypto,
        private BindingRegistryService $bindings,
        private EventReceiverPolicy $policy,
    ) {
    }

    /** @return array<string, int|string> */
    public function status(string $bindingId): array {
        $row = $this->row($bindingId);
        if ($row === null) {
            throw new \OutOfBoundsException('Event connection is not configured');
        }
        return self::publicStatus($row);
    }

    /**
     * Store a one-time WeKnora credential. Reprovisioning the same connection
     * ID with a new key is an immediate rotation, preserving its receipt ID.
     * A different receiver connection needs explicit revocation and pairing.
     *
     * @return array{created: bool, status: array<string, int|string>}
     */
    public function configure(
        string $bindingId,
        string $claimedBindingId,
        string $claimedInstanceId,
        string $connectionId,
        string $keyId,
        string $secret,
        string $receiverUrl,
    ): array {
        self::assertBindingId($bindingId);
        $localInstanceId = $this->config->getSystemValueString('instanceid');
        if ($claimedBindingId !== $bindingId ||
            !preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $localInstanceId) ||
            $claimedInstanceId !== $localInstanceId ||
            !preg_match('/\A[A-Za-z0-9_-]{16,128}\z/D', $connectionId) ||
            !preg_match('/\A[A-Za-z0-9._-]{1,64}\z/D', $keyId) ||
            !preg_match('/\A[A-Za-z0-9_-]{32,256}\z/D', $secret)) {
            throw new \InvalidArgumentException('Invalid event connection identity or credential');
        }
        $this->policy->requireApproved($receiverUrl);
        $ciphertext = $this->crypto->encrypt($secret);
        if ($ciphertext === '' || hash_equals($ciphertext, $secret)) {
            throw new \UnexpectedValueException('Event credential encryption failed');
        }

        $this->db->beginTransaction();
        try {
            // Binding deletion holds this row lock before retiring its ID and
            // deleting the sender. A concurrent provision cannot revive it.
            $this->db->insertIgnoreConflict('weknora_bind_lock', ['id' => 1]);
            $lock = $this->db->getQueryBuilder();
            $lock->select('id')->from('weknora_bind_lock')
                ->where($lock->expr()->eq('id', $lock->createNamedParameter(1)))
                ->forUpdate();
            $locked = $lock->executeQuery();
            try {
                if ($locked->fetchOne() === false) {
                    throw new \UnexpectedValueException('Binding lock unavailable');
                }
            } finally {
                $locked->closeCursor();
            }
            $this->bindings->requireActiveRoot($bindingId);
            $existing = $this->row($bindingId, true);
            $now = time();
            if ($existing === null) {
                $duplicate = $this->db->getQueryBuilder();
                $duplicate->select('binding_id')->from('weknora_event_conn')
                    ->where($duplicate->expr()->eq('connection_id',
                        $duplicate->createNamedParameter($connectionId)));
                $duplicateResult = $duplicate->executeQuery();
                try {
                    if ($duplicateResult->fetchOne() !== false) {
                        throw new \DomainException('Connection ID already belongs to a binding');
                    }
                } finally {
                    $duplicateResult->closeCursor();
                }
                // Serialize first pairing with retention. Otherwise pruning
                // could create a floor after this check but before the new
                // sender row becomes visible to the pruning transaction.
                $this->db->insertIgnoreConflict('weknora_outbox_lock', ['id' => 1]);
                $outboxLock = $this->db->getQueryBuilder();
                $outboxLock->select('id')->from('weknora_outbox_lock')
                    ->where($outboxLock->expr()->eq('id', $outboxLock->createNamedParameter(1)))
                    ->forUpdate();
                $outboxLocked = $outboxLock->executeQuery();
                try {
                    if ($outboxLocked->fetchOne() === false) {
                        throw new \UnexpectedValueException('Outbox lock unavailable');
                    }
                } finally {
                    $outboxLocked->closeCursor();
                }
                $floor = $this->changeFloor($bindingId);
                if ($floor > 0) {
                    throw new \DomainException('Change history was already pruned');
                }
                $insert = $this->db->getQueryBuilder();
                $insert->insert('weknora_event_conn')->values([
                    'binding_id' => $insert->createNamedParameter($bindingId),
                    'connection_id' => $insert->createNamedParameter($connectionId),
                    'key_id' => $insert->createNamedParameter($keyId),
                    'secret_ciphertext' => $insert->createNamedParameter($ciphertext),
                    'receiver_url' => $insert->createNamedParameter($receiverUrl),
                    'received_id' => $insert->createNamedParameter(0),
                    'applied_id' => $insert->createNamedParameter(0),
                    'applied_checked_at' => $insert->createNamedParameter(0),
                    'applied_error_code' => $insert->createNamedParameter('status_unverified'),
                    'status' => $insert->createNamedParameter('active'),
                    'attempt_count' => $insert->createNamedParameter(0),
                    'next_attempt_at' => $insert->createNamedParameter(0),
                    'last_error_code' => $insert->createNamedParameter(''),
                    'created_at' => $insert->createNamedParameter($now),
                    'updated_at' => $insert->createNamedParameter($now),
                ]);
                $insert->executeStatement();
                $created = true;
            } else {
                if ($existing['connection_id'] !== $connectionId ||
                    $existing['receiver_url'] !== $receiverUrl ||
                    $existing['key_id'] === $keyId) {
                    throw new \DomainException('Connection replacement requires explicit revocation');
                }
                $update = $this->db->getQueryBuilder();
                $update->update('weknora_event_conn')
                    ->set('key_id', $update->createNamedParameter($keyId))
                    ->set('secret_ciphertext', $update->createNamedParameter($ciphertext))
                    ->set('status', $update->createNamedParameter('active'))
                    ->set('attempt_count', $update->createNamedParameter(0))
                    ->set('next_attempt_at', $update->createNamedParameter(0))
                    ->set('last_error_code', $update->createNamedParameter(''))
                    ->set('applied_checked_at', $update->createNamedParameter(0))
                    ->set('applied_error_code', $update->createNamedParameter('status_unverified'))
                    ->set('updated_at', $update->createNamedParameter($now))
                    ->where($update->expr()->eq('binding_id', $update->createNamedParameter($bindingId)))
                    ->executeStatement();
                $created = false;
            }
            $row = $this->row($bindingId);
            if ($row === null) {
                throw new \UnexpectedValueException('Event connection write failed');
            }
            $this->db->commit();
            return ['created' => $created, 'status' => self::publicStatus($row)];
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    public function revoke(string $bindingId): bool {
        self::assertBindingId($bindingId);
        $this->db->beginTransaction();
        try {
            $this->db->insertIgnoreConflict('weknora_bind_lock', ['id' => 1]);
            $lock = $this->db->getQueryBuilder();
            $lock->select('id')->from('weknora_bind_lock')
                ->where($lock->expr()->eq('id', $lock->createNamedParameter(1)))
                ->forUpdate();
            $result = $lock->executeQuery();
            try {
                if ($result->fetchOne() === false) {
                    throw new \UnexpectedValueException('Binding lock unavailable');
                }
            } finally {
                $result->closeCursor();
            }
            $delete = $this->db->getQueryBuilder();
            $deleted = $delete->delete('weknora_event_conn')
                ->where($delete->expr()->eq('binding_id', $delete->createNamedParameter($bindingId)))
                ->executeStatement() === 1;
            $this->db->commit();
            return $deleted;
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /** @return array<string, mixed>|null */
    public function row(string $bindingId, bool $forUpdate = false): ?array {
        self::assertBindingId($bindingId);
        $query = $this->db->getQueryBuilder();
        $query->select('*')->from('weknora_event_conn')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)));
        if ($forUpdate) {
            $query->forUpdate();
        }
        $result = $query->executeQuery();
        try {
            $row = $result->fetchAssociative();
            return $row === false ? null : $row;
        } finally {
            $result->closeCursor();
        }
    }

    private function changeFloor(string $bindingId): int {
        $query = $this->db->getQueryBuilder();
        $query->select('floor_id')->from('weknora_change_floor')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)));
        $result = $query->executeQuery();
        try {
            $floor = $result->fetchOne();
            return $floor === false ? 0 : (int)$floor;
        } finally {
            $result->closeCursor();
        }
    }

    /** @param array<string, mixed> $row
     *  @return array<string, int|string>
     */
    public static function publicStatus(array $row): array {
        return [
            'binding_id' => (string)$row['binding_id'],
            'connection_id' => (string)$row['connection_id'],
            'key_id' => (string)$row['key_id'],
            'receiver_url' => (string)$row['receiver_url'],
            'status' => (string)$row['status'],
            'received_through_event_id' => (string)$row['received_id'],
            'applied_through_event_id' => (string)$row['applied_id'],
            'applied_checked_at' => (int)$row['applied_checked_at'],
            'applied_error_code' => (string)$row['applied_error_code'],
            'attempt_count' => (int)$row['attempt_count'],
            'next_attempt_at' => (int)$row['next_attempt_at'],
            'last_error_code' => (string)$row['last_error_code'],
        ];
    }

    private static function assertBindingId(string $bindingId): void {
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $bindingId)) {
            throw new \InvalidArgumentException('Invalid binding ID');
        }
    }
}
