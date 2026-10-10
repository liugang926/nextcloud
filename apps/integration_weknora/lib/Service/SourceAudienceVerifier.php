<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\Files\Folder;
use OCP\IDBConnection;
use OCP\IUser;

/** Current LDAP group membership complements Nextcloud's effective READ check. */
final class SourceAudienceVerifier {
    private array $snapshots = [];
    private array $decisions = [];
    public function __construct(private DirectoryGrantSnapshotService $audience,
        private LiveLdapGuidVerifier $ldap, private IDBConnection $db) {}

    public function allows(Folder $root, string $ownerUid, IUser $user,
        string $directoryId, string $objectGuid, bool $validateDescendants = false): bool {
        $cacheKey = $root->getId() . ':' . $root->getEtag() . ':' . $ownerUid . ':' . $user->getUID() . ':' . (int)$validateDescendants;
        $decisionKey = $cacheKey . ':' . $directoryId . ':' . $objectGuid;
        $decision = $this->decisions[$decisionKey] ?? null;
        if (!$validateDescendants && $decision !== null && $decision['at'] >= time() - 4) { return $decision['allow']; }
        $cached = $this->snapshots[$cacheKey] ?? null;
        if ($validateDescendants || $cached === null || $cached['at'] < time() - 4) {
            // The caller checks this exact file's mount and effective READ.
            // Rechecking the root audience must not walk every source file.
            $cached = ['at' => time(), 'snapshot' => $this->audience->snapshot($root, $ownerUid, $user->getUID(), $validateDescendants)];
            $this->snapshots[$cacheKey] = $cached;
        }
        foreach ($cached['snapshot'] as $subject) {
            if ($subject['directory_id'] !== $directoryId) { continue; }
            if ($subject['kind'] === 'user' && hash_equals($subject['object_guid'], $objectGuid)) {
                $this->decisions[$decisionKey] = ['at' => time(), 'allow' => true]; return true;
            }
            if ($subject['kind'] !== 'group') { continue; }
            $q = $this->db->getQueryBuilder();
            $r = $q->select('nextcloud_gid')->from('weknora_group_identity')
                ->where($q->expr()->eq('directory_id', $q->createNamedParameter($directoryId)))
                ->andWhere($q->expr()->eq('object_guid', $q->createNamedParameter($subject['object_guid'])))->executeQuery();
            try { $gid = $r->fetchOne(); } finally { $r->closeCursor(); }
            if ($gid === false) { throw new \RuntimeException('Source group mapping is unverified'); }
            if ($this->ldap->isEffectiveGroupMember($user, (string)$gid, $directoryId,
                $objectGuid, $subject['object_guid'])) {
                $this->decisions[$decisionKey] = ['at' => time(), 'allow' => true]; return true;
            }
        }
        $this->decisions[$decisionKey] = ['at' => time(), 'allow' => false];
        return false;
    }

    public function invalidate(): void { $this->snapshots = []; $this->decisions = []; }
}
