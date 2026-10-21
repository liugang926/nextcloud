<?php
declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

/** A rejected remote request with one allow-listed machine code. */
final class DirectoryOnboardingRemoteRejectedException extends \DomainException {
    public function __construct(int $status, private ?string $remoteCode = null) {
        if ($status < 400 || $status > 499 ||
            ($remoteCode !== null && !preg_match('/\A[a-z][a-z0-9_]{0,63}\z/D', $remoteCode))) {
            throw new \InvalidArgumentException('Invalid onboarding rejection');
        }
        parent::__construct('Directory onboarding request was rejected', $status);
    }

    public function remoteCode(): ?string {
        return $this->remoteCode;
    }
}
