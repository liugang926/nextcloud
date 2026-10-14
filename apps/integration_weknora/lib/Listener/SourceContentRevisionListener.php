<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Listener;

use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\SourceContentRevisionService;
use OCP\EventDispatcher\Event;
use OCP\EventDispatcher\IEventListener;
use OCP\Files\Events\Node\BeforeNodeWrittenEvent;
use OCP\Files\Events\Node\BeforeNodeCopiedEvent;
use OCP\Files\Events\Node\NodeCopiedEvent;
use OCP\Files\Events\Node\NodeCreatedEvent;
use OCP\Files\Events\Node\NodeWrittenEvent;
use OCP\Files\File;
use OCP\Files\Folder;
use OCP\Files\IRootFolder;

/** @implements IEventListener<Event> */
final class SourceContentRevisionListener implements IEventListener {
    public function __construct(private SourceContentRevisionService $revisions,
        private BindingRegistryService $bindings, private IRootFolder $rootFolder) {}

    public function handle(Event $event): void {
        $before = $event instanceof BeforeNodeWrittenEvent || $event instanceof BeforeNodeCopiedEvent;
        if ($event instanceof NodeCopiedEvent || $event instanceof BeforeNodeCopiedEvent) { $node = $event->getTarget(); }
        elseif ($event instanceof BeforeNodeWrittenEvent || $event instanceof NodeWrittenEvent || $event instanceof NodeCreatedEvent) { $node = $event->getNode(); }
        else { return; }
        if (!$node instanceof File || (int)$node->getId() < 1) { return; }
        $ids = [];
        foreach ($this->bindings->listBindings() as $binding) {
            try {
                $view = $this->rootFolder->getUserFolder($binding['owner_uid']);
                foreach ($view->getById($binding['root_file_id']) as $root) {
                    if (!$root instanceof Folder) { continue; }
                    foreach ($root->getById((int)$node->getId()) as $ownerFile) {
                        if ($ownerFile instanceof File && $root->isSubNode($ownerFile)) { $ids[] = $binding['id']; break 2; }
                    }
                }
            } catch (\Throwable $e) { /* This file is still fenced by its global physical ID. */ }
        }
        if ($before) { $this->revisions->begin((int)$node->getId(), $ids); }
        else { $this->revisions->complete((int)$node->getId(), $ids); }
    }
}
