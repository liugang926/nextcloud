<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\IConfig;
use OCP\IDBConnection;
use OCP\IUserManager;

/** The local binding registry, currently backed by Nextcloud app config. */
final class BindingRegistryService {
    private const APP_ID = 'integration_weknora';

    public function __construct(
        private IConfig $config,
        private IRootFolder $rootFolder,
        private IUserManager $userManager,
        private IDBConnection $db,
    ) {
    }

    /** @return list<array{id: string, name: string, owner_uid: string, root_file_id: int, publication_state: string, publication_epoch: int}> */
    public function listBindings(): array {
        // App configuration can be changed by another PHP process (for
        // example an administrator using occ). Read the committed registry
        // rather than a worker-local config cache before making an access
        // decision about a live binding.
        return $this->readCommittedBindings();
    }

    /** @return list<array{id: string, name: string, owner_uid: string, root_file_id: int, publication_state: string, publication_epoch: int}> */
    private function readCommittedBindings(): array {
        $read = $this->db->getQueryBuilder();
        $read->select('configvalue')->from('appconfig')
            ->where($read->expr()->eq('appid', $read->createNamedParameter(self::APP_ID)))
            ->andWhere($read->expr()->eq('configkey', $read->createNamedParameter('bindings')));
        $readResult = $read->executeQuery();
        try {
            $raw = $readResult->fetchOne();
        } finally {
            $readResult->closeCursor();
        }
        $bindings = $this->decodeBindings($raw === false ? '[]' : (string)$raw);
        return $this->requireRegisteredSources($bindings);
    }

    /** A retired ID, or one reused for a different root, is never active. */
    private function requireRegisteredSources(array $bindings): array {
        if ($bindings === []) {
            return [];
        }
        $query = $this->db->getQueryBuilder();
        $query->select('binding_id', 'source_hash', 'publication_state', 'publication_epoch')
            ->from('weknora_binding_id')
            ->where($query->expr()->eq('retired_at', $query->createNamedParameter(0)));
        $result = $query->executeQuery();
        try {
            $active = [];
            while (($row = $result->fetchAssociative()) !== false) {
                $active[(string)$row['binding_id']] = $row;
            }
        } finally {
            $result->closeCursor();
        }
        foreach ($bindings as &$binding) {
            $expected = MachineKeyRegistryService::sourceHash($binding);
            if (!isset($active[$binding['id']]) ||
                !hash_equals($expected, (string)$active[$binding['id']]['source_hash'])) {
                throw new \UnexpectedValueException('Binding ID is unregistered or retired');
            }
            $row = $active[$binding['id']];
            $state = $row['publication_state'] ?? null;
            $rawEpoch = $row['publication_epoch'] ?? null;
            $epoch = (is_int($rawEpoch) || is_string($rawEpoch)) &&
                preg_match('/\A(0|[1-9][0-9]*)\z/D', (string)$rawEpoch)
                ? filter_var($rawEpoch, FILTER_VALIDATE_INT,
                    ['options' => ['min_range' => 0]])
                : false;
            if (($state !== 'active' && $state !== 'stopped') || $epoch === false) {
                throw new \UnexpectedValueException('Invalid binding publication state');
            }
            $binding['publication_state'] = $state;
            $binding['publication_epoch'] = $epoch;
        }
        unset($binding);
        return $bindings;
    }

    /** A fresh gate check is required for every source read. */
    public function requirePublicationActive(string $bindingId): int {
        foreach ($this->listBindings() as $binding) {
            if ($binding['id'] !== $bindingId) {
                continue;
            }
            if ($binding['publication_state'] === 'stopped') {
                throw new BindingPublicationStoppedException('Binding publication is stopped');
            }
            return $binding['publication_epoch'];
        }
        throw new \UnexpectedValueException('Binding is no longer configured');
    }

