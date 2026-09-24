<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\BackgroundJob;

use OCA\IntegrationWeknora\Service\EventDeliveryService;
use OCP\AppFramework\Utility\ITimeFactory;
use OCP\BackgroundJob\TimedJob;

/** Poll due sender checkpoints; database row locks serialize all workers. */
final class EventDeliveryJob extends TimedJob {
    public function __construct(
        ITimeFactory $time,
        private EventDeliveryService $delivery,
    ) {
        parent::__construct($time);
        $this->setInterval(60);
        $this->setAllowParallelRuns(false);
    }

    protected function run($argument): void {
        $this->delivery->deliverDue($this->time->getTime());
    }
}
