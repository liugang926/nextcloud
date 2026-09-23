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

    /** @return list<array{id: string, name: string, owner_uid: string, root_file_id: int}> */
    public function listBindings(): array {
        // App configuration can be changed by another PHP process (for
        // example an administrator using occ). Read the committed registry
        // rather than a worker-local config cache before making an access
        // decision about a live binding.
        return $this->readCommittedBindings();
    }

    /** @return list<array{id: string, name: string, owner_uid: string, root_file_id: int}> */
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
        return $this->decodeBindings($raw === false ? '[]' : (string)$raw);
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
                $bindings[] = [
                    'id' => $id,
                    'name' => $name,
                    'owner_uid' => $ownerUid,
                    'root_file_id' => $rootFileId,
                ];
            }
            $this->config->setAppValue(self::APP_ID, 'bindings', json_encode($bindings, JSON_THROW_ON_ERROR));
            $this->db->commit();
            return !$found;
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
