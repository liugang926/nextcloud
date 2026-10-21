<?php
declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

/** A stable, non-sensitive classification for a failed onboarding request. */
final class DirectoryOnboardingRequestException extends \RuntimeException {
    public const TRANSPORT = 'transport';
    public const INVALID_RESPONSE = 'invalid_response';
    public const SERVER_ERROR = 'server_error';
    public const RATE_LIMITED = 'rate_limited';
    public const HTTP_ERROR = 'http_error';
    public const CREDENTIAL_UNAVAILABLE = 'credential_unavailable';

    public function __construct(private string $reason) {
        if (!in_array($reason, [
            self::TRANSPORT,
            self::INVALID_RESPONSE,
            self::SERVER_ERROR,
            self::RATE_LIMITED,
            self::HTTP_ERROR,
            self::CREDENTIAL_UNAVAILABLE,
        ], true)) {
            throw new \InvalidArgumentException('Invalid onboarding failure classification');
        }
        parent::__construct('Directory onboarding request failed');
    }

    public function reason(): string {
        return $this->reason;
    }
}
