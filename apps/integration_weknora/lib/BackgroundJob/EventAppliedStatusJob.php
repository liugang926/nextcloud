<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\BackgroundJob;

use OCA\IntegrationWeknora\Service\EventAppliedStatusService;
use OCP\AppFramework\Utility\ITimeFactory;
use OCP\BackgroundJob\TimedJob;

/** Poll signed applied watermarks independently of sender delivery. */
final class EventAppliedStatusJob extends TimedJob {
    public function __construct(
        ITimeFactory $time,
        private EventAppliedStatusService $appliedStatus,
    ) {
        parent::__construct($time);
        $this->setInterval(60);
        $this->setAllowParallelRuns(false);
    }

    protected function run($argument): void {
        $this->appliedStatus->pollDue($this->time->getTime());
    }
}
