<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\BackgroundJob;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCP\AppFramework\Utility\ITimeFactory;
use OCP\BackgroundJob\TimedJob;
use Psr\Log\LoggerInterface;
final class DirectoryPublicationJob extends TimedJob {
    public function __construct(ITimeFactory $time, private DirectoryPublicationService $directories, private LoggerInterface $logger) {
        parent::__construct($time); $this->setInterval(60); $this->setAllowParallelRuns(false);
    }
    protected function run($argument): void {
        try { $this->directories->reconcile(); }
        catch (\Throwable $e) { $this->logger->error('Directory publication reconciliation is unavailable', ['error_type' => get_class($e)]); }
    }
}
