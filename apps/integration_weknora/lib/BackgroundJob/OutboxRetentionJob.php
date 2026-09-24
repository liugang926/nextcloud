<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\BackgroundJob;

use OCA\IntegrationWeknora\Service\ChangeOutboxService;
use OCP\AppFramework\Utility\ITimeFactory;
use OCP\BackgroundJob\IJob;
use OCP\BackgroundJob\TimedJob;

/** Daily, transactionally checkpointed cleanup of old change hints. */
final class OutboxRetentionJob extends TimedJob {
    public function __construct(
        ITimeFactory $time,
        private ChangeOutboxService $outbox,
    ) {
        parent::__construct($time);
        $this->setInterval(86400);
        $this->setTimeSensitivity(IJob::TIME_INSENSITIVE);
        $this->setAllowParallelRuns(false);
    }

    protected function run($argument): void {
        $this->outbox->pruneExpired($this->time->getTime());
    }
}
