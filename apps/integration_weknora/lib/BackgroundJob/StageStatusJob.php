<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\BackgroundJob;

use OCA\IntegrationWeknora\Service\RemoteStageStatusService;
use OCP\AppFramework\Utility\ITimeFactory;
use OCP\BackgroundJob\TimedJob;

final class StageStatusJob extends TimedJob {
    public function __construct(ITimeFactory $time, private RemoteStageStatusService $statuses) {
        parent::__construct($time);
        $this->setInterval(60);
        $this->setAllowParallelRuns(false);
    }

    protected function run($argument): void { $this->statuses->pollDue($this->time->getTime()); }
}
