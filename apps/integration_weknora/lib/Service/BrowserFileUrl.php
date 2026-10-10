<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

/** Builds a Files URL from an administrator-configured browser origin. */
final class BrowserFileUrl {
    public static function fromConfiguredBase(string $route, string $publicUrl): ?string {
        $parts = parse_url($publicUrl);
        if (!is_array($parts) || !in_array($parts['scheme'] ?? '', ['http', 'https'], true) ||
            !isset($parts['host']) || $parts['host'] === '' ||
            isset($parts['user']) || isset($parts['pass']) ||
            isset($parts['query']) || isset($parts['fragment'])) {
            return null;
        }
        // PHP's parse_url already includes brackets around IPv6 literals.
        $host = $parts['host'];
        if (str_contains($host, ':') && !str_starts_with($host, '[')) {
            $host = '[' . $host . ']';
        }
        $port = isset($parts['port']) ? ':' . $parts['port'] : '';
        $path = '/' . ltrim($route, '/');
        $basePath = rtrim($parts['path'] ?? '', '/');
        if ($basePath !== '' && $path !== $basePath &&
            !str_starts_with($path, $basePath . '/')) {
            $path = $basePath . $path;
        }
        return $parts['scheme'] . '://' . $host . $port . $path;
    }
}
