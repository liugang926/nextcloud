<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IConfig;
use OCP\IDBConnection;

/** Recoverable, exact-pair withdrawal. This first version admits empty inventories only. */
final class SourceDecommissionService {
    public const EMPTY_INVENTORY_SHA256 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';

    public function __construct(
        private IDBConnection $db,
        private IConfig $config,
        private BindingRegistryService $bindings,
    ) {
    }

    /** Stop source reads first, then persist an operation which cannot be resumed. */
    public function begin(string $bindingId, string $operationId, string $actorUid): array {
        if (!self::validBindingId($bindingId) ||
            !SourcePairingRegistryService::validOperationId($operationId) ||
            $actorUid === '' || strlen($actorUid) > 64) {
            throw new \InvalidArgumentException('Invalid decommission request');
        }
        $operationId = strtolower($operationId);
        // A crash or DB failure after this independent transaction leaves the
        // source stopped and credentials intact. Repeating begin is safe.
        if ($this->bindings->setPublicationState($bindingId, 'stopped', $actorUid) === null) {
            throw new \OutOfBoundsException('Binding was not found');
        }
        $this->db->beginTransaction();
        try {
            $this->lockRegistry();
            $binding = $this->stoppedBinding($bindingId);
            $pair = $this->activePair($bindingId);
            if ($pair === null || $pair['data_source_id'] === '' ||
                $pair['instance_id'] !== $this->instanceId() ||
                $pair['source_hash'] !== MachineKeyRegistryService::sourceHash($binding)) {
                throw new \DomainException('An exact active source pair is required');
            }
            if ($this->liveRotation((string)$pair['operation_id'])) {
                throw new \DomainException('Finish or abort source-key rotation first');
            }
            $existing = $this->one('binding_id', $bindingId);
            if ($existing !== null) {
                if ($existing['operation_id'] !== $operationId ||
                    !$this->samePair($existing, $pair) ||
                    (int)$existing['publication_epoch'] !== $binding['publication_epoch']) {
                    throw new \DomainException('Decommission operation conflicts with its original pair');
                }
                $this->db->commit();
                return $this->publicRow($existing);
            }
            if ($this->one('operation_id', $operationId) !== null) {
                throw new \DomainException('Decommission operation ID has been used');
            }
            $key = $this->db->getQueryBuilder();
            $result = $key->select('key_id')->from('weknora_machine_key')
                ->where($key->expr()->eq('key_id', $key->createNamedParameter((string)$pair['key_id'])))
                ->andWhere($key->expr()->eq('binding_id', $key->createNamedParameter($bindingId)))
                ->andWhere($key->expr()->eq('expires_at', $key->createNamedParameter(0)))
                ->executeQuery();
            try {
                if ($result->fetchOne() === false) {
                    throw new \DomainException('Paired source credential is unavailable');
                }
            } finally {
                $result->closeCursor();
            }
            $now = time();
            $row = [
                'operation_id' => $operationId,
                'pair_operation_id' => (string)$pair['operation_id'],
                'binding_id' => $bindingId,
                'instance_id' => (string)$pair['instance_id'],
                'tenant_id' => (string)$pair['tenant_id'],
                'knowledge_base_id' => (string)$pair['knowledge_base_id'],
                'data_source_id' => (string)$pair['data_source_id'],
                'key_id' => (string)$pair['key_id'],
                'publication_epoch' => $binding['publication_epoch'],
                'state' => 'prepared',
                'inventory_sha256' => '',
                'created_by_uid' => $actorUid,
                'created_at' => $now,
                'updated_at' => $now,
            ];
            if ($this->db->insertIgnoreConflict('weknora_src_decom', $row) !== 1) {
                throw new \DomainException('Decommission operation conflicts with existing state');
            }
            $this->db->commit();
            return $this->publicRow($row);
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    public function status(string $bindingId): ?array {
        if (!self::validBindingId($bindingId)) {
            throw new \InvalidArgumentException('Invalid binding ID');
        }
        $row = $this->one('binding_id', $bindingId);
        return $row === null ? null : $this->publicRow($row);
    }

    /** Signed machine GET; the operation and the current pair key are pinned. */
    public function intent(string $bindingId, string $operationId, string $keyId): array {
        $row = $this->exactRow($bindingId, $operationId, $keyId);
        if ($row['state'] !== 'prepared' && $row['state'] !== 'acknowledged') {
            throw new \DomainException('Decommission operation is closed');
        }
        $this->stoppedBinding($bindingId);
        return $this->publicRow($row);
    }

    /** An ACK says only that WeKnora proved its dedicated source inventory empty. */
    public function acknowledge(string $bindingId, string $operationId, string $keyId,
        string $pairOperationId, string $instanceId, string $tenantId,
        string $knowledgeBaseId, string $dataSourceId, int $publicationEpoch,
        string $inventorySha256): array {
        if ($inventorySha256 !== self::EMPTY_INVENTORY_SHA256) {
            throw new \DomainException('A complete empty source inventory is required');
        }
        $this->db->beginTransaction();
        try {
            $this->lockRegistry();
            $row = $this->exactRow($bindingId, $operationId, $keyId);
            $this->stoppedBinding($bindingId);
            if ($row['pair_operation_id'] !== $pairOperationId ||
                $row['instance_id'] !== $instanceId ||
                (string)$row['tenant_id'] !== $tenantId ||
                $row['knowledge_base_id'] !== $knowledgeBaseId ||
                $row['data_source_id'] !== $dataSourceId ||
                (int)$row['publication_epoch'] !== $publicationEpoch ||
                !in_array($row['state'], ['prepared', 'acknowledged'], true)) {
                throw new \DomainException('Decommission acknowledgement does not match the pair');
            }
            $pair = $this->activePair($bindingId);
            if ($pair === null || !$this->samePair($row, $pair)) {
                throw new \DomainException('Active source pair changed');
            }
            if ($row['state'] === 'prepared') {
                $query = $this->db->getQueryBuilder();
                if ($query->update('weknora_src_decom')
                    ->set('state', $query->createNamedParameter('acknowledged'))
                    ->set('inventory_sha256', $query->createNamedParameter($inventorySha256))
                    ->set('updated_at', $query->createNamedParameter(time()))
                    ->where($query->expr()->eq('operation_id', $query->createNamedParameter($operationId)))
                    ->andWhere($query->expr()->eq('state', $query->createNamedParameter('prepared')))
                    ->executeStatement() !== 1) {
                    throw new \DomainException('Decommission state changed concurrently');
                }
                $row['state'] = 'acknowledged';
                $row['inventory_sha256'] = $inventorySha256;
            } elseif ($row['inventory_sha256'] !== $inventorySha256) {
                throw new \DomainException('Decommission acknowledgement changed');
            }
            $this->db->commit();
            return $this->publicRow($row);
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /** The registry retires the exact binding, keys and operation in one DB transaction. */
    public function finalize(string $bindingId, string $operationId): array {
        if (!self::validBindingId($bindingId) ||
            !SourcePairingRegistryService::validOperationId($operationId)) {
            throw new \InvalidArgumentException('Invalid decommission request');
        }
        $operationId = strtolower($operationId);
        $row = $this->one('operation_id', $operationId);
        if ($row === null || $row['binding_id'] !== $bindingId) {
            throw new \OutOfBoundsException('Decommission operation was not found');
        }
        if ($row['state'] === 'finalized') {
            return $this->publicRow($row);
        }
        $this->bindings->remove($bindingId, $operationId);
        $done = $this->one('operation_id', $operationId);
        if ($done === null || $done['state'] !== 'finalized') {
            throw new \UnexpectedValueException('Binding retirement did not complete');
        }
        return $this->publicRow($done);
    }

    private function exactRow(string $bindingId, string $operationId, string $keyId): array {
        if (!self::validBindingId($bindingId) ||
            !SourcePairingRegistryService::validOperationId($operationId) ||
            !MachineKeyRegistryService::validKeyId($keyId)) {
            throw new \InvalidArgumentException('Invalid decommission identity');
        }
        $row = $this->one('operation_id', strtolower($operationId));
        if ($row === null || $row['binding_id'] !== $bindingId ||
            !hash_equals((string)$row['key_id'], $keyId)) {
            throw new \DomainException('Decommission operation or key does not match');
        }
        return $row;
    }

    private function stoppedBinding(string $id): array {
        foreach ($this->bindings->listBindings() as $binding) {
            if ($binding['id'] === $id && $binding['publication_state'] === 'stopped') {
                return $binding;
            }
        }
        throw new \DomainException('Binding publication must remain stopped');
    }

    private function activePair(string $id): ?array {
        $query = $this->db->getQueryBuilder();
        $result = $query->select('*')->from('weknora_src_pair')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($id)))
            ->andWhere($query->expr()->eq('state', $query->createNamedParameter('active')))
            ->setMaxResults(2)->executeQuery();
        try {
            $first = $result->fetchAssociative();
            $second = $result->fetchAssociative();
            if ($second !== false) {
                throw new \DomainException('Multiple active source pairings');
            }
            return $first === false ? null : $first;
        } finally {
            $result->closeCursor();
        }
    }

    private function liveRotation(string $pairOperationId): bool {
        $query = $this->db->getQueryBuilder();
        $result = $query->select('operation_id')->from('weknora_src_pair_rot')
            ->where($query->expr()->eq('pair_operation_id', $query->createNamedParameter($pairOperationId)))
            ->andWhere($query->expr()->orX(
                $query->expr()->eq('state', $query->createNamedParameter('pending')),
                $query->expr()->eq('state', $query->createNamedParameter('committed')),
            ))->setMaxResults(1)->executeQuery();
        try {
            return $result->fetchOne() !== false;
        } finally {
            $result->closeCursor();
        }
    }

    private function samePair(array $intent, array $pair): bool {
        foreach (['binding_id', 'instance_id', 'tenant_id', 'knowledge_base_id',
            'data_source_id', 'key_id'] as $name) {
            if ((string)$intent[$name] !== (string)$pair[$name]) {
                return false;
            }
        }
        return $intent['pair_operation_id'] === $pair['operation_id'];
    }

    private function one(string $column, string $value): ?array {
        $query = $this->db->getQueryBuilder();
        $result = $query->select('*')->from('weknora_src_decom')
            ->where($query->expr()->eq($column, $query->createNamedParameter($value)))
            ->setMaxResults(1)->executeQuery();
        try {
            $row = $result->fetchAssociative();
            return $row === false ? null : $row;
        } finally {
            $result->closeCursor();
        }
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

    private function instanceId(): string {
        $id = $this->config->getSystemValueString('instanceid');
        if ($id === '' || strlen($id) > 64) {
            throw new \UnexpectedValueException('Nextcloud instance identity is unavailable');
        }
        return $id;
    }

    private static function validBindingId(string $id): bool {
        return (bool)preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $id);
    }

    private function publicRow(array $row): array {
        return [
            'operation_id' => (string)$row['operation_id'],
            'pair_operation_id' => (string)$row['pair_operation_id'],
            'binding_id' => (string)$row['binding_id'],
            'instance_id' => (string)$row['instance_id'],
            'tenant_id' => (string)$row['tenant_id'],
            'knowledge_base_id' => (string)$row['knowledge_base_id'],
            'data_source_id' => (string)$row['data_source_id'],
            'key_id' => (string)$row['key_id'],
            'publication_epoch' => (int)$row['publication_epoch'],
            'state' => (string)$row['state'],
            'inventory_sha256' => (string)$row['inventory_sha256'],
            'cleanup_state' => in_array($row['state'], ['acknowledged', 'finalized'], true)
                ? 'empty_confirmed' : 'pending',
        ];
    }
}
