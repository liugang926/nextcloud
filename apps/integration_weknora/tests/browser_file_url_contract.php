<?php

declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/BrowserFileUrl.php';

use OCA\IntegrationWeknora\Service\BrowserFileUrl;

function checkUrl(?string $actual, ?string $expected): void {
    if ($actual !== $expected) {
        throw new RuntimeException('Unexpected browser Files URL: ' . (string)$actual);
    }
}

checkUrl(BrowserFileUrl::fromConfiguredBase('/index.php/f/77', 'http://10.106.105.128:18082'),
    'http://10.106.105.128:18082/index.php/f/77');
checkUrl(BrowserFileUrl::fromConfiguredBase('/index.php/f/77', 'http://[::1]:18082'),
    'http://[::1]:18082/index.php/f/77');
checkUrl(BrowserFileUrl::fromConfiguredBase('/index.php/f/77', 'https://cloud.example.test/nc'),
    'https://cloud.example.test/nc/index.php/f/77');
checkUrl(BrowserFileUrl::fromConfiguredBase('/nc/index.php/f/77', 'https://cloud.example.test/nc'),
    'https://cloud.example.test/nc/index.php/f/77');
checkUrl(BrowserFileUrl::fromConfiguredBase('/index.php/f/77', 'https://user@cloud.example.test'), null);
checkUrl(BrowserFileUrl::fromConfiguredBase('/index.php/f/77', 'https://cloud.example.test/?x=1'), null);

echo "browser Files URL contract passed\n";
