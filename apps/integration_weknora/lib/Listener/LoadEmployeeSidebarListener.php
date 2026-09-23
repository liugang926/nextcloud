<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Listener;

use OCA\Files\Event\LoadSidebar;
use OCP\EventDispatcher\Event;
use OCP\EventDispatcher\IEventListener;
use OCP\Util;

/** @template-implements IEventListener<LoadSidebar> */
final class LoadEmployeeSidebarListener implements IEventListener {
    public function handle(Event $event): void {
        if (!$event instanceof LoadSidebar) {
            return;
        }

        Util::addStyle('integration_weknora', 'weknora-sidebar');
        Util::addInitScript('integration_weknora', 'weknora-sidebar');
    }
}
