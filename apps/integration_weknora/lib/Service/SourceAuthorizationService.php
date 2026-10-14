<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\Files\File;
use OCP\Files\Folder;
use OCP\Files\IRootFolder;

/** Current, user-scoped source authorization for one published file. */
final class SourceAuthorizationService {
    public function __construct(
        private BindingRegistryService $bindings,
        private IdentityMappingService $identities,
        private FilePublicationStateService $publications,
        private IRootFolder $rootFolder,
        private SourceAudienceVerifier $audience,
        private SourceContentRevisionService $contentRevisions,
        private SourcePairingRegistryService $pairings,
    ) {
    }

    /** Fresh root proof for source KB metadata; never authorizes any document. */
    public function authorizeRoot(string $bindingId, string $directoryId, string $objectGuid): array {
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $bindingId)) {
            throw new \InvalidArgumentException('Invalid source identity');
        }
        [$directoryId, $objectGuid] = IdentityMappingService::normalizeIdentity($directoryId, $objectGuid);
        $binding = null;
        foreach ($this->bindings->listBindings() as $candidate) {
            if ($candidate['id'] === $bindingId) { $binding = $candidate; break; }
        }
        if ($binding === null) { return $this->deny('binding_not_found'); }
        $pair = $this->pairings->status($bindingId);
        if ($pair === null || $pair['state'] !== 'active') { return $this->deny('source_pairing_inactive'); }
        try { $epoch = $this->bindings->requirePublicationActive($bindingId); }
        catch (BindingPublicationStoppedException $e) { return $this->deny('publication_stopped'); }
        $identity = $this->identities->resolve($directoryId, $objectGuid);
        if ($identity === null) { return $this->deny('identity_unmapped_or_disabled'); }
        $ownerRoot = $this->bindings->requireActiveRoot($bindingId);
        if (!$ownerRoot->isReadable()) { return $this->deny('source_not_published'); }
        // A KB title must not expose a source whose current permission mode
        // is unknown. The first-phase adapter requires a uniform whole tree.
        if (!$this->rootAudience($bindingId, $ownerRoot, $binding['owner_uid'], $identity['user'],
            $directoryId, $objectGuid)) {
            return $this->deny('current_directory_membership_denied');
        }
        $userFolder = $this->rootFolder->getUserFolder($identity['uid']);
        if ($userFolder->getId() === $binding['root_file_id']) { return $this->deny('source_not_readable'); }
        foreach ($userFolder->getById($binding['root_file_id']) as $root) {
            if (!$root instanceof Folder || $root->getId() !== $binding['root_file_id'] ||
                !$userFolder->isSubNode($root) || !$root->isReadable()) { continue; }
            $permissions = $this->readablePermissionChain($root, $root);
            if ($permissions === null) { continue; }
            if ($this->bindings->requirePublicationActive($bindingId) !== $epoch ||
                $this->pairings->status($bindingId) !== $pair ||
                $this->bindings->requireActiveRoot($bindingId)->getPath() !== $ownerRoot->getPath() ||
                $this->identities->resolve($directoryId, $objectGuid) === null ||
                !$this->rootAudience($bindingId, $ownerRoot, $binding['owner_uid'], $identity['user'],
                    $directoryId, $objectGuid) || !$root->isReadable()) {
                return $this->deny('publication_changed');
            }
            return ['allow' => true, 'reason' => 'authorized', 'binding_id' => $bindingId,
                'publication_epoch' => (string)$epoch, 'directory_id' => $directoryId,
                'object_guid' => $objectGuid, 'policy_revision' => hash('sha256', json_encode([
                    $bindingId, $epoch, $identity['mapping_id'], $permissions], JSON_THROW_ON_ERROR)),
                'checked_at' => time()];
        }
        return $this->deny('source_not_readable');
    }

    private function rootAudience(string $bindingId, Folder $root, string $ownerUid,
        \OCP\IUser $user, string $directoryId, string $objectGuid): bool {
        try { return $this->audience->allows($root, $ownerUid, $user, $directoryId, $objectGuid, true); }
        catch (UnsupportedPublicationAclException $e) {
            // Unsupported descendants invalidate the whole uniform source.
            $this->bindings->setPublicationState($bindingId, 'stopped', 'acl-policy');
            throw $e;
        }
    }

    /** @return array{allow: bool, reason: string, policy_revision: ?string, source_etag: ?string, checked_at: int} */
    public function authorize(string $bindingId, string $directoryId, string $objectGuid, int $fileId, string $expectedContentRevision = '0'): array {
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $bindingId) || $fileId < 1) {
            throw new \InvalidArgumentException('Invalid source identity');
        }
        [$directoryId, $objectGuid] = IdentityMappingService::normalizeIdentity($directoryId, $objectGuid);
        try { $contentRevision = $this->contentRevisions->current($fileId); }
        catch (\Throwable $e) { return $this->deny('source_version_unverified'); }
        if ($expectedContentRevision !== $contentRevision) { return $this->deny('source_version_changed'); }

        $binding = null;
        foreach ($this->bindings->listBindings() as $candidate) {
            if ($candidate['id'] === $bindingId) {
                $binding = $candidate;
                break;
            }
        }
        if ($binding === null) {
            return $this->deny('binding_not_found');
        }
        try {
            $bindingEpoch = $this->bindings->requirePublicationActive($bindingId);
        } catch (BindingPublicationStoppedException $exception) {
            return $this->deny('publication_stopped');
        }
        $identity = $this->identities->resolve($directoryId, $objectGuid);
        if ($identity === null) {
            return $this->deny('identity_unmapped_or_disabled');
        }

        // A withdrawn source is never authorized, even if a user can still
        // read the original through Nextcloud's ordinary Files interface.
        if ($this->publications->getState($bindingId, $fileId) !== 'eligible') {
            return $this->deny('publication_withdrawn');
        }

        // A user's share alone cannot authorize a source the configured
        // publisher can no longer read. The owner-side check also excludes
        // files hidden by a more restrictive ACL within an otherwise readable
        // binding root.
        $ownerRoot = $this->bindings->requireActiveRoot($bindingId);
        if ($identity['user']->getBackendClassName() === 'LDAP' &&
            !$this->audience->allows($ownerRoot, $binding['owner_uid'], $identity['user'], $directoryId, $objectGuid)) {
            return $this->deny('current_directory_membership_denied');
        }
        $ownerCanPublish = false;
        foreach ($ownerRoot->getById($fileId) as $ownerFile) {
            if (!$ownerFile instanceof File || $ownerFile->getId() !== $fileId ||
                !$ownerRoot->isSubNode($ownerFile)) {
                continue;
            }
            $this->bindings->assertNodeInSupportedMount($bindingId, $ownerRoot, $ownerFile);
            if ($this->readablePermissionChain($ownerFile, $ownerRoot) !== null) {
                $ownerCanPublish = true;
                break;
            }
        }
        if (!$ownerCanPublish) {
            return $this->deny('source_not_published');
        }

        // getUserFolder(uid) builds that user's mount view. Never resolve the
        // node through the binding owner's view or the connector's account.
        $userFolder = $this->rootFolder->getUserFolder($identity['uid']);
        if ($userFolder->getId() === $binding['root_file_id']) {
            return $this->deny('source_not_readable');
        }

        foreach ($userFolder->getById($binding['root_file_id']) as $root) {
            if (!$root instanceof Folder || $root->getId() !== $binding['root_file_id'] ||
                !$userFolder->isSubNode($root) || !$root->isReadable()) {
                continue;
            }
            foreach ($root->getById($fileId) as $file) {
                if (!$file instanceof File || $file->getId() !== $fileId ||
                    !$root->isSubNode($file) || !$userFolder->isSubNode($file)) {
                    continue;
                }
                $this->bindings->assertNodeInSupportedMount($bindingId, $root, $file);
                $permissionChain = $this->readablePermissionChain($file, $root);
                if ($permissionChain === null) {
                    continue;
                }
                $revision = hash('sha256', json_encode([
                    $bindingId,
                    $binding['root_file_id'],
                    $identity['mapping_id'],
                    $permissionChain,
                ], JSON_THROW_ON_ERROR));
                try {
                    $freshEpoch = $this->bindings->requirePublicationActive($bindingId);
                    $this->bindings->requireActiveRoot($bindingId);
                } catch (BindingPublicationStoppedException $exception) {
                    return $this->deny('publication_stopped');
                }
                if ($this->contentRevisions->current($fileId) !== $contentRevision || $freshEpoch !== $bindingEpoch ||
                    $this->publications->getState($bindingId, $fileId) !== 'eligible') {
                    return $this->deny('publication_changed');
                }
                return [
                    'allow' => true,
                    'reason' => 'authorized',
                    'policy_revision' => $revision,
                    // The reader must compare this live source version with
                    // the version indexed in WeKnora. A changed file cannot
                    // keep serving its previous answer while a new build is
                    // pending or has failed.
                    'source_etag' => $file->getEtag(),
                    'source_content_revision' => $contentRevision,
                    'checked_at' => time(),
                ];
            }
        }
        return $this->deny('source_not_readable');
    }

    /**
     * Verify every directory from the target back to the bound root. A file
     * cache lookup alone does not establish access to intermediate folders.
     *
     * @return list<array{int, int}>|null
     */
    private function readablePermissionChain(File|Folder $file, Folder $root): ?array {
        $cursor = $file;
        $permissions = [];
        $visited = [];
        for ($depth = 0; $depth < 1024; $depth++) {
            $path = $cursor->getPath();
            if (isset($visited[$path]) || !$cursor->isReadable()) {
                return null;
            }
            $visited[$path] = true;
            $permissions[] = [(int)$cursor->getId(), (int)$cursor->getPermissions()];
            if ($path === $root->getPath() && $cursor->getId() === $root->getId()) {
                return $permissions;
            }
            $parent = $cursor->getParent();
            if (!$parent instanceof Folder || !$root->isSubNode($parent) &&
                !($parent->getPath() === $root->getPath() && $parent->getId() === $root->getId())) {
                return null;
            }
            $cursor = $parent;
        }
        return null;
    }

    /** @return array{allow: bool, reason: string, policy_revision: null, source_etag: null, checked_at: int} */
    private function deny(string $reason): array {
        return ['allow' => false, 'reason' => $reason, 'policy_revision' => null,
            'source_etag' => null, 'source_content_revision' => null, 'checked_at' => time()];
    }
}
