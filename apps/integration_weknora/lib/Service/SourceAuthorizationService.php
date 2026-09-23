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
    ) {
    }

    /** @return array{allow: bool, reason: string, policy_revision: ?string, checked_at: int} */
    public function authorize(string $bindingId, string $directoryId, string $objectGuid, int $fileId): array {
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $bindingId) || $fileId < 1) {
            throw new \InvalidArgumentException('Invalid source identity');
        }
        [$directoryId, $objectGuid] = IdentityMappingService::normalizeIdentity($directoryId, $objectGuid);

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
        $identity = $this->identities->resolve($directoryId, $objectGuid);
        if ($identity === null) {
            return $this->deny('identity_unmapped_or_disabled');
        }

        // A withdrawn source is never authorized, even if a user can still
        // read the original through Nextcloud's ordinary Files interface.
        if ($this->publications->getState($bindingId, $fileId) !== 'eligible') {
            return $this->deny('publication_withdrawn');
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
                return [
                    'allow' => true,
                    'reason' => 'authorized',
                    'policy_revision' => $revision,
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
    private function readablePermissionChain(File $file, Folder $root): ?array {
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

    /** @return array{allow: bool, reason: string, policy_revision: null, checked_at: int} */
    private function deny(string $reason): array {
        return ['allow' => false, 'reason' => $reason, 'policy_revision' => null, 'checked_at' => time()];
    }
}
