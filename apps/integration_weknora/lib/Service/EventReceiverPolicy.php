<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

/** Egress is disabled until an operator approves exact origins in the runtime. */
final class EventReceiverPolicy {
    public const PATH = '/api/v1/integrations/nextcloud/events';

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
        $scheme = $parts['scheme'];
        $host = $parts['host'];
        if (($scheme !== 'https' && $scheme !== 'http') ||
            !is_string($host) || strlen($host) > 253 ||
            !preg_match('/\A[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\z/D', $host) ||
            filter_var($host, FILTER_VALIDATE_IP) !== false ||
            str_contains($host, '..') ||
            (isset($parts['port']) && ($parts['port'] < 1 || $parts['port'] > 65535))) {
            throw new \InvalidArgumentException('Invalid receiver URL');
        }
        $origin = $scheme . '://' . $host . (isset($parts['port']) ? ':' . $parts['port'] : '');
        if ($url !== $origin . self::PATH) {
            throw new \InvalidArgumentException('Receiver URL must be canonical');
        }
        $devHttp = getenv('WEKNORA_EVENT_DEV_HTTP') === '1';
        if ($scheme === 'http' && !$devHttp) {
            throw new \InvalidArgumentException('HTTP receiver requires explicit development switch');
        }
        $approved = getenv('WEKNORA_EVENT_ALLOWED_ORIGINS');
        $origins = is_string($approved) ? explode(',', $approved) : [];
        if (!in_array($origin, $origins, true)) {
            throw new \InvalidArgumentException('Receiver origin is not operator approved');
        }
        // A fixed, operator-approved enterprise HTTPS host may resolve to a
        // private address. The exact-origin check and TLS verification still
        // apply. Local HTTP additionally requires the development switch.
        return ['origin' => $origin, 'allow_local' => true];
    }
}
