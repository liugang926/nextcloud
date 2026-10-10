<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\AppInfo;

use OCA\IntegrationWeknora\Listener\FileChangeListener;
use OCA\IntegrationWeknora\Listener\LoadEmployeeSidebarListener;
use OCA\Files\Event\LoadSidebar;
use OCP\AppFramework\App;
use OCP\AppFramework\Bootstrap\IBootContext;
use OCP\AppFramework\Bootstrap\IBootstrap;
use OCP\AppFramework\Bootstrap\IRegistrationContext;
use OCP\Files\Events\Node\BeforeNodeRenamedEvent;
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
        // Only successful post-operation events can produce deletion hints.
        foreach ([
            BeforeNodeRenamedEvent::class,
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
