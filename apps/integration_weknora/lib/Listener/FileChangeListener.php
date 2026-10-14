<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Listener;

use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\ChangeOutboxService;
use OCA\IntegrationWeknora\Service\RootMigrationService;
use OCP\EventDispatcher\Event;
use OCP\EventDispatcher\IEventListener;
use OCP\Files\Events\Node\BeforeNodeRenamedEvent;
use OCP\Files\Events\Node\BeforeNodeDeletedEvent;
use OCP\Files\Events\Node\NodeCopiedEvent;
use OCP\Files\Events\Node\NodeCreatedEvent;
use OCP\Files\Events\Node\NodeDeletedEvent;
use OCP\Files\Events\Node\NodeRenamedEvent;
use OCP\Files\Events\Node\NodeTouchedEvent;
use OCP\Files\Events\Node\NodeWrittenEvent;
use OCP\Files\File;
use OCP\Files\FileInfo;
use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\Files\Node;
use OCP\IDBConnection;
use Psr\Log\LoggerInterface;

/** @implements IEventListener<Event> */
final class FileChangeListener implements IEventListener {
    /** @var array<string, int> Captured before rename; never enqueued before success. */
    private array $pendingRenameSourceIds = [];
    private array $pendingRenameScopes = [];
    /** Canonical owner-view facts captured before delete; never a pre-delete hint. */
    private array $pendingDeletes = [];

    public function __construct(
        private BindingRegistryService $bindings,
        private IRootFolder $rootFolder,
        private ChangeOutboxService $outbox,
        private LoggerInterface $logger,
        private ?RootMigrationService $rootMoves = null,
        private ?IDBConnection $db = null,
    ) {
    }

    public function handle(Event $event): void {
        try {
            if ($event instanceof BeforeNodeRenamedEvent) {
                if ($event->getSource() instanceof Folder && $this->rootMoves !== null) { $this->rootMoves->capture($event->getSource()); }
                $this->captureRenameIdentity($event);
                return;
            }
            if ($event instanceof BeforeNodeDeletedEvent) {
                $this->captureDelete($event->getNode());
                return;
            }
            $bindings = $this->bindings->listBindings();
        } catch (\Throwable $exception) {
            $this->logFailure($event, $exception);
            return;
        }

        if ($event instanceof NodeRenamedEvent && $this->rootMoves !== null) { $this->rootMoves->observe(); }
        $capturedSourceId = $event instanceof NodeRenamedEvent ?
            $this->takeRenameIdentity($event) : null;
        $renameKey = $event instanceof NodeRenamedEvent ? $this->renameKey($event->getSource(), $event->getTarget()) : null;
        $renameScopes = $renameKey === null ? [] : ($this->pendingRenameScopes[$renameKey] ?? []);
        foreach ($bindings as $binding) {
            try {
                $rootPath = $this->bindingRootPath($binding);
                $rootId = $binding['root_file_id'];
                $bindingId = $binding['id'];

                if ($event instanceof NodeRenamedEvent) {
                    $target = $this->canonicalNode($binding, $event->getTarget());
                    $this->recordRename($bindingId, $rootId, $rootPath, $event, $capturedSourceId,
                        $renameScopes[$bindingId] ?? null, $target);
                } elseif ($event instanceof NodeDeletedEvent) {
                    $captured = $this->pendingDeletes[$this->deleteKey($event->getNode())][$bindingId] ?? null;
                    if ($captured !== null) {
                        // Deleting a receiver's shared mount can be only an
                        // unshare. A surviving canonical source is not a tombstone.
                        $survivor = $this->canonicalNode($binding, $event->getNode(), $captured['file_id']);
                        if ($survivor !== null) { $this->outbox->append($bindingId, null, 'reconcile'); }
                        else { $this->recordCapturedDelete($binding, $captured); }
                    } else {
                        if ($this->canonicalNode($binding, $event->getNode()) !== null) {
                            $this->outbox->append($bindingId, null, 'reconcile');
                        } else { $this->recordDelete($bindingId, $rootId, $rootPath, $event->getNode()); }
                    }
                } elseif ($event instanceof NodeCopiedEvent) {
                    $canonical = $this->canonicalNode($binding, $event->getTarget());
                    if ($canonical !== null) { $this->recordCurrent($bindingId, $rootId, $rootPath, $canonical); }
                } elseif ($event instanceof NodeCreatedEvent || $event instanceof NodeWrittenEvent ||
                    $event instanceof NodeTouchedEvent) {
                    $canonical = $this->canonicalNode($binding, $event->getNode());
                    if ($canonical !== null) { $this->recordCurrent($bindingId, $rootId, $rootPath, $canonical); }
                }
            } catch (\Throwable $exception) {
                $this->logFailure($event, $exception);
            }
        }
        if ($event instanceof NodeDeletedEvent) { unset($this->pendingDeletes[$this->deleteKey($event->getNode())]); }
        if ($renameKey !== null) { unset($this->pendingRenameScopes[$renameKey]); }
    }

