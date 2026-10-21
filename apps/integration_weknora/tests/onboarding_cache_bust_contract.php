<?php

declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/DirectoryOnboardingClient.php';

use OCA\IntegrationWeknora\Service\DirectoryOnboardingClient;

function cacheBustCheck(bool $condition, string $message): void {
    if (!$condition) {
        fwrite(STDERR, "FAIL: {$message}\n");
        exit(1);
    }
}

$method = new ReflectionMethod(DirectoryOnboardingClient::class, 'cacheBustedGetUrl');
$base = 'https://weknora.example.test/api/v1/integrations/nextcloud/onboarding/operations/operation';
$first = $method->invoke(null, $base, [
    'actor_directory_id' => 'directory',
    'actor_object_guid' => 'guid',
]);
$second = $method->invoke(null, $base, [
    'actor_directory_id' => 'directory',
    'actor_object_guid' => 'guid',
]);

parse_str((string)parse_url($first, PHP_URL_QUERY), $firstQuery);
parse_str((string)parse_url($second, PHP_URL_QUERY), $secondQuery);
cacheBustCheck(str_starts_with($first, $base . '?'), 'GET URL must retain the approved endpoint');
cacheBustCheck(($firstQuery['actor_directory_id'] ?? null) === 'directory',
    'GET URL must retain the scoped actor directory');
cacheBustCheck(($firstQuery['actor_object_guid'] ?? null) === 'guid',
    'GET URL must retain the scoped actor object');
cacheBustCheck(is_string($firstQuery['_nc_cache_bust'] ?? null) &&
    preg_match('/\A[a-f0-9]{24}\z/D', $firstQuery['_nc_cache_bust']) === 1,
    'GET URL must contain a bounded non-sensitive cache-bust value');
cacheBustCheck($firstQuery['_nc_cache_bust'] !== $secondQuery['_nc_cache_bust'],
    'Each GET must use a distinct cache key');

fwrite(STDOUT, "onboarding cache-bust contract: PASS\n");
