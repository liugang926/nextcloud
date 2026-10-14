<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\BackgroundJob;
use OCA\IntegrationWeknora\Service\DirectoryDiscoveryService;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCP\AppFramework\Utility\ITimeFactory;
use OCP\BackgroundJob\TimedJob;
final class DirectoryDiscoveryJob extends TimedJob {
    public function __construct(ITimeFactory $time, private DirectoryDiscoveryService $discovery, private DirectoryPublicationService $directories) {
        parent::__construct($time); $this->setInterval(5); $this->setAllowParallelRuns(false);
    }
    protected function run($argument): void { $this->discovery->deliverDue(); $this->directories->processDue(); }
}