    private function deleteKey(Node $node): string { return $this->nodePath($node) ?? ('id:' . ($this->nodeId($node) ?? 'unknown')); }

    private function captureDelete(Node $node): void {
        $key = $this->deleteKey($node); $facts = [];
        foreach ($this->bindings->listBindings() as $binding) {
            $canonical = $this->canonicalNode($binding, $node);
            if ($canonical === null) { continue; }
            $facts[$binding['id']] = ['file_id' => $canonical->getId(), 'path' => $canonical->getPath(),
                'folder' => $canonical instanceof Folder, 'internal_path' => trim($canonical->getInternalPath(), '/'),
                'storage_id' => $canonical->getMountPoint()?->getNumericStorageId()];
        }
        $this->pendingDeletes[$key] = $facts;
    }

    /** Match physical filecache IDs through the publisher's actual mount view. */
    private function canonicalNode(array $binding, Node $node, ?int $knownId = null): ?Node {
        $fileId = $knownId ?? $this->nodeId($node);
        if ($fileId === null) { return null; }
        $view = $this->rootFolder->getUserFolder($binding['owner_uid']);
        foreach ($view->getById($binding['root_file_id']) as $root) {
            if (!$root instanceof Folder || !$root->isReadable() || !$view->isSubNode($root)) { continue; }
            if ($root->getId() === $fileId) { return $root; }
            foreach ($root->getById($fileId) as $current) {
                if ($current instanceof Node && $current->getId() === $fileId && $root->isSubNode($current) && $current->isReadable()) {
                    return $current;
                }
            }
        }
        return null;
    }

    private function recordCapturedDelete(array $binding, array $captured): void {
        // A failed owner-view lookup is an unknown source, not evidence of
        // physical deletion. Successful post-delete + a usable files view is
        // required before withdrawing the pre-captured physical identity.
        $view = $this->rootFolder->getUserFolder($binding['owner_uid']);
        if (!$view->isReadable()) { $this->outbox->append($binding['id'], null, 'reconcile'); return; }
        if ($this->db === null || !is_int($captured['storage_id'])) {
            $this->outbox->append($binding['id'], null, 'reconcile'); return;
        }
        $q = $this->db->getQueryBuilder();
        $r = $q->select('storage', 'path')->from('filecache')
            ->where($q->expr()->eq('fileid', $q->createNamedParameter($captured['file_id'])))->executeQuery();
        try { $stored = $r->fetchAssociative(); } finally { $r->closeCursor(); }
        if ($stored !== false && (int)$stored['storage'] === $captured['storage_id'] &&
            trim((string)$stored['path'], '/') === $captured['internal_path']) {
            $this->outbox->append($binding['id'], null, 'reconcile'); return;
        }
        $this->outbox->append($binding['id'], $captured['file_id'], $captured['folder'] ? 'subtree_deleted' : 'delete', $captured['path']);
    }

