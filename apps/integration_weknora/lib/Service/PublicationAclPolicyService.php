<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCA\GroupFolders\Folder\FolderManager;
use OCA\GroupFolders\Mount\GroupMountPoint;
use OCP\App\IAppManager;
use OCP\Files\FileInfo;
use OCP\Files\Folder;
use OCP\Server;

/** V1 publishes only roots whose ACL is uniform throughout the subtree. */
final class PublicationAclPolicyService {
    private const GROUPFOLDERS_VERSION = '22.0.6';

    public function __construct(private IAppManager $apps) {
    }

    public function assertSupported(Folder $root): void {
        $mount = $root->getMountPoint();
        if ($mount === null) {
            throw new UnsupportedPublicationAclException('Publication mount is unavailable');
        }

        // A publisher-owned directory can be shared with a department. The
        // binding still resolves through its owner's local home mount, while
        // each reader is checked separately through that reader's mount view.
        if (get_class($mount) === 'OC\\Files\\Mount\\HomeMountPoint' &&
            $mount->getMountType() === '' &&
            $mount->getMountProvider() === 'OC\\Files\\Mount\\LocalHomeMountProvider') {
            return;
        }

        // This adapter is deliberately tied to the verified Team Folders
        // release. Base Team Folder group grants apply to the whole binding.
        // Advanced permissions may differ per file, so the entire source is
        // unavailable until that mode is disabled or separately supported.
        if (!$mount instanceof GroupMountPoint || $mount->getMountType() !== 'group') {
            throw new UnsupportedPublicationAclException('Publication mount ACL is not supported');
        }
        try {
            if ($this->apps->getAppVersion('groupfolders') !== self::GROUPFOLDERS_VERSION) {
                throw new UnsupportedPublicationAclException('Team Folders version is not verified');
            }
            $folderId = $mount->getFolderId();
            $folder = $mount->getFolder();
            if ($folderId < 1 || $folder->id !== $folderId || $folder->acl ||
                Server::get(FolderManager::class)->getFolderAclEnabled($folderId)) {
                throw new UnsupportedPublicationAclException('Advanced Team Folder ACL is not supported');
            }
        } catch (UnsupportedPublicationAclException $exception) {
            throw $exception;
        } catch (\Throwable $exception) {
            // A missing app service, unknown version or failed ACL lookup
            // cannot be interpreted as a uniform permission policy.
            throw new UnsupportedPublicationAclException('Team Folder ACL proof is unavailable', 0, $exception);
        }
    }

    /** A child mount can carry a different ACL even under a safe root. */
    public function assertSameMount(Folder $root, FileInfo $node): void {
        $rootMount = $root->getMountPoint();
        $nodeMount = $node->getMountPoint();
        if ($rootMount === null || $nodeMount === null ||
            $rootMount->getMountPoint() !== $nodeMount->getMountPoint() ||
            $rootMount->getNumericStorageId() !== $nodeMount->getNumericStorageId()) {
            throw new UnsupportedPublicationAclException('Nested publication mount is not supported');
        }
    }

    /** Used on binding creation, resume and administrator diagnostics. */
    public function assertTreeSupported(Folder $root): void {
        $this->assertSupported($root);
        $pending = [$root];
        $visited = [];
        while ($pending !== []) {
            $folder = array_pop($pending);
            $path = $folder->getPath();
            if (isset($visited[$path])) {
                throw new UnsupportedPublicationAclException('Publication folder cycle is not supported');
            }
            $visited[$path] = true;
            foreach ($folder->getDirectoryListing() as $node) {
                if (!$root->isSubNode($node)) {
                    throw new UnsupportedPublicationAclException('Publication node escaped the bound root');
                }
                $this->assertSameMount($root, $node);
                if ($node instanceof Folder) {
                    $pending[] = $node;
                }
            }
        }
    }
}
