<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\Files\Folder;
use OCP\IConfig;
use OCP\IDBConnection;

/** Durable, binding-scoped intent for one dedicated WeKnora knowledge base. */
final class SourcePairingRegistryService {
    private const ROTATION_GRACE_SECONDS = 86400;
    public function __construct(
        private IDBConnection $db,
        private IConfig $config,
        private BindingRegistryService $bindings,
    ) {
    }

    public static function validOperationId(mixed $value): bool {
        return is_string($value) && (bool)preg_match(
            '/\A[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}\z/D',
            $value,
        );
    }

    public static function validRemoteId(mixed $value): bool {
        return is_string($value) && (bool)preg_match('/\A[A-Za-z0-9._-]{1,128}\z/D', $value);
    }

    public static function rootHash(Folder $root): string {
        return hash('sha256', $root->getStorage()->getId() . "\0" .
            $root->getInternalPath() . "\0" . $root->getPath());
    }

    /** @return array{pairing: array, token?: string, created: bool} */
    public function prepare(string $bindingId, string $operationId, string $tenantId,
        string $knowledgeBaseId, string $actorUid): array {
        $this->validateInput($bindingId, $operationId, $tenantId, $knowledgeBaseId);
        if ($actorUid === '' || strlen($actorUid) > 64) {
            throw new \InvalidArgumentException('Invalid administrator');
        }
        $operationId = strtolower($operationId);
        $this->db->beginTransaction();
        try {
            $this->lockRegistry();
            [$binding, $root] = $this->requireActiveBinding($bindingId);
            $sourceHash = MachineKeyRegistryService::sourceHash($binding);
            $rootHash = self::rootHash($root);
            $instanceId = $this->instanceId();
            $existingOperation = $this->byOperation($operationId);
            if ($existingOperation !== null) {
                if ($existingOperation['binding_id'] !== $bindingId ||
                    (string)$existingOperation['tenant_id'] !== $tenantId ||
                    $existingOperation['knowledge_base_id'] !== $knowledgeBaseId ||
                    $existingOperation['source_hash'] !== $sourceHash ||
                    $existingOperation['root_hash'] !== $rootHash ||
                    ($existingOperation['state'] === 'pending' &&
                        (int)$existingOperation['publication_epoch'] !== $binding['publication_epoch']) ||
                    $existingOperation['instance_id'] !== $instanceId ||
                    $existingOperation['state'] === 'aborted' ||
                    $existingOperation['state'] === 'retired') {
                    throw new \DomainException('Pairing operation conflicts with its original intent');
                }
                $this->db->commit();
                return ['pairing' => $this->publicRow($existingOperation), 'created' => false];
            }
            if ($this->liveForBinding($bindingId) !== null ||
                $this->liveForTarget($tenantId, $knowledgeBaseId) !== null) {
                throw new \DomainException('Binding or knowledge base is already paired');
            }
            $keyId = 'pair_' . str_replace('-', '', $operationId);
            $token = rtrim(strtr(base64_encode(random_bytes(48)), '+/', '-_'), '=');
            $now = time();
            $keyInserted = $this->db->insertIgnoreConflict('weknora_machine_key', [
                'key_id' => $keyId,
                'binding_id' => $bindingId,
                'token_sha256' => hash('sha256', $token),
                'source_hash' => $sourceHash,
                'created_at' => $now,
                'created_by_uid' => $actorUid,
            ]);
            if ($keyInserted !== 1) {
                throw new \DomainException('Pairing key ID has already been used');
            }
            $row = [
                'operation_id' => $operationId,
                'binding_id' => $bindingId,
                'source_hash' => $sourceHash,
                'root_hash' => $rootHash,
                'publication_epoch' => $binding['publication_epoch'],
                'instance_id' => $instanceId,
                'tenant_id' => $tenantId,
                'knowledge_base_id' => $knowledgeBaseId,
                'data_source_id' => '',
                'key_id' => $keyId,
                'state' => 'pending',
                'created_by_uid' => $actorUid,
                'created_at' => $now,
                'updated_at' => $now,
            ];
            if ($this->db->insertIgnoreConflict('weknora_src_pair', $row) !== 1) {
                throw new \DomainException('Pairing operation has already been used');
            }
            $this->db->commit();
            return ['pairing' => $this->publicRow($row), 'token' => $token, 'created' => true];
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /** @return array{pairing: array, changed: bool} */
    public function commit(string $bindingId, string $keyId, string $operationId,
        string $instanceId, string $tenantId, string $knowledgeBaseId, string $dataSourceId): array {
        $this->validateInput($bindingId, $operationId, $tenantId, $knowledgeBaseId);
        if (!self::validRemoteId($dataSourceId) || $instanceId === '' || strlen($instanceId) > 64) {
            throw new \InvalidArgumentException('Invalid pairing source identity');
        }
        $operationId = strtolower($operationId);
        $this->db->beginTransaction();
        try {
            $this->lockRegistry();
            $row = $this->byOperation($operationId);
            if ($row === null || $row['binding_id'] !== $bindingId ||
                !hash_equals($row['key_id'], $keyId)) {
                throw new \DomainException('Pairing operation or key does not match');
            }
            $keyQuery = $this->db->getQueryBuilder();
            $keyResult = $keyQuery->select('source_hash')->from('weknora_machine_key')
                ->where($keyQuery->expr()->eq('key_id', $keyQuery->createNamedParameter($keyId)))
                ->andWhere($keyQuery->expr()->eq('binding_id', $keyQuery->createNamedParameter($bindingId)))
                ->executeQuery();
            try {
                $key = $keyResult->fetchAssociative();
            } finally {
                $keyResult->closeCursor();
            }
            if ($key === false || !hash_equals($row['source_hash'], (string)$key['source_hash'])) {
                throw new \DomainException('Pairing machine key was revoked or changed');
            }
            [$binding, $root] = $this->requireActiveBinding($bindingId);
            if ($row['state'] !== 'pending' && $row['state'] !== 'active') {
                throw new \DomainException('Pairing operation is closed');
            }
            if ($row['instance_id'] !== $instanceId || $instanceId !== $this->instanceId() ||
                (string)$row['tenant_id'] !== $tenantId ||
                $row['knowledge_base_id'] !== $knowledgeBaseId ||
                $row['source_hash'] !== MachineKeyRegistryService::sourceHash($binding) ||
                $row['root_hash'] !== self::rootHash($root)) {
                throw new \DomainException('Pairing source or target changed');
            }
            if ($row['state'] === 'active') {
                if ($row['data_source_id'] !== $dataSourceId) {
                    throw new \DomainException('Pairing data source changed');
                }
                $this->db->commit();
                return ['pairing' => $this->publicRow($row), 'changed' => false];
            }
            // A stop/resume during preparation invalidates the uncommitted
            // intent. A pair already committed before the stop remains valid.
            if ((int)$row['publication_epoch'] !== $binding['publication_epoch']) {
                throw new \DomainException('Publication changed during pairing preparation');
            }
            if ($this->liveForDataSource($dataSourceId) !== null) {
                throw new \DomainException('Data source is already paired');
            }
            $query = $this->db->getQueryBuilder();
            $updated = $query->update('weknora_src_pair')
                ->set('data_source_id', $query->createNamedParameter($dataSourceId))
                ->set('state', $query->createNamedParameter('active'))
                ->set('updated_at', $query->createNamedParameter(time()))
                ->where($query->expr()->eq('operation_id', $query->createNamedParameter($operationId)))
                ->andWhere($query->expr()->eq('state', $query->createNamedParameter('pending')))
                ->executeStatement();
            if ($updated !== 1) {
                throw new \DomainException('Pairing operation changed concurrently');
            }
            $row['data_source_id'] = $dataSourceId;
            $row['state'] = 'active';
            $this->db->commit();
            return ['pairing' => $this->publicRow($row), 'changed' => true];
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /** @return array{pairing: array, revoked_key: bool} */
    public function abort(string $bindingId, string $operationId): array {
        if (!self::validOperationId($operationId)) {
            throw new \InvalidArgumentException('Invalid pairing operation');
        }
        $operationId = strtolower($operationId);
        $this->db->beginTransaction();
        try {
            $this->lockRegistry();
            $row = $this->byOperation($operationId);
            if ($row === null || $row['binding_id'] !== $bindingId) {
                throw new \OutOfBoundsException('Pairing operation was not found');
            }
            if ($row['state'] === 'active') {
                throw new \DomainException('Active pairing cannot be aborted');
            }
            if ($row['state'] === 'aborted' || $row['state'] === 'retired') {
                $this->db->commit();
                return ['pairing' => $this->publicRow($row), 'revoked_key' => false];
            }
            $revoked = $this->abortPendingRow($row);
            $this->db->commit();
            return ['pairing' => $this->publicRow($row), 'revoked_key' => $revoked === 1];
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /** Signed by the pair key. A lost ACK may retry with its abort-only tombstone. */
    public function abortMachine(string $bindingId, string $keyId, string $operationId,
        string $instanceId, string $tenantId, string $knowledgeBaseId): array {
        $this->validateInput($bindingId, $operationId, $tenantId, $knowledgeBaseId);
        if ($instanceId === '' || strlen($instanceId) > 64) {
            throw new \InvalidArgumentException('Invalid pairing instance');
        }
        $operationId = strtolower($operationId);
        $this->db->beginTransaction();
        try {
            $this->lockRegistry();
            $row = $this->byOperation($operationId);
            if ($row === null || $row['binding_id'] !== $bindingId ||
                !hash_equals($row['key_id'], $keyId) ||
                $row['instance_id'] !== $instanceId || $instanceId !== $this->instanceId() ||
                (string)$row['tenant_id'] !== $tenantId ||
                $row['knowledge_base_id'] !== $knowledgeBaseId ||
                !in_array($row['state'], ['pending', 'aborted'], true)) {
                throw new \DomainException('Pairing cannot be aborted');
            }
            if ($row['state'] === 'aborted') {
                $this->db->commit();
                return ['pairing' => $this->publicRow($row), 'revoked_key' => false];
            }
            $revoked = $this->abortPendingRow($row);
            $this->db->commit();
            return ['pairing' => $this->publicRow($row), 'revoked_key' => $revoked === 1];
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /** @param array<string, mixed> $row Mutated to its aborted state. */
    private function abortPendingRow(array &$row): int {
        $now = time();
        // Keep the verifier hash solely for an identical abort retry. Normal
        // service requests reject the expired key immediately after commit.
        $query = $this->db->getQueryBuilder();
        $revoked = $query->update('weknora_machine_key')
            ->set('expires_at', $query->createNamedParameter($now))
            ->where($query->expr()->eq('binding_id',
                $query->createNamedParameter((string)$row['binding_id'])))
            ->andWhere($query->expr()->eq('key_id',
                $query->createNamedParameter((string)$row['key_id'])))
            ->andWhere($query->expr()->eq('expires_at', $query->createNamedParameter(0)))
            ->executeStatement();
        if ($revoked !== 1) {
            throw new \DomainException('Pending pairing key is unavailable');
        }
        $query = $this->db->getQueryBuilder();
        if ($query->update('weknora_src_pair')
            ->set('state', $query->createNamedParameter('aborted'))
            ->set('updated_at', $query->createNamedParameter($now))
            ->where($query->expr()->eq('operation_id',
                $query->createNamedParameter((string)$row['operation_id'])))
            ->andWhere($query->expr()->eq('state', $query->createNamedParameter('pending')))
            ->executeStatement() !== 1) {
            throw new \DomainException('Pairing state changed concurrently');
        }
        $row['state'] = 'aborted';
        return $revoked;
    }

    public function status(string $bindingId): ?array {
        $row = $this->latestForBinding($bindingId);
        return $row === null ? null : $this->publicRow($row);
    }

    /** Prepare an overlapping key. The token is returned only on first creation. */
    public function prepareRotation(string $bindingId, string $operationId, string $actorUid): array {
        if (!self::validOperationId($operationId) || $actorUid === '' || strlen($actorUid) > 64) {
            throw new \InvalidArgumentException('Invalid rotation intent');
        }
        $operationId = strtolower($operationId);
        $this->db->beginTransaction();
        try {
            $this->lockRegistry();
            $pair = $this->requireCurrentActivePair($bindingId);
            [$binding] = $this->requireActiveBinding($bindingId);
            $existing = $this->rotationByOperation($operationId);
            if ($existing !== null) {
                if ($existing['pair_operation_id'] !== $pair['operation_id'] ||
                    $existing['binding_id'] !== $bindingId ||
                    $existing['state'] === 'aborted') {
                    throw new \DomainException('Rotation operation conflicts with its original intent');
                }
                $this->db->commit();
                return ['rotation' => $this->publicRotation($existing), 'created' => false];
            }
            if ($this->liveRotation($pair['operation_id']) !== null) {
                throw new \DomainException('A source-key rotation is already in progress');
            }
            if ((int)$pair['publication_epoch'] > $binding['publication_epoch']) {
                throw new \DomainException('Pairing publication epoch is invalid');
            }
            $newKeyId = 'rot_' . str_replace('-', '', $operationId);
            $token = rtrim(strtr(base64_encode(random_bytes(48)), '+/', '-_'), '=');
            $now = time();
            if ($this->db->insertIgnoreConflict('weknora_machine_key', [
                'key_id' => $newKeyId,
                'binding_id' => $bindingId,
                'token_sha256' => hash('sha256', $token),
                'source_hash' => $pair['source_hash'],
                'created_at' => $now,
                'created_by_uid' => $actorUid,
                'expires_at' => 0,
            ]) !== 1) {
                throw new \DomainException('Rotation key ID was already used');
            }
            $row = [
                'operation_id' => $operationId,
                'pair_operation_id' => $pair['operation_id'],
                'binding_id' => $bindingId,
                'instance_id' => $pair['instance_id'],
                'tenant_id' => $pair['tenant_id'],
                'knowledge_base_id' => $pair['knowledge_base_id'],
                'data_source_id' => $pair['data_source_id'],
                'old_key_id' => $pair['key_id'],
                'new_key_id' => $newKeyId,
                'publication_epoch' => $binding['publication_epoch'],
                'state' => 'pending',
                'old_key_expires_at' => 0,
                'created_at' => $now,
                'updated_at' => $now,
            ];
            if ($this->db->insertIgnoreConflict('weknora_src_pair_rot', $row) !== 1) {
                throw new \DomainException('Rotation operation was already used');
            }
            $this->db->commit();
            return ['rotation' => $this->publicRotation($row), 'token' => $token, 'created' => true];
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    public function rotationStatus(string $bindingId): ?array {
        $query = $this->db->getQueryBuilder();
        $result = $query->select('*')->from('weknora_src_pair_rot')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->orderBy('id', 'DESC')->setMaxResults(1)->executeQuery();
        try {
            $row = $result->fetchAssociative();
            return $row === false ? null : $this->publicRotation($row);
        } finally {
            $result->closeCursor();
        }
    }

    /** Signed by the new key. Keep the old key valid until WeKnora switches. */
    public function commitRotation(string $bindingId, string $keyId, string $operationId,
        string $pairOperationId, string $instanceId, string $tenantId,
        string $knowledgeBaseId, string $dataSourceId): array {
        return $this->advanceRotation($bindingId, $keyId, $operationId, $pairOperationId,
            $instanceId, $tenantId, $knowledgeBaseId, $dataSourceId, false);
    }

    /** Called after WeKnora durably switches its source config to the new key. */
    public function finalizeRotation(string $bindingId, string $keyId, string $operationId,
        string $pairOperationId, string $instanceId, string $tenantId,
        string $knowledgeBaseId, string $dataSourceId): array {
        return $this->advanceRotation($bindingId, $keyId, $operationId, $pairOperationId,
            $instanceId, $tenantId, $knowledgeBaseId, $dataSourceId, true);
    }

    private function advanceRotation(string $bindingId, string $keyId, string $operationId,
        string $pairOperationId, string $instanceId, string $tenantId,
        string $knowledgeBaseId, string $dataSourceId, bool $finalize): array {
        $this->validateInput($bindingId, $pairOperationId, $tenantId, $knowledgeBaseId);
        if (!self::validOperationId($operationId) || !self::validRemoteId($dataSourceId) ||
            $instanceId === '' || strlen($instanceId) > 64) {
            throw new \InvalidArgumentException('Invalid rotation source identity');
        }
        $operationId = strtolower($operationId);
        $pairOperationId = strtolower($pairOperationId);
        $this->db->beginTransaction();
        try {
            $this->lockRegistry();
            $rotation = $this->rotationByOperation($operationId);
            $pair = $this->byOperation($pairOperationId);
            if ($rotation === null || $pair === null ||
                $rotation['binding_id'] !== $bindingId ||
                $rotation['pair_operation_id'] !== $pairOperationId ||
                !hash_equals($rotation['new_key_id'], $keyId) ||
                $rotation['instance_id'] !== $instanceId ||
                (string)$rotation['tenant_id'] !== $tenantId ||
                $rotation['knowledge_base_id'] !== $knowledgeBaseId ||
                $rotation['data_source_id'] !== $dataSourceId ||
                $pair['binding_id'] !== $bindingId || $pair['state'] !== 'active' ||
                $pair['instance_id'] !== $instanceId || $instanceId !== $this->instanceId() ||
                (string)$pair['tenant_id'] !== $tenantId ||
                $pair['knowledge_base_id'] !== $knowledgeBaseId ||
                $pair['data_source_id'] !== $dataSourceId ||
                $rotation['state'] === 'aborted') {
                throw new \DomainException('Rotation operation or source tuple does not match');
            }
            $key = $this->machineKey($keyId, $bindingId);
            if ($key === null || !hash_equals($pair['source_hash'], $key['source_hash'])) {
                throw new \DomainException('Rotation key is missing or changed');
            }
            [$binding, $root] = $this->requireActiveBinding($bindingId);
            if ($pair['source_hash'] !== MachineKeyRegistryService::sourceHash($binding) ||
                $pair['root_hash'] !== self::rootHash($root)) {
                throw new \DomainException('Paired source moved or changed');
            }
            if ($rotation['state'] === 'pending') {
                if ($finalize || (int)$rotation['publication_epoch'] !== $binding['publication_epoch'] ||
                    $pair['key_id'] !== $rotation['old_key_id']) {
                    throw new \DomainException('Pending rotation is stale');
                }
                $old = $this->machineKey($rotation['old_key_id'], $bindingId);
                if ($old === null || !hash_equals($pair['source_hash'], $old['source_hash'])) {
                    throw new \DomainException('Old source key is missing or changed');
                }
                $now = time();
                $expiry = $now + self::ROTATION_GRACE_SECONDS;
                $query = $this->db->getQueryBuilder();
                $query->update('weknora_machine_key')
                    ->set('expires_at', $query->createNamedParameter($expiry))
                    ->where($query->expr()->eq('key_id', $query->createNamedParameter($rotation['old_key_id'])))
                    ->andWhere($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
                    ->executeStatement();
                $query = $this->db->getQueryBuilder();
                $query->update('weknora_src_pair')
                    ->set('key_id', $query->createNamedParameter($keyId))
                    ->set('updated_at', $query->createNamedParameter($now))
                    ->where($query->expr()->eq('operation_id', $query->createNamedParameter($pairOperationId)))
                    ->executeStatement();
                $query = $this->db->getQueryBuilder();
                $query->update('weknora_src_pair_rot')
                    ->set('state', $query->createNamedParameter('committed'))
                    ->set('old_key_expires_at', $query->createNamedParameter($expiry))
                    ->set('updated_at', $query->createNamedParameter($now))
                    ->where($query->expr()->eq('operation_id', $query->createNamedParameter($operationId)))
                    ->executeStatement();
                $rotation['state'] = 'committed';
                $rotation['old_key_expires_at'] = $expiry;
                $this->db->commit();
                return ['rotation' => $this->publicRotation($rotation), 'changed' => true];
            }
            if ($pair['key_id'] !== $keyId ||
                !in_array($rotation['state'], ['committed', 'finalized'], true)) {
                throw new \DomainException('Rotation state is inconsistent');
            }
            if ($finalize && $rotation['state'] === 'committed') {
                $query = $this->db->getQueryBuilder();
                $query->delete('weknora_machine_key')
                    ->where($query->expr()->eq('key_id', $query->createNamedParameter($rotation['old_key_id'])))
                    ->andWhere($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
                    ->executeStatement();
                $query = $this->db->getQueryBuilder();
                $query->update('weknora_src_pair_rot')
                    ->set('state', $query->createNamedParameter('finalized'))
                    ->set('updated_at', $query->createNamedParameter(time()))
                    ->where($query->expr()->eq('operation_id', $query->createNamedParameter($operationId)))
                    ->executeStatement();
                $rotation['state'] = 'finalized';
                $this->db->commit();
                return ['rotation' => $this->publicRotation($rotation), 'changed' => true];
            }
            $this->db->commit();
            return ['rotation' => $this->publicRotation($rotation), 'changed' => false];
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    public function abortRotation(string $bindingId, string $operationId): array {
        if (!self::validOperationId($operationId)) {
            throw new \InvalidArgumentException('Invalid rotation operation');
        }
        $this->db->beginTransaction();
        try {
            $this->lockRegistry();
            $rotation = $this->rotationByOperation(strtolower($operationId));
            if ($rotation === null || $rotation['binding_id'] !== $bindingId) {
                throw new \OutOfBoundsException('Rotation was not found');
            }
            if ($rotation['state'] === 'committed' || $rotation['state'] === 'finalized') {
                throw new \DomainException('Committed rotation must be recovered');
            }
            if ($rotation['state'] === 'aborted' || $rotation['state'] === 'retired') {
                $this->db->commit();
                return ['rotation' => $this->publicRotation($rotation), 'revoked_key' => false];
            }
            $query = $this->db->getQueryBuilder();
            $revoked = $query->delete('weknora_machine_key')
                ->where($query->expr()->eq('key_id', $query->createNamedParameter($rotation['new_key_id'])))
                ->andWhere($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
                ->executeStatement();
            $query = $this->db->getQueryBuilder();
            $query->update('weknora_src_pair_rot')
                ->set('state', $query->createNamedParameter('aborted'))
                ->set('updated_at', $query->createNamedParameter(time()))
                ->where($query->expr()->eq('operation_id', $query->createNamedParameter(strtolower($operationId))))
                ->executeStatement();
            $rotation['state'] = 'aborted';
            $this->db->commit();
            return ['rotation' => $this->publicRotation($rotation), 'revoked_key' => $revoked === 1];
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /** Signed by the still-active old key, so a lost abort ACK is retryable. */
    public function abortRotationMachine(string $bindingId, string $keyId, string $operationId,
        string $pairOperationId, string $instanceId, string $tenantId,
        string $knowledgeBaseId, string $dataSourceId): array {
        $this->validateInput($bindingId, $pairOperationId, $tenantId, $knowledgeBaseId);
        if (!self::validOperationId($operationId) || !self::validRemoteId($dataSourceId)) {
            throw new \InvalidArgumentException('Invalid rotation source identity');
        }
        $operationId = strtolower($operationId);
        $pairOperationId = strtolower($pairOperationId);
        $this->db->beginTransaction();
        try {
            $this->lockRegistry();
            $rotation = $this->rotationByOperation($operationId);
            $pair = $this->byOperation($pairOperationId);
            if ($rotation === null || $pair === null ||
                $rotation['binding_id'] !== $bindingId ||
                $rotation['pair_operation_id'] !== $pairOperationId ||
                !hash_equals($rotation['old_key_id'], $keyId) ||
                $rotation['instance_id'] !== $instanceId ||
                (string)$rotation['tenant_id'] !== $tenantId ||
                $rotation['knowledge_base_id'] !== $knowledgeBaseId ||
                $rotation['data_source_id'] !== $dataSourceId ||
                $pair['binding_id'] !== $bindingId || $pair['state'] !== 'active' ||
                $pair['instance_id'] !== $instanceId || $instanceId !== $this->instanceId() ||
                (string)$pair['tenant_id'] !== $tenantId ||
                $pair['knowledge_base_id'] !== $knowledgeBaseId ||
                $pair['data_source_id'] !== $dataSourceId ||
                $pair['key_id'] !== $keyId ||
                !in_array($rotation['state'], ['pending', 'aborted'], true)) {
                throw new \DomainException('Rotation cannot be aborted');
            }
            $old = $this->machineKey($keyId, $bindingId);
            [$binding, $root] = $this->requireActiveBinding($bindingId);
            if ($old === null || !hash_equals($pair['source_hash'], $old['source_hash']) ||
                $pair['source_hash'] !== MachineKeyRegistryService::sourceHash($binding) ||
                $pair['root_hash'] !== self::rootHash($root)) {
                throw new \DomainException('Paired source changed');
            }
            if ($rotation['state'] === 'aborted') {
                $this->db->commit();
                return ['rotation' => $this->publicRotation($rotation), 'changed' => false];
            }
            $query = $this->db->getQueryBuilder();
            $query->delete('weknora_machine_key')
                ->where($query->expr()->eq('key_id', $query->createNamedParameter($rotation['new_key_id'])))
                ->andWhere($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
                ->executeStatement();
            $query = $this->db->getQueryBuilder();
            $query->update('weknora_src_pair_rot')
                ->set('state', $query->createNamedParameter('aborted'))
                ->set('updated_at', $query->createNamedParameter(time()))
                ->where($query->expr()->eq('operation_id', $query->createNamedParameter($operationId)))
                ->executeStatement();
            $rotation['state'] = 'aborted';
            $this->db->commit();
            return ['rotation' => $this->publicRotation($rotation), 'changed' => true];
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    private function requireCurrentActivePair(string $bindingId): array {
        $pair = $this->liveForBinding($bindingId);
        if ($pair === null || $pair['state'] !== 'active') {
            throw new \DomainException('An active source pairing is required');
        }
        [$binding, $root] = $this->requireActiveBinding($bindingId);
        if ($pair['instance_id'] !== $this->instanceId() ||
            $pair['source_hash'] !== MachineKeyRegistryService::sourceHash($binding) ||
            $pair['root_hash'] !== self::rootHash($root) ||
            $this->machineKey($pair['key_id'], $bindingId) === null) {
            throw new \DomainException('Active source pairing is no longer valid');
        }
        return $pair;
    }

    private function machineKey(string $keyId, string $bindingId): ?array {
        $query = $this->db->getQueryBuilder();
        $result = $query->select('source_hash', 'expires_at')->from('weknora_machine_key')
            ->where($query->expr()->eq('key_id', $query->createNamedParameter($keyId)))
            ->andWhere($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->executeQuery();
        try {
            $row = $result->fetchAssociative();
            return $row === false || ((int)$row['expires_at'] > 0 && (int)$row['expires_at'] <= time())
                ? null : $row;
        } finally {
            $result->closeCursor();
        }
    }

    private function rotationByOperation(string $operationId): ?array {
        $query = $this->db->getQueryBuilder();
        $result = $query->select('*')->from('weknora_src_pair_rot')
            ->where($query->expr()->eq('operation_id', $query->createNamedParameter($operationId)))
            ->executeQuery();
        try {
            $row = $result->fetchAssociative();
            return $row === false ? null : $row;
        } finally {
            $result->closeCursor();
        }
    }

    private function liveRotation(string $pairOperationId): ?array {
        $query = $this->db->getQueryBuilder();
        $result = $query->select('operation_id')->from('weknora_src_pair_rot')
            ->where($query->expr()->eq('pair_operation_id', $query->createNamedParameter($pairOperationId)))
            ->andWhere($query->expr()->orX(
                $query->expr()->eq('state', $query->createNamedParameter('pending')),
                $query->expr()->eq('state', $query->createNamedParameter('committed')),
            ))->setMaxResults(1)->executeQuery();
        try {
            $row = $result->fetchAssociative();
            return $row === false ? null : $row;
        } finally {
            $result->closeCursor();
        }
    }

    private function publicRotation(array $row): array {
        return [
            'operation_id' => (string)$row['operation_id'],
            'pair_operation_id' => (string)$row['pair_operation_id'],
            'binding_id' => (string)$row['binding_id'],
            'instance_id' => (string)$row['instance_id'],
            'tenant_id' => (string)$row['tenant_id'],
            'knowledge_base_id' => (string)$row['knowledge_base_id'],
            'data_source_id' => (string)$row['data_source_id'],
            'old_key_id' => (string)$row['old_key_id'],
            'new_key_id' => (string)$row['new_key_id'],
            'state' => (string)$row['state'],
            'publication_epoch' => (int)$row['publication_epoch'],
            'old_key_expires_at' => (int)$row['old_key_expires_at'],
        ];
    }

    private function validateInput(string $bindingId, string $operationId, string $tenantId,
        string $knowledgeBaseId): void {
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $bindingId) ||
            !self::validOperationId($operationId) || !self::validTenantId($tenantId) ||
            !self::validRemoteId($knowledgeBaseId)) {
            throw new \InvalidArgumentException('Invalid pairing intent');
        }
    }

    public static function validTenantId(mixed $value): bool {
        return is_string($value) &&
            (bool)preg_match('/\A[1-9][0-9]{0,19}\z/D', $value) &&
            (strlen($value) < 20 || strcmp($value, '18446744073709551615') <= 0);
    }

    private function instanceId(): string {
        $id = $this->config->getSystemValueString('instanceid');
        if ($id === '' || strlen($id) > 64) {
            throw new \UnexpectedValueException('Nextcloud instance identity is unavailable');
        }
        return $id;
    }

    private function lockRegistry(): void {
        $this->db->insertIgnoreConflict('weknora_bind_lock', ['id' => 1]);
        $query = $this->db->getQueryBuilder();
        $result = $query->select('id')->from('weknora_bind_lock')
            ->where($query->expr()->eq('id', $query->createNamedParameter(1)))
            ->forUpdate()->executeQuery();
        try {
            if ($result->fetchOne() === false) {
                throw new \UnexpectedValueException('Binding registry lock is missing');
            }
        } finally {
            $result->closeCursor();
        }
    }

    /** @return array{0: array, 1: Folder} */
    private function requireActiveBinding(string $bindingId): array {
        foreach ($this->bindings->listBindings() as $binding) {
            if ($binding['id'] === $bindingId) {
                if ($binding['publication_state'] !== 'active') {
                    throw new BindingPublicationStoppedException('Binding publication is stopped');
                }
                return [$binding, $this->bindings->requireActiveRoot($bindingId)];
            }
        }
        throw new \OutOfBoundsException('Binding was not found');
    }

    private function byOperation(string $operationId): ?array {
        return $this->one('operation_id', $operationId);
    }

    private function latestForBinding(string $bindingId): ?array {
        return $this->one('binding_id', $bindingId, true);
    }

    private function liveForBinding(string $bindingId): ?array {
        return $this->live('binding_id', $bindingId);
    }

    private function liveForTarget(string $tenantId, string $knowledgeBaseId): ?array {
        $query = $this->db->getQueryBuilder();
        $result = $query->select('*')->from('weknora_src_pair')
            ->where($query->expr()->eq('tenant_id', $query->createNamedParameter($tenantId)))
            ->andWhere($query->expr()->eq('knowledge_base_id', $query->createNamedParameter($knowledgeBaseId)))
            ->andWhere($query->expr()->orX(
                $query->expr()->eq('state', $query->createNamedParameter('pending')),
                $query->expr()->eq('state', $query->createNamedParameter('active')),
            ))->setMaxResults(1)->executeQuery();
        try {
            $row = $result->fetchAssociative();
            return $row === false ? null : $row;
        } finally {
            $result->closeCursor();
        }
    }

    private function liveForDataSource(string $dataSourceId): ?array {
        return $this->live('data_source_id', $dataSourceId);
    }

    private function live(string $column, string $value): ?array {
        $query = $this->db->getQueryBuilder();
        $result = $query->select('*')->from('weknora_src_pair')
            ->where($query->expr()->eq($column, $query->createNamedParameter($value)))
            ->andWhere($query->expr()->orX(
                $query->expr()->eq('state', $query->createNamedParameter('pending')),
                $query->expr()->eq('state', $query->createNamedParameter('active')),
            ))->setMaxResults(1)->executeQuery();
        try {
            $row = $result->fetchAssociative();
            return $row === false ? null : $row;
        } finally {
            $result->closeCursor();
        }
    }

    private function one(string $column, string $value, bool $latest = false): ?array {
        $query = $this->db->getQueryBuilder();
        $query->select('*')->from('weknora_src_pair')
            ->where($query->expr()->eq($column, $query->createNamedParameter($value)))
            ->setMaxResults(1);
        if ($latest) {
            $query->orderBy('id', 'DESC');
        }
        $result = $query->executeQuery();
        try {
            $row = $result->fetchAssociative();
            return $row === false ? null : $row;
        } finally {
            $result->closeCursor();
        }
    }

    private function publicRow(array $row): array {
        return [
            'binding_id' => (string)$row['binding_id'],
            'operation_id' => (string)$row['operation_id'],
            'instance_id' => (string)$row['instance_id'],
            'tenant_id' => (string)$row['tenant_id'],
            'knowledge_base_id' => (string)$row['knowledge_base_id'],
            'data_source_id' => $row['data_source_id'] === '' ? null : (string)$row['data_source_id'],
            'state' => (string)$row['state'],
            'key_id' => (string)$row['key_id'],
            'publication_epoch' => (int)$row['publication_epoch'],
        ];
    }
}
