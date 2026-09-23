<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IDBConnection;

/** One machine key belongs to exactly one publication binding. */
final class MachineKeyRegistryService {
    public function __construct(
        private IDBConnection $db,
        private BindingRegistryService $bindings,
    ) {
    }

    /** @return array{binding_id: string, token_sha256: string, source_hash: string}|null */
    public function find(string $keyId): ?array {
        if (!self::validKeyId($keyId)) {
            return null;
        }
        $query = $this->db->getQueryBuilder();
        $query->select('binding_id', 'token_sha256', 'source_hash', 'expires_at')->from('weknora_machine_key')
            ->where($query->expr()->eq('key_id', $query->createNamedParameter($keyId)));
        $result = $query->executeQuery();
        try {
            $row = $result->fetchAssociative();
        } finally {
            $result->closeCursor();
        }
        if ($row === false || ((int)$row['expires_at'] > 0 && (int)$row['expires_at'] <= time()) ||
            !self::validHash($row['token_sha256'] ?? null) ||
            !self::validHash($row['source_hash'] ?? null)) {
            return null;
        }
        return [
            'binding_id' => (string)$row['binding_id'],
            'token_sha256' => strtolower((string)$row['token_sha256']),
            'source_hash' => strtolower((string)$row['source_hash']),
        ];
    }

    /** @return list<array{key_id: string, binding_id: string, created_at: int, created_by_uid: string}> */
    public function listForBinding(string $bindingId): array {
        $this->requireConfiguredBinding($bindingId);
        $query = $this->db->getQueryBuilder();
        $query->select('key_id', 'binding_id', 'created_at', 'created_by_uid')
            ->from('weknora_machine_key')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->orderBy('created_at', 'ASC')->addOrderBy('key_id', 'ASC');
        $result = $query->executeQuery();
        try {
            $keys = [];
            while (($row = $result->fetchAssociative()) !== false) {
                $keys[] = [
                    'key_id' => (string)$row['key_id'],
                    'binding_id' => (string)$row['binding_id'],
                    'created_at' => (int)$row['created_at'],
                    'created_by_uid' => (string)$row['created_by_uid'],
                ];
            }
            return $keys;
        } finally {
            $result->closeCursor();
        }
    }

