<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\BackgroundJob;

use OCA\IntegrationWeknora\Service\ScopedCredentialStore;
use OCP\AppFramework\Utility\ITimeFactory;
use OCP\BackgroundJob\TimedJob;
use Psr\Log\LoggerInterface;

/** Encrypts legacy plaintext scoped credentials after an app upgrade. */
final class CredentialMigrationJob extends TimedJob {
    public function __construct(
        ITimeFactory $time,
        private ScopedCredentialStore $credentials,
        private LoggerInterface $logger,
    ) {
        parent::__construct($time);
        $this->setInterval(900);
    }

    protected function run($argument): void {
        try {
            $this->credentials->migrateLegacy();
        } catch (\Throwable $exception) {
            // Never log ciphertext or plaintext; retry on the next interval.
            $this->logger->error('WeKnora scoped credential migration failed', [
                'error_type' => get_class($exception),
            ]);
        }
    }
}