    /**
     * Toggle only the publication gate. Files, keys, binding identity and
     * per-file exclusions are retained. Resume validates today's root view.
     *
     * @return array{publication_state: string, publication_epoch: int, changed: bool}|null
     */
    public function setPublicationState(string $id, string $state, string $actorUid): ?array {
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $id) ||
            !in_array($state, ['active', 'stopped'], true) ||
            $actorUid === '' || strlen($actorUid) > 64) {
            throw new \InvalidArgumentException('Invalid binding publication update');
        }
        $this->db->beginTransaction();
        try {
            $this->db->insertIgnoreConflict('weknora_bind_lock', ['id' => 1]);
            $lock = $this->db->getQueryBuilder();
            $lock->select('id')->from('weknora_bind_lock')
                ->where($lock->expr()->eq('id', $lock->createNamedParameter(1)))
                ->forUpdate();
            $lockResult = $lock->executeQuery();
            try {
                if ($lockResult->fetchOne() === false) {
                    throw new \UnexpectedValueException('Binding registry lock is missing');
                }
            } finally {
                $lockResult->closeCursor();
            }

            $selected = null;
            foreach ($this->readCommittedBindings() as $binding) {
                if ($binding['id'] === $id) {
                    $selected = $binding;
                    break;
                }
            }
            if ($selected === null) {
                $this->db->commit();
                return null;
            }
            if ($selected['publication_state'] === $state) {
                $this->db->commit();
                return ['publication_state' => $state,
                    'publication_epoch' => $selected['publication_epoch'], 'changed' => false];
            }
            if ($state === 'active') {
                // A stopped binding cannot resume against a moved, missing,
                // unreadable, or newly overlapping publication root.
                $this->requireActiveRoot($id);
            }
            if ($selected['publication_epoch'] === PHP_INT_MAX) {
                throw new \UnexpectedValueException('Publication epoch exhausted');
            }
            $epoch = $selected['publication_epoch'] + 1;
            $update = $this->db->getQueryBuilder();
            $changed = $update->update('weknora_binding_id')
                ->set('publication_state', $update->createNamedParameter($state))
                ->set('publication_epoch', $update->createNamedParameter($epoch))
                ->where($update->expr()->eq('binding_id', $update->createNamedParameter($id)))
                ->andWhere($update->expr()->eq('retired_at', $update->createNamedParameter(0)))
                ->andWhere($update->expr()->eq('publication_state',
                    $update->createNamedParameter($selected['publication_state'])))
                ->andWhere($update->expr()->eq('publication_epoch',
                    $update->createNamedParameter($selected['publication_epoch'])))
                ->executeStatement();
            if ($changed !== 1) {
                throw new \UnexpectedValueException('Binding publication state changed concurrently');
            }
            $audit = $this->db->getQueryBuilder();
            $audit->insert('weknora_bind_pub_audit')->values([
                'binding_id' => $audit->createNamedParameter($id),
                'action' => $audit->createNamedParameter($state === 'stopped' ? 'stop' : 'resume'),
                'actor_uid' => $audit->createNamedParameter($actorUid),
                'publication_epoch' => $audit->createNamedParameter($epoch),
                'created_at' => $audit->createNamedParameter(time()),
            ])->executeStatement();
            $this->db->commit();
            return ['publication_state' => $state, 'publication_epoch' => $epoch, 'changed' => true];
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /**
     * Resolve a binding against today's mount view and reject roots that have
     * become overlapping since they were configured. File moves can change
     * ancestry without passing through save(), so every source read must use
     * this check before treating a binding as a publication boundary.
     */
    public function requireActiveRoot(string $bindingId): Folder {
        $selected = null;
        $resolved = [];
        foreach ($this->listBindings() as $binding) {
            try {
                $root = $this->resolveRoot($binding['owner_uid'], $binding['root_file_id']);
            } catch (\InvalidArgumentException $exception) {
                throw new \UnexpectedValueException('A binding root is no longer available', 0, $exception);
            }
            foreach ($resolved as $other) {
                if ($this->overlaps($root, $other)) {
                    throw new \UnexpectedValueException('Binding roots now overlap');
                }
            }
            $resolved[] = $root;
            if ($binding['id'] === $bindingId) {
                $selected = $root;
            }
        }
        if ($selected === null) {
            throw new \UnexpectedValueException('Binding is no longer configured');
        }
        // Once paired, a moved folder must not silently change the source
        // boundary behind the same stable binding and knowledge-base IDs.
        $query = $this->db->getQueryBuilder();
        $result = $query->select('root_hash')->from('weknora_src_pair')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->andWhere($query->expr()->eq('state', $query->createNamedParameter('active')))
            ->setMaxResults(1)->executeQuery();
        try {
            $pairedRootHash = $result->fetchOne();
        } finally {
            $result->closeCursor();
        }
        if ($pairedRootHash !== false &&
            !hash_equals((string)$pairedRootHash, SourcePairingRegistryService::rootHash($selected))) {
            throw new \UnexpectedValueException('Paired binding root has moved');
        }
        return $selected;
    }

    /** @return list<array{id: string, name: string, owner_uid: string, root_file_id: int}> */
    private function decodeBindings(string $raw): array {
        try {
            $bindings = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
        } catch (\JsonException $exception) {
            throw new \UnexpectedValueException('Invalid bindings JSON', 0, $exception);
        }
        if (!is_array($bindings) || !array_is_list($bindings)) {
            throw new \UnexpectedValueException('Bindings must be an array');
        }

        $ids = [];
        foreach ($bindings as $binding) {
            if (!is_array($binding) ||
                !isset($binding['id'], $binding['name'], $binding['owner_uid'], $binding['root_file_id']) ||
                !is_string($binding['id']) || !preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $binding['id']) ||
                !is_string($binding['name']) || $binding['name'] === '' || strlen($binding['name']) > 255 ||
                !is_string($binding['owner_uid']) || $binding['owner_uid'] === '' ||
                !is_int($binding['root_file_id']) || $binding['root_file_id'] < 1 ||
                isset($ids[$binding['id']])) {
                throw new \UnexpectedValueException('Invalid binding');
            }
            $ids[$binding['id']] = true;
        }
        return $bindings;
    }

    /**
     * Add a binding or rename an existing one. Changing a binding's source
     * requires a separate revoke and new binding to avoid stale publications.
     *
     * @return bool True when created.
     */
    public function save(string $id, string $name, string $ownerUid, int $rootFileId): bool {
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $id) ||
            trim($name) === '' || strlen($name) > 255 ||
            $ownerUid === '' || strlen($ownerUid) > 64 || $rootFileId < 1) {
            throw new \InvalidArgumentException('Invalid binding fields');
        }
        $root = $this->resolveRoot($ownerUid, $rootFileId);
        $this->db->beginTransaction();
        try {
            // Fresh app installs can create the lock table without running
            // postSchemaChange's seed hook. Make the first write safe as well.
            $this->db->insertIgnoreConflict('weknora_bind_lock', ['id' => 1]);
            // A single row lock serializes API writers. Read appconfig directly
            // while holding it so a stale per-request config cache cannot
            // overwrite a concurrent administrator's binding.
            $lock = $this->db->getQueryBuilder();
            $lock->select('id')->from('weknora_bind_lock')
                ->where($lock->expr()->eq('id', $lock->createNamedParameter(1)))
                ->forUpdate();
            $lockResult = $lock->executeQuery();
            try {
                if ($lockResult->fetchOne() === false) {
                    throw new \UnexpectedValueException('Binding registry lock is missing');
                }
            } finally {
                $lockResult->closeCursor();
            }

            $bindings = $this->readCommittedBindings();
            $found = false;
            foreach ($bindings as &$binding) {
                if ($binding['id'] === $id) {
                    if ($binding['owner_uid'] !== $ownerUid || $binding['root_file_id'] !== $rootFileId) {
                        throw new \DomainException('Changing a binding source is not supported');
                    }
                    $binding['name'] = $name;
                    $found = true;
                    continue;
                }
                // In V1 we cannot establish reliable physical ancestry for
                // shared mounts presented by two different user roots.
                if ($binding['owner_uid'] !== $ownerUid) {
                    throw new \DomainException('Cross-owner bindings require a verified mount model');
                }
                $otherRoot = $this->resolveRoot($binding['owner_uid'], $binding['root_file_id']);
                if ($this->overlaps($root, $otherRoot)) {
                    throw new \DomainException('Binding roots overlap');
                }
            }
            unset($binding);
            if (!$found) {
                $registered = $this->db->insertIgnoreConflict('weknora_binding_id', [
                    'binding_id' => $id,
                    'source_hash' => MachineKeyRegistryService::sourceHash([
                        'id' => $id,
                        'owner_uid' => $ownerUid,
                        'root_file_id' => $rootFileId,
                    ]),
                    'retired_at' => 0,
                ]);
                if ($registered !== 1) {
                    throw new \DomainException('Binding ID has already been used');
                }
                $bindings[] = [
                    'id' => $id,
                    'name' => $name,
                    'owner_uid' => $ownerUid,
                    'root_file_id' => $rootFileId,
                ];
            }
            $storedBindings = array_map(static fn (array $binding): array => [
                'id' => $binding['id'],
                'name' => $binding['name'],
                'owner_uid' => $binding['owner_uid'],
                'root_file_id' => $binding['root_file_id'],
            ], $bindings);
            $this->config->setAppValue(self::APP_ID, 'bindings', json_encode($storedBindings, JSON_THROW_ON_ERROR));
            $this->db->commit();
            return !$found;
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /** Remove a binding and all of its machine and event credentials atomically. */
    public function remove(string $id): ?int {
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $id)) {
            throw new \InvalidArgumentException('Invalid binding ID');
        }
        $this->db->beginTransaction();
        try {
            $this->db->insertIgnoreConflict('weknora_bind_lock', ['id' => 1]);
            $lock = $this->db->getQueryBuilder();
            $lock->select('id')->from('weknora_bind_lock')
                ->where($lock->expr()->eq('id', $lock->createNamedParameter(1)))
                ->forUpdate();
            $lockResult = $lock->executeQuery();
            try {
                if ($lockResult->fetchOne() === false) {
                    throw new \UnexpectedValueException('Binding registry lock is missing');
                }
            } finally {
                $lockResult->closeCursor();
            }
            $bindings = $this->readCommittedBindings();
            $remaining = array_values(array_filter($bindings,
                static fn (array $binding): bool => $binding['id'] !== $id));
            if (count($remaining) === count($bindings)) {
                $this->db->commit();
                return null;
            }
            $retire = $this->db->getQueryBuilder();
            $retired = $retire->update('weknora_binding_id')
                ->set('retired_at', $retire->createNamedParameter(time()))
                ->where($retire->expr()->eq('binding_id', $retire->createNamedParameter($id)))
                ->andWhere($retire->expr()->eq('retired_at', $retire->createNamedParameter(0)))
                ->executeStatement();
            if ($retired !== 1) {
                throw new \UnexpectedValueException('Binding ID registration is missing');
            }
            $delete = $this->db->getQueryBuilder();
            $revoked = $delete->delete('weknora_machine_key')
                ->where($delete->expr()->eq('binding_id', $delete->createNamedParameter($id)))
                ->executeStatement();
            $eventDelete = $this->db->getQueryBuilder();
            $eventDelete->delete('weknora_event_conn')
                ->where($eventDelete->expr()->eq('binding_id', $eventDelete->createNamedParameter($id)))
                ->executeStatement();
            $retirePair = $this->db->getQueryBuilder();
            $retirePair->update('weknora_src_pair')
                ->set('state', $retirePair->createNamedParameter('retired'))
                ->set('updated_at', $retirePair->createNamedParameter(time()))
                ->where($retirePair->expr()->eq('binding_id', $retirePair->createNamedParameter($id)))
                ->andWhere($retirePair->expr()->orX(
                    $retirePair->expr()->eq('state', $retirePair->createNamedParameter('pending')),
                    $retirePair->expr()->eq('state', $retirePair->createNamedParameter('active')),
                ))->executeStatement();
            $retireRotation = $this->db->getQueryBuilder();
            $retireRotation->update('weknora_src_pair_rot')
                ->set('state', $retireRotation->createNamedParameter('retired'))
                ->set('updated_at', $retireRotation->createNamedParameter(time()))
                ->where($retireRotation->expr()->eq('binding_id', $retireRotation->createNamedParameter($id)))
                ->andWhere($retireRotation->expr()->orX(
                    $retireRotation->expr()->eq('state', $retireRotation->createNamedParameter('pending')),
                    $retireRotation->expr()->eq('state', $retireRotation->createNamedParameter('committed')),
                ))->executeStatement();
            $storedRemaining = array_map(static fn (array $binding): array => [
                'id' => $binding['id'],
                'name' => $binding['name'],
                'owner_uid' => $binding['owner_uid'],
                'root_file_id' => $binding['root_file_id'],
            ], $remaining);
            $this->config->setAppValue(self::APP_ID, 'bindings',
                json_encode($storedRemaining, JSON_THROW_ON_ERROR));
            $this->db->commit();
            return $revoked;
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    private function overlaps(Folder $a, Folder $b): bool {
        if ($a->getId() === $b->getId() || $a->isSubNode($b) || $b->isSubNode($a)) {
            return true;
        }
        if ($a->getStorage()->getId() !== $b->getStorage()->getId()) {
            return false;
        }
        $aPath = trim($a->getInternalPath(), '/');
        $bPath = trim($b->getInternalPath(), '/');
        if ($aPath === '' || $bPath === '') {
            return true;
        }
        return $aPath === $bPath || str_starts_with($aPath . '/', $bPath . '/') ||
            str_starts_with($bPath . '/', $aPath . '/');
    }

    private function resolveRoot(string $ownerUid, int $rootFileId): Folder {
        $owner = $this->userManager->get($ownerUid);
        if ($owner === null || !$owner->isEnabled()) {
            throw new \InvalidArgumentException('Unknown binding owner');
        }
        $userFolder = $this->rootFolder->getUserFolder($ownerUid);
        if ($userFolder->getId() === $rootFileId) {
            throw new \InvalidArgumentException('Cannot bind an entire user files root');
        }
        foreach ($userFolder->getById($rootFileId) as $node) {
            if ($node instanceof Folder && $node->getId() === $rootFileId &&
                $userFolder->isSubNode($node) && $node->isReadable()) {
                return $node;
            }
        }
        throw new \InvalidArgumentException('Binding root is not a readable owner folder');
    }
}
