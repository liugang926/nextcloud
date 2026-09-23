<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IConfig;
use OCP\IRequest;

/** The existing connector credential is the trust boundary for machine APIs. */
final class PairedServiceToken {
    public function __construct(private IConfig $config) {
    }

    public function verify(IRequest $request): bool {
        $expectedHash = $this->config->getAppValue('integration_weknora', 'service_token_sha256', '');
        if (!is_string($expectedHash) || !preg_match('/\A[a-fA-F0-9]{64}\z/D', $expectedHash)) {
            return false;
        }
        $authorization = $request->getHeader('Authorization');
        if (!is_string($authorization) ||
            !preg_match('/\ABearer[ \t]+([^\s]+)\z/iD', $authorization, $matches)) {
            return false;
        }
        return hash_equals(strtolower($expectedHash), hash('sha256', $matches[1]));
    }
}
