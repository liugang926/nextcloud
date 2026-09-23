<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IDBConnection;
use OCP\IUser;
use OCP\IUserManager;

/** Administrator-attested, persistent and unambiguous AD identity links. */
final class IdentityMappingService {
    public function __construct(
        private IDBConnection $db,
        private IUserManager $users,
    ) {
    }

    public static function normalizeIdentity(string $directoryId, string $objectGuid): array {
        if (!preg_match('/\A[A-Za-z0-9._:-]{1,128}\z/D', $directoryId) ||
            !preg_match('/\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\z/D', $objectGuid)) {
            throw new \InvalidArgumentException('Invalid stable directory identity');
        }
        // The AD binary objectGUID must be converted to this canonical UUID
        // form by the caller. Names and email addresses are never identities.
        return [$directoryId, strtolower($objectGuid)];
    }

    /** @return list<array<string, mixed>> */
    public function listMappings(): array {
        $query = $this->db->getQueryBuilder();
        $query->select('directory_id', 'object_guid', 'nextcloud_uid', 'backend_class', 'actor_uid', 'created_at')
            ->from('weknora_identities')
            ->orderBy('directory_id')
            ->addOrderBy('object_guid');
        $result = $query->executeQuery();
        try {
            return $result->fetchAllAssociative();
        } finally {
            $result->closeCursor();
        }
    }

    /** @return array{uid: string, user: IUser, mapping_id: int}|null */
    public function resolve(string $directoryId, string $objectGuid): ?array {
        [$directoryId, $objectGuid] = self::normalizeIdentity($directoryId, $objectGuid);
        $mapping = $this->findPrincipal($directoryId, $objectGuid);
        if ($mapping === null) {
            return null;
        }
        $uid = (string)$mapping['nextcloud_uid'];
        $user = $this->users->get($uid);
        if ($user === null || !$user->isEnabled() || $user->getUID() !== $uid ||
            $user->getBackendClassName() !== $mapping['backend_class']) {
            return null;
        }
        return ['uid' => $uid, 'user' => $user, 'mapping_id' => (int)$mapping['id']];
    }

    /** @return bool true if inserted, false for identical existing mapping */
    public function create(string $directoryId, string $objectGuid, string $uid, string $actorUid): bool {
        [$directoryId, $objectGuid] = self::normalizeIdentity($directoryId, $objectGuid);
        self::assertUids($uid, $actorUid);
        $user = $this->users->get($uid);
        if ($user === null || !$user->isEnabled() || $user->getUID() !== $uid) {
            throw new \InvalidArgumentException('Nextcloud account is unavailable');
        }
        $backendClass = $user->getBackendClassName();
        if (!is_string($backendClass) || $backendClass === '' || strlen($backendClass) > 255) {
            throw new \InvalidArgumentException('Unknown Nextcloud account backend');
        }

        $this->db->beginTransaction();
        try {
            $this->lockRegistry();
            $existing = $this->findPrincipal($directoryId, $objectGuid);
            if ($existing !== null) {
                if ($existing['nextcloud_uid'] !== $uid || $existing['backend_class'] !== $backendClass) {
                    throw new \DomainException('Directory principal is already mapped');
                }
                $this->db->commit();
                return false;
            }
            if ($this->findUid($uid) !== null) {
                throw new \DomainException('Nextcloud UID is already mapped');
            }

            $timestamp = time();
            $query = $this->db->getQueryBuilder();
            $query->insert('weknora_identities')->values([
                'directory_id' => $query->createNamedParameter($directoryId),
                'object_guid' => $query->createNamedParameter($objectGuid),
                'nextcloud_uid' => $query->createNamedParameter($uid),
                'backend_class' => $query->createNamedParameter($backendClass),
                'actor_uid' => $query->createNamedParameter($actorUid),
                'created_at' => $query->createNamedParameter($timestamp),
            ])->executeStatement();
            $this->audit($directoryId, $objectGuid, $uid, 'created', $actorUid, $timestamp);
            $this->db->commit();
            return true;
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /** @return bool true if deleted, false if absent */
    public function revoke(string $directoryId, string $objectGuid, string $uid, string $actorUid): bool {
        [$directoryId, $objectGuid] = self::normalizeIdentity($directoryId, $objectGuid);
        self::assertUids($uid, $actorUid);
        $this->db->beginTransaction();
        try {
            $this->lockRegistry();
            $existing = $this->findPrincipal($directoryId, $objectGuid);
            if ($existing === null) {
                $this->db->commit();
                return false;
            }
            if ($existing['nextcloud_uid'] !== $uid) {
                throw new \DomainException('Mapping does not match Nextcloud UID');
            }
            $query = $this->db->getQueryBuilder();
            $query->delete('weknora_identities')
                ->where($query->expr()->eq('id', $query->createNamedParameter((int)$existing['id'])))
                ->executeStatement();
            $this->audit($directoryId, $objectGuid, $uid, 'revoked', $actorUid, time());
            $this->db->commit();
            return true;
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    private static function assertUids(string $uid, string $actorUid): void {
        if ($uid === '' || strlen($uid) > 64 || $actorUid === '' || strlen($actorUid) > 64) {
            throw new \InvalidArgumentException('Invalid Nextcloud UID');
        }
    }

    private function lockRegistry(): void {
        // Reuse the app's seeded singleton lock. It serializes identity writes
        // without relying on DB-specific upsert or aborted-transaction behavior.
        $query = $this->db->getQueryBuilder();
        $query->select('id')->from('weknora_bind_lock')
            ->where($query->expr()->eq('id', $query->createNamedParameter(1)))
            ->forUpdate();
        $result = $query->executeQuery();
        try {
            if ($result->fetchOne() === false) {
                throw new \UnexpectedValueException('Identity registry lock missing');
            }
        } finally {
            $result->closeCursor();
        }
    }

    private function findPrincipal(string $directoryId, string $objectGuid): ?array {
        $query = $this->db->getQueryBuilder();
        $query->select('id', 'nextcloud_uid', 'backend_class')
            ->from('weknora_identities')
            ->where($query->expr()->eq('directory_id', $query->createNamedParameter($directoryId)))
            ->andWhere($query->expr()->eq('object_guid', $query->createNamedParameter($objectGuid)));
        $result = $query->executeQuery();
        try {
            $row = $result->fetchAssociative();
            return $row === false ? null : $row;
        } finally {
            $result->closeCursor();
        }
    }

    private function findUid(string $uid): ?array {
        $query = $this->db->getQueryBuilder();
        $query->select('id')->from('weknora_identities')
            ->where($query->expr()->eq('nextcloud_uid', $query->createNamedParameter($uid)));
        $result = $query->executeQuery();
        try {
            $row = $result->fetchAssociative();
            return $row === false ? null : $row;
        } finally {
            $result->closeCursor();
        }
    }

    private function audit(string $directoryId, string $objectGuid, string $uid, string $action, string $actorUid, int $timestamp): void {
        $query = $this->db->getQueryBuilder();
        $query->insert('weknora_identity_audit')->values([
            'directory_id' => $query->createNamedParameter($directoryId),
            'object_guid' => $query->createNamedParameter($objectGuid),
            'nextcloud_uid' => $query->createNamedParameter($uid),
            'action' => $query->createNamedParameter($action),
            'actor_uid' => $query->createNamedParameter($actorUid),
            'created_at' => $query->createNamedParameter($timestamp),
        ])->executeStatement();
    }
}
