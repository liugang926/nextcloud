<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\AppInfo;

use OCA\IntegrationWeknora\Listener\FileChangeListener;
use OCA\IntegrationWeknora\Listener\DirectoryDiscoveryListener;
use OCA\IntegrationWeknora\Listener\SourceContentRevisionListener;
use OCA\IntegrationWeknora\Listener\LoadEmployeeSidebarListener;
use OCA\Files\Event\LoadSidebar;
use OCP\AppFramework\App;
use OCP\AppFramework\Bootstrap\IBootContext;
use OCP\AppFramework\Bootstrap\IBootstrap;
use OCP\AppFramework\Bootstrap\IRegistrationContext;
use OCP\Files\Events\Node\BeforeNodeRenamedEvent;
use OCP\Files\Events\Node\BeforeNodeDeletedEvent;
use OCP\Files\Events\Node\BeforeNodeWrittenEvent;
use OCP\Files\Events\Node\BeforeNodeCopiedEvent;
use OCP\Files\Events\Node\NodeCopiedEvent;
use OCP\Files\Events\Node\NodeCreatedEvent;
use OCP\Files\Events\Node\NodeDeletedEvent;
use OCP\Files\Events\Node\NodeRenamedEvent;
use OCP\Files\Events\Node\NodeTouchedEvent;
use OCP\Files\Events\Node\NodeWrittenEvent;

final class Application extends App implements IBootstrap {
    public const APP_ID = 'integration_weknora';

    public function __construct() {
        parent::__construct(self::APP_ID);
    }

    public function register(IRegistrationContext $context): void {
        $context->registerEventListener(LoadSidebar::class, LoadEmployeeSidebarListener::class);
        foreach ([BeforeNodeWrittenEvent::class, BeforeNodeCopiedEvent::class,
            NodeCreatedEvent::class, NodeCopiedEvent::class, NodeWrittenEvent::class] as $event) {
            $context->registerEventListener($event, SourceContentRevisionListener::class);
        }
        foreach ([NodeCreatedEvent::class, NodeCopiedEvent::class, NodeRenamedEvent::class] as $event) {
            $context->registerEventListener($event, DirectoryDiscoveryListener::class);
        }
        // Only successful post-operation events can produce deletion hints.
        foreach ([
            BeforeNodeRenamedEvent::class,
            BeforeNodeDeletedEvent::class,
            NodeCreatedEvent::class,
            NodeCopiedEvent::class,
            NodeWrittenEvent::class,
            NodeTouchedEvent::class,
            NodeRenamedEvent::class,
            NodeDeletedEvent::class,
        ] as $event) {
            $context->registerEventListener($event, FileChangeListener::class);
        }
    }

    public function boot(IBootContext $context): void {
    }
}
