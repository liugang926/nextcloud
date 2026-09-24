<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Listener;

use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\ChangeOutboxService;
use OCP\EventDispatcher\Event;
use OCP\EventDispatcher\IEventListener;
use OCP\Files\Events\Node\BeforeNodeRenamedEvent;
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
use Psr\Log\LoggerInterface;

/** @implements IEventListener<Event> */
final class FileChangeListener implements IEventListener {
    /** @var array<string, int> Captured before rename; never enqueued before success. */
    private array $pendingRenameSourceIds = [];

    public function __construct(
        private BindingRegistryService $bindings,
        private IRootFolder $rootFolder,
        private ChangeOutboxService $outbox,
        private LoggerInterface $logger,
    ) {
    }

    public function handle(Event $event): void {
        try {
            if ($event instanceof BeforeNodeRenamedEvent) {
                $this->captureRenameIdentity($event);
                return;
            }
            $bindings = $this->bindings->listBindings();
        } catch (\Throwable $exception) {
            $this->logFailure($event, $exception);
            return;
        }

        $capturedSourceId = $event instanceof NodeRenamedEvent ?
            $this->takeRenameIdentity($event) : null;
        foreach ($bindings as $binding) {
            try {
                $rootPath = $this->bindingRootPath($binding);
                $rootId = $binding['root_file_id'];
                $bindingId = $binding['id'];

                if ($event instanceof NodeRenamedEvent) {
                    $this->recordRename($bindingId, $rootId, $rootPath, $event, $capturedSourceId);
                } elseif ($event instanceof NodeDeletedEvent) {
                    $this->recordDelete($bindingId, $rootId, $rootPath, $event->getNode());
                } elseif ($event instanceof NodeCopiedEvent) {
                    $this->recordCurrent($bindingId, $rootId, $rootPath, $event->getTarget());
                } elseif ($event instanceof NodeCreatedEvent || $event instanceof NodeWrittenEvent ||
                    $event instanceof NodeTouchedEvent) {
                    $this->recordCurrent($bindingId, $rootId, $rootPath, $event->getNode());
                }
            } catch (\Throwable $exception) {
                $this->logFailure($event, $exception);
            }
        }
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
        }
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
            $this->outbox->append($bindingId, $fileId, 'upsert', null, $path, $this->nodeEtag($node));
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
    ): void {
        $source = $event->getSource();
        $target = $event->getTarget();
        $oldPath = $this->nodePath($source);
        $newPath = $this->nodePath($target);
        $sourceId = $this->nodeId($source) ?? $capturedSourceId;
        $targetId = $this->nodeId($target);
        $wasInside = $this->inScope($rootId, $rootPath, $oldPath, $sourceId);
        $isInside = $this->inScope($rootId, $rootPath, $newPath, $targetId);
        if (!$wasInside && !$isInside) {
            return;
        }
        if ($this->isFolder($target) || $this->isFolder($source)) {
            $type = $wasInside && $isInside ? 'subtree_moved' :
                ($wasInside ? 'subtree_deleted' : 'subtree_scan');
            $this->outbox->append($bindingId, $sourceId ?? $targetId, $type, $oldPath, $newPath);
        } elseif ($wasInside && $isInside) {
            $this->outbox->append($bindingId, $targetId, $targetId === null ? 'reconcile' : 'metadata',
                $oldPath, $newPath, $this->nodeEtag($target));
        } elseif ($isInside) {
            $this->outbox->append($bindingId, $targetId, $targetId === null ? 'reconcile' : 'upsert',
                $oldPath, $newPath, $this->nodeEtag($target));
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