    /** Return a plaintext token exactly once to its administrator issuer. */
    public function issue(string $bindingId, string $keyId, string $issuerUid): string {
        if (!self::validKeyId($keyId) || $issuerUid === '' || strlen($issuerUid) > 64) {
            throw new \InvalidArgumentException('Invalid machine key');
        }
        $this->db->beginTransaction();
        try {
            // Serialize issuance with binding removal. A key issued before
            // deletion is deleted with the binding; issuance after deletion
            // sees no configured binding and fails.
            $this->db->insertIgnoreConflict('weknora_bind_lock', ['id' => 1]);
            $lock = $this->db->getQueryBuilder();
            $lock->select('id')->from('weknora_bind_lock')
                ->where($lock->expr()->eq('id', $lock->createNamedParameter(1)))
                ->forUpdate();
            $result = $lock->executeQuery();
            try {
                if ($result->fetchOne() === false) {
                    throw new \UnexpectedValueException('Binding registry lock unavailable');
                }
            } finally {
                $result->closeCursor();
            }
            $binding = $this->requireConfiguredBinding($bindingId);
            $this->bindings->requireActiveRoot($bindingId);
            $token = rtrim(strtr(base64_encode(random_bytes(48)), '+/', '-_'), '=');
            $inserted = $this->db->insertIgnoreConflict('weknora_machine_key', [
                'key_id' => $keyId,
                'binding_id' => $bindingId,
                'token_sha256' => hash('sha256', $token),
                'source_hash' => self::sourceHash($binding),
                'created_at' => time(),
                'created_by_uid' => $issuerUid,
            ]);
            if ($inserted !== 1) {
                throw new \DomainException('Machine key ID already exists');
            }
            $this->db->commit();
            return $token;
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    public function revoke(string $bindingId, string $keyId): bool {
        if (!self::validKeyId($keyId)) {
            throw new \InvalidArgumentException('Invalid machine key ID');
        }
        $this->db->beginTransaction();
        try {
            // Source commit, pairing abort and binding removal share this lock.
            // An active/pending pairing key must follow its pairing lifecycle.
            $this->db->insertIgnoreConflict('weknora_bind_lock', ['id' => 1]);
            $lock = $this->db->getQueryBuilder();
            $result = $lock->select('id')->from('weknora_bind_lock')
                ->where($lock->expr()->eq('id', $lock->createNamedParameter(1)))
                ->forUpdate()->executeQuery();
            try {
                if ($result->fetchOne() === false) {
                    throw new \UnexpectedValueException('Binding registry lock unavailable');
                }
            } finally {
                $result->closeCursor();
            }
            $this->requireConfiguredBinding($bindingId);
            $pairQuery = $this->db->getQueryBuilder();
            $pairResult = $pairQuery->select('operation_id')->from('weknora_src_pair')
                ->where($pairQuery->expr()->eq('binding_id', $pairQuery->createNamedParameter($bindingId)))
                ->andWhere($pairQuery->expr()->eq('key_id', $pairQuery->createNamedParameter($keyId)))
                ->andWhere($pairQuery->expr()->orX(
                    $pairQuery->expr()->eq('state', $pairQuery->createNamedParameter('pending')),
                    $pairQuery->expr()->eq('state', $pairQuery->createNamedParameter('active')),
                ))->executeQuery();
            try {
                if ($pairResult->fetchOne() !== false) {
                    throw new \DomainException('Pairing machine key cannot be revoked directly');
                }
            } finally {
                $pairResult->closeCursor();
            }
            $rotationQuery = $this->db->getQueryBuilder();
            $rotationResult = $rotationQuery->select('operation_id')->from('weknora_src_pair_rot')
                ->where($rotationQuery->expr()->eq('binding_id', $rotationQuery->createNamedParameter($bindingId)))
                ->andWhere($rotationQuery->expr()->orX(
                    $rotationQuery->expr()->eq('old_key_id', $rotationQuery->createNamedParameter($keyId)),
                    $rotationQuery->expr()->eq('new_key_id', $rotationQuery->createNamedParameter($keyId)),
                ))
                ->andWhere($rotationQuery->expr()->orX(
                    $rotationQuery->expr()->eq('state', $rotationQuery->createNamedParameter('pending')),
                    $rotationQuery->expr()->eq('state', $rotationQuery->createNamedParameter('committed')),
                ))->executeQuery();
            try {
                if ($rotationResult->fetchOne() !== false) {
                    throw new \DomainException('Rotating source key cannot be revoked directly');
                }
            } finally {
                $rotationResult->closeCursor();
            }
            $query = $this->db->getQueryBuilder();
            $revoked = $query->delete('weknora_machine_key')
                ->where($query->expr()->eq('key_id', $query->createNamedParameter($keyId)))
                ->andWhere($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
                ->executeStatement() === 1;
            $this->db->commit();
            return $revoked;
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    public function bindingExists(string $bindingId): bool {
        return $this->configuredBinding($bindingId) !== null;
    }

    /** @param array{binding_id: string, source_hash: string} $key */
    public function matchesCurrentBinding(array $key): bool {
        $binding = $this->configuredBinding($key['binding_id']);
        return $binding !== null && hash_equals(self::sourceHash($binding), $key['source_hash']);
    }

    /** @return array{id: string, name: string, owner_uid: string, root_file_id: int}|null */
    private function configuredBinding(string $bindingId): ?array {
        foreach ($this->bindings->listBindings() as $binding) {
            if ($binding['id'] === $bindingId) {
                return $binding;
            }
        }
        return null;
    }

    /** @return array{id: string, name: string, owner_uid: string, root_file_id: int} */
    private function requireConfiguredBinding(string $bindingId): array {
        $binding = $this->configuredBinding($bindingId);
        if ($binding === null) {
            throw new \InvalidArgumentException('Unknown binding');
        }
        return $binding;
    }

    /** @param array{id: string, owner_uid: string, root_file_id: int} $binding */
    public static function sourceHash(array $binding): string {
        return hash('sha256', $binding['id'] . "\0" . $binding['owner_uid'] . "\0" .
            (string)$binding['root_file_id']);
    }

    public static function validKeyId(mixed $keyId): bool {
        return is_string($keyId) && (bool)preg_match('/\A[A-Za-z0-9._-]{1,64}\z/D', $keyId);
    }

    public static function validHash(mixed $hash): bool {
        return is_string($hash) && (bool)preg_match('/\A[a-fA-F0-9]{64}\z/D', $hash);
    }
}
