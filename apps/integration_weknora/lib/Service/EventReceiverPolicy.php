<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

require_once __DIR__ . '/ApprovedOriginPolicy.php';

/** Egress is disabled until an operator approves exact origins in the runtime. */
final class EventReceiverPolicy {
    public const PATH = '/api/v1/integrations/nextcloud/events';
    public const STATUS_PATH = '/api/v1/integrations/nextcloud/events/status';

    /** @return array{origin: string, allow_local: bool} */
    public function requireApproved(string $url): array {
        if (strlen($url) > 512 || preg_match('/[\x00-\x20\x7f]/', $url)) {
            throw new \InvalidArgumentException('Invalid receiver URL');
        }
        $parts = parse_url($url);
        if (!is_array($parts) || isset($parts['user']) || isset($parts['pass']) ||
            isset($parts['query']) || isset($parts['fragment']) ||
            !isset($parts['scheme'], $parts['host'], $parts['path']) ||
            $parts['path'] !== self::PATH) {
            throw new \InvalidArgumentException('Invalid receiver URL');
        }
        $origin = $parts['scheme'] . '://' . $parts['host'] .
            (isset($parts['port']) ? ':' . $parts['port'] : '');
        try {
            $origin = (new ApprovedOriginPolicy())->canonical(strtolower($origin), true);
        } catch (\DomainException $exception) {
            throw new \InvalidArgumentException('Receiver origin is not operator approved', 0, $exception);
        }
        if ($url !== $origin . self::PATH) {
            throw new \InvalidArgumentException('Receiver URL must be canonical');
        }
        // A fixed, operator-approved enterprise HTTPS host may resolve to a
        // private address. The exact-origin check and TLS verification still
        // apply. Local HTTP additionally requires the development switch.
        return ['origin' => $origin, 'allow_local' => true];
    }
}