    private function logFailure(Event $event, \Throwable $exception): void {
        // An outbox/database outage must not turn a completed user file
        // operation into an apparent failure. Full reconciliation repairs
        // the missing hint; never fabricate a deletion on failure.
        $this->logger->error('WeKnora change hint could not be recorded', [
            'event' => get_class($event),
            'error_type' => get_class($exception),
        ]);
    }

    private function captureRenameIdentity(BeforeNodeRenamedEvent $event): void {
        $oldPath = $this->nodePath($event->getSource());
        $newPath = $this->nodePath($event->getTarget());
        $sourceId = $this->nodeId($event->getSource());
        if ($oldPath !== null && $newPath !== null && $sourceId !== null) {
            $this->pendingRenameSourceIds[$oldPath . "\0" . $newPath] = $sourceId;
            $scopes = [];
            foreach ($this->bindings->listBindings() as $binding) {
                $source = $this->canonicalNode($binding, $event->getSource());
                if ($source !== null) { $scopes[$binding['id']] = ['path' => $source->getPath(), 'file_id' => $source->getId()]; }
            }
            $this->pendingRenameScopes[$oldPath . "\0" . $newPath] = $scopes;
        }
    }

    private function renameKey(Node $source, Node $target): ?string {
        $old = $this->nodePath($source); $new = $this->nodePath($target);
        return $old === null || $new === null ? null : $old . "\0" . $new;
    }

    private function takeRenameIdentity(NodeRenamedEvent $event): ?int {
        $oldPath = $this->nodePath($event->getSource());
        $newPath = $this->nodePath($event->getTarget());
        if ($oldPath === null || $newPath === null) {
            return null;
        }
        $key = $oldPath . "\0" . $newPath;
        $id = $this->pendingRenameSourceIds[$key] ?? null;
        unset($this->pendingRenameSourceIds[$key]);
        return $id;
    }

    /** @param array{owner_uid: string, root_file_id: int} $binding */
    private function bindingRootPath(array $binding): ?string {
        try {
            $userFolder = $this->rootFolder->getUserFolder($binding['owner_uid']);
            foreach ($userFolder->getById($binding['root_file_id']) as $node) {
                if ($node instanceof Folder && $node->getId() === $binding['root_file_id'] &&
                    $userFolder->isSubNode($node)) {
                    return rtrim($node->getPath(), '/');
                }
            }
        } catch (\Throwable $exception) {
            // A missing mount/root is an unknown source state, not a deletion.
        }
        return null;
    }

    private function recordCurrent(string $bindingId, int $rootId, ?string $rootPath, Node $node): void {
        $path = $this->nodePath($node);
        $fileId = $this->nodeId($node);
        if (!$this->inScope($rootId, $rootPath, $path, $fileId)) {
            return;
        }
        if ($this->isFolder($node)) {
            $this->outbox->append($bindingId, $fileId, 'subtree_scan', null, $path);
        } elseif ($node instanceof File && $fileId !== null) {
            $this->outbox->append($bindingId, $fileId, 'upsert', null, $path,
                $this->nodeEtag($node), $this->relativePath($rootPath, $path));
        } else {
            $this->outbox->append($bindingId, $fileId, 'reconcile', null, $path);
        }
    }

    private function recordDelete(string $bindingId, int $rootId, ?string $rootPath, Node $node): void {
        $oldPath = $this->nodePath($node);
        $fileId = $this->nodeId($node);
        if (!$this->inScope($rootId, $rootPath, $oldPath, $fileId)) {
            return;
        }
        if ($this->isFolder($node)) {
            // A folder post-delete does not enumerate descendants. The old
            // path is a subtree withdrawal hint for the consumer to reconcile.
            $type = 'subtree_deleted';
        } else {
            // A file-ID tombstone is emitted only when the post-delete event
            // retained a reliable source ID. 404 and pre-delete never qualify.
            $type = $fileId === null ? 'reconcile' : 'delete';
        }
        $this->outbox->append($bindingId, $fileId, $type, $oldPath);
    }

