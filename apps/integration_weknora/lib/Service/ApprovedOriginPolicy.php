<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

/** Exact-origin egress policy; production self-remembered origins are not authority. */
final class ApprovedOriginPolicy {
    public function canonical(string $raw, bool $forConnection): string {
        $raw = trim($raw);
        if ($raw === '' || strlen($raw) > 512 || preg_match('/[\x00-\x20\x7f]/', $raw)) {
            throw new \InvalidArgumentException('Invalid WeKnora origin');
        }
        $parts = parse_url($raw);
        if (!is_array($parts) || isset($parts['user']) || isset($parts['pass']) ||
            isset($parts['query']) || isset($parts['fragment']) ||
            !isset($parts['scheme'], $parts['host']) ||
            (isset($parts['path']) && $parts['path'] !== '' && $parts['path'] !== '/')) {
            throw new \InvalidArgumentException('Invalid WeKnora origin');
        }
        $devHttp = getenv('WEKNORA_EVENT_DEV_HTTP') === '1';
        $scheme = strtolower((string)$parts['scheme']);
        if (($scheme !== 'https' && !($devHttp && $scheme === 'http')) ||
            !is_string($parts['host']) || strlen($parts['host']) > 253 ||
            str_contains($parts['host'], '..') ||
            (!filter_var($parts['host'], FILTER_VALIDATE_IP) &&
                !preg_match('/\A[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\z/D', strtolower($parts['host'])))) {
            throw new \InvalidArgumentException('Invalid WeKnora origin');
        }
        $origin = $scheme . '://' . strtolower($parts['host']);
        if (isset($parts['port'])) {
            if (!is_int($parts['port']) || $parts['port'] < 1 || $parts['port'] > 65535) {
                throw new \InvalidArgumentException('Invalid WeKnora origin');
            }
            $origin .= ':' . $parts['port'];
        }
        if ($forConnection && !in_array($origin, $this->allowList(), true)) {
            throw new \DomainException('The WeKnora origin is not operator approved');
        }
        return $origin;
    }

    /** @return list<string> */
    public function allowList(): array {
        $raw = getenv('WEKNORA_CONNECTION_ALLOWED_ORIGINS');
        if (!is_string($raw) || trim($raw) === '') {
            $raw = getenv('WEKNORA_EVENT_ALLOWED_ORIGINS');
        }
        $origins = [];
        foreach (is_string($raw) ? explode(',', $raw) : [] as $candidate) {
            $candidate = trim($candidate);
            if ($candidate === '') {
                continue;
            }
            try {
                $origins[] = $this->canonical($candidate, false);
            } catch (\Throwable) {
                // A malformed operator entry is ignored; it never broadens policy.
            }
        }
        return array_values(array_unique($origins));
    }

    public function isApproved(string $origin): bool {
        try {
            return in_array($this->canonical($origin, false), $this->allowList(), true);
        } catch (\Throwable) {
            return false;
        }
    }

    public function safeWebUrl(mixed $raw, string $apiOrigin): ?string {
        if (!is_string($raw) || $raw === '' || strlen($raw) > 2048 ||
            preg_match('/[\x00-\x20\x7f]/', $raw)) {
            return null;
        }
        $parts = parse_url($raw);
        $devHttp = getenv('WEKNORA_EVENT_DEV_HTTP') === '1';
        if (!is_array($parts) || isset($parts['user']) || isset($parts['pass']) ||
            !isset($parts['scheme'], $parts['host']) ||
            ($parts['scheme'] !== 'https' && !($devHttp && $parts['scheme'] === 'http')) ||
            filter_var($parts['host'], FILTER_VALIDATE_IP) !== false) {
            return null;
        }
        $webOrigin = strtolower($parts['scheme']) . '://' . strtolower($parts['host']);
        if (isset($parts['port'])) {
            $webOrigin .= ':' . (int)$parts['port'];
        }
        if ($webOrigin !== $apiOrigin && !$this->isApproved($webOrigin)) {
            return null;
        }
        return $raw;
    }
}
