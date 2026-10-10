<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;

use OCA\GroupFolders\Folder\FolderManager;
use OCA\GroupFolders\Mount\GroupMountPoint;
use OCP\Constants;
use OCP\Files\Folder;
use OCP\IDBConnection;
use OCP\IUserManager;
use OCP\Server;
use OCP\Share\IManager;
use OCP\Share\IShare;

/** Complete root audience without permanently expanding groups into users. */
final class DirectoryGrantSnapshotService {
    public function __construct(private IManager $shares, private IUserManager $users,
        private IdentityMappingService $identities, private LiveLdapGuidVerifier $ldap,
        private PublicationAclPolicyService $acl, private IDBConnection $db) {}

    public function snapshot(Folder $root, string $ownerUid, string $actorUid, bool $validateDescendants = true): array {
        if ($validateDescendants) { $this->acl->assertTreeSupported($root); }
        else { $this->acl->assertSupported($root); }
        $context = $this->users->get($actorUid);
        if ($context === null || !$context->isEnabled()) { throw new \DomainException('Directory actor is unavailable'); }
        $subjects = [];
        $mount = $root->getMountPoint();
        if ($mount instanceof GroupMountPoint) {
            $folder = Server::get(FolderManager::class)->getFolder($mount->getFolderId());
            if ($folder === null || $folder->acl) { throw new UnsupportedPublicationAclException('Team Folder permissions are unavailable'); }
            foreach ($folder->groups as $gid => $grant) {
                if (($grant['type'] ?? null) !== 'group') {
                    throw new UnsupportedPublicationAclException('Team/Circle permissions are not supported');
                }
                if (((int)$grant['permissions'] & Constants::PERMISSION_READ) === 0) { continue; }
                $identity = $this->groupIdentity((string)$gid, $context);
                $subjects[] = ['kind' => 'group'] + $identity + ['source_relation_id' => 'team:' . $folder->id . ':' . $identity['object_guid']];
            }
        } else {
            $owner = $root->getOwner();
            if ($owner === null || $owner->getUID() !== $ownerUid) {
                throw new UnsupportedPublicationAclException('Local publication owner is unverified');
            }
            $subjects[] = ['kind' => 'user'] + $this->identities->forUser($ownerUid) + ['source_relation_id' => 'owner:' . $root->getId()];
        }
        $ancestors = []; $seen = [];
        for ($node = $root; $node instanceof Folder && count($seen) < 256;) {
            if (isset($seen[$node->getId()])) { break; }
            $seen[$node->getId()] = true; $ancestors[$node->getId()] = true;
            try { $node = $node->getParent(); } catch (\Throwable $e) { break; }
        }
        $descendants = []; $pending = $validateDescendants ? [$root] : [];
        while ($pending !== []) {
            foreach (array_pop($pending)->getDirectoryListing() as $node) {
                $descendants[$node->getId()] = true;
                if ($node instanceof Folder) { $pending[] = $node; }
            }
        }
        // Includes reshares created by another user. Failure or incomplete
        // iteration throws before the complete snapshot is ever submitted.
        foreach ($this->shares->getAllShares() as $share) {
            $id = $share->getNodeId();
            $expires = $share->getExpirationDate();
            if ($expires !== null && $expires->getTimestamp() <= time()) { continue; }
            if (($share->getPermissions() & Constants::PERMISSION_READ) === 0) { continue; }
            if (isset($descendants[$id])) {
                throw new UnsupportedPublicationAclException('Descendant shares require per-file permission support');
            }
            if (!isset($ancestors[$id])) { continue; }
            $kind = match ($share->getShareType()) {
                IShare::TYPE_USER => 'user', IShare::TYPE_GROUP => 'group',
                default => throw new UnsupportedPublicationAclException('Publication share type is not supported'),
            };
            $identity = $kind === 'user' ? $this->identities->forUser($share->getSharedWith())
                : $this->groupIdentity($share->getSharedWith(), $context);
            $subjects[] = ['kind' => $kind] + $identity + ['source_relation_id' => 'share:' . $share->getFullId()];
        }
        usort($subjects, static fn (array $a, array $b): int => strcmp($a['source_relation_id'], $b['source_relation_id']));
        return $subjects;
    }

    private function groupIdentity(string $gid, \OCP\IUser $context): array {
        $identity = $this->ldap->groupIdentity($gid, $context);
        $q = $this->db->getQueryBuilder();
        $r = $q->select('directory_id', 'object_guid')->from('weknora_group_identity')
            ->where($q->expr()->eq('nextcloud_gid', $q->createNamedParameter($gid)))->executeQuery();
        try { $existing = $r->fetchAssociative(); } finally { $r->closeCursor(); }
        if ($existing !== false && ($existing['directory_id'] !== $identity['directory_id'] || $existing['object_guid'] !== $identity['object_guid'])) {
            throw new \DomainException('LDAP group identity changed');
        }
        if ($existing === false && $this->db->insertIgnoreConflict('weknora_group_identity', $identity + [
            'nextcloud_gid' => $gid, 'verified_at' => time()]) !== 1) {
            // Concurrent insertion requires an exact retry, never name-based remapping.
            throw new \DomainException('LDAP group mapping is reserved');
        }
        return $identity;
    }
}
