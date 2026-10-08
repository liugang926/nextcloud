<?php

declare(strict_types=1);

// occ intentionally suppresses third-party commands in maintenance. This
// dedicated CLI loads only the closure command; maintenance stays enabled.
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
define('OC_CONSOLE', 1);
$root = dirname(__DIR__, 3);
require_once $root . '/lib/base.php';
if (!\OC::$CLI || !function_exists('posix_geteuid') ||
    posix_geteuid() !== fileowner(\OC::$configDir . 'config.php')) {
    fwrite(STDERR, "Recovery must run as the Nextcloud config owner\n"); exit(1);
}
$config = \OCP\Server::get(\OCP\IConfig::class);
if (!$config->getSystemValueBool('maintenance', false)) {
    fwrite(STDERR, "Recovery requires maintenance mode\n"); exit(1);
}
// Loading this namespace does not register listeners, jobs or public routes.
spl_autoload_register(static function (string $class): void {
    $prefix = 'OCA\\IntegrationWeknora\\';
    if (str_starts_with($class, $prefix)) {
        $path = dirname(__DIR__) . '/lib/' . str_replace('\\', '/', substr($class, strlen($prefix))) . '.php';
        if (is_file($path)) { require_once $path; }
    }
});
try {
    $app = new \Symfony\Component\Console\Application('WeKnora closed recovery');
    $app->add(\OCP\Server::get(\OCA\IntegrationWeknora\Command\ReplayRecoveryLedger::class));
    $app->setDefaultCommand('integration_weknora:replay-recovery-ledger', true);
    exit($app->run());
} catch (\Throwable $error) {
    fwrite(STDERR, "Closed recovery failed; inspect private operator evidence\n"); exit(1);
}
