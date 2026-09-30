<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\BackgroundJob;

use OCA\IntegrationWeknora\Service\EventHealthMonitor;
use OCP\AppFramework\Utility\ITimeFactory;
use OCP\BackgroundJob\TimedJob;

/** Alert on source-side sender and applied-status failures once per minute. */
final class EventHealthJob extends TimedJob {
    public function __construct(
        ITimeFactory $time,
        private EventHealthMonitor $monitor,
    ) {
        parent::__construct($time);
        $this->setInterval(60);
        $this->setAllowParallelRuns(false);
    }

    protected function run($argument): void {
        $this->monitor->check($this->time->getTime());
    }
}