    private function recordRename(
        string $bindingId,
        int $rootId,
        ?string $rootPath,
        NodeRenamedEvent $event,
        ?int $capturedSourceId,
        ?array $capturedScope = null,
        ?Node $canonicalTarget = null,
    ): void {
        $source = $event->getSource();
        $target = $event->getTarget();
        $oldPath = $capturedScope['path'] ?? $this->nodePath($source);
        $newPath = $canonicalTarget === null ? $this->nodePath($target) : $this->nodePath($canonicalTarget);
        $sourceId = $capturedScope['file_id'] ?? $this->nodeId($source) ?? $capturedSourceId;
        $targetId = $canonicalTarget === null ? $this->nodeId($target) : $this->nodeId($canonicalTarget);
        $wasInside = $capturedScope !== null || $this->inScope($rootId, $rootPath, $oldPath, $sourceId);
        $isInside = $canonicalTarget !== null || $this->inScope($rootId, $rootPath, $newPath, $targetId);
        if (!$wasInside && !$isInside) {
            return;
        }
        if ($this->isFolder($target) || $this->isFolder($source)) {
            $type = $wasInside && $isInside ? 'subtree_moved' :
                ($wasInside ? 'subtree_deleted' : 'subtree_scan');
            $this->outbox->append($bindingId, $sourceId ?? $targetId, $type, $oldPath, $newPath);
        } elseif ($wasInside && $isInside) {
            $this->outbox->append($bindingId, $targetId, $targetId === null ? 'reconcile' : 'metadata',
                $oldPath, $newPath, $this->nodeEtag($canonicalTarget ?? $target), $this->relativePath($rootPath, $newPath));
        } elseif ($isInside) {
            $this->outbox->append($bindingId, $targetId, $targetId === null ? 'reconcile' : 'upsert',
                $oldPath, $newPath, $this->nodeEtag($canonicalTarget ?? $target), $this->relativePath($rootPath, $newPath));
        } else {
            // The source ID captured before the operation is safe to withdraw
            // only now that its post-rename event confirms success. If an
            // adapter never sent the pre-event, request reconciliation.
            $this->outbox->append($bindingId, $sourceId,
                $sourceId === null ? 'reconcile' : 'delete', $oldPath, $newPath);
        }
    }

    private function inScope(int $rootId, ?string $rootPath, ?string $path, ?int $fileId): bool {
        if ($fileId === $rootId) {
            return true;
        }
        return $rootPath !== null && $path !== null &&
            ($path === $rootPath || str_starts_with($path, $rootPath . '/'));
    }

    private function relativePath(?string $rootPath, ?string $path): ?string {
        if ($rootPath === null || $path === null || !str_starts_with($path, $rootPath . '/')) {
            return null;
        }
        $relative = substr($path, strlen($rootPath) + 1);
        if ($relative === '' || strlen($relative) > 4096 || str_contains($relative, '\\') ||
            str_contains($relative, "\r") || str_contains($relative, "\n")) {
            return null;
        }
        foreach (explode('/', $relative) as $part) {
            if ($part === '' || $part === '.' || $part === '..') {
                return null;
            }
        }
        return $relative;
    }

    private function nodePath(Node $node): ?string {
        try {
            return $node->getPath();
        } catch (\Throwable $exception) {
            return null;
        }
    }

    private function nodeId(Node $node): ?int {
        try {
            $id = $node->getId();
            return $id > 0 ? $id : null;
        } catch (\Throwable $exception) {
            return null;
        }
    }

    private function nodeEtag(Node $node): ?string {
        try {
            $etag = $node->getEtag();
            return strlen($etag) <= 255 ? $etag : null;
        } catch (\Throwable $exception) {
            return null;
        }
    }

    private function isFolder(Node $node): bool {
        if ($node instanceof Folder) {
            return true;
        }
        try {
            // NC 34's post-delete hook can wrap a deleted directory in a
            // NonExistingFile because the path no longer exists. Its cached
            // FileInfo still preserves the original type.
            return $node->getType() === FileInfo::TYPE_FOLDER;
        } catch (\Throwable $exception) {
            return false;
        }
    }
}
