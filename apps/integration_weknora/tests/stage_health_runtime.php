<?php
declare(strict_types=1);
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });
\OC_App::loadApp('user_ldap');
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\StageOperationalStatusService;
use OCA\IntegrationWeknora\Service\OperationalStatusService;
use OCA\IntegrationWeknora\Service\EventHealthEvaluator;
function healthCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
$container = (new Application())->getContainer();
$stage = $container->get(StageOperationalStatusService::class)->snapshot();
healthCheck(isset($stage['stored_status_count']) && !isset($stage['current_status_count']), 'Replica diagnostics claimed current user-visible counts');
foreach (['stored_status_count', 'stale_status_count', 'permission_unverified_count', 'graph_failure_count', 'directory_discovery_pending_count'] as $key) {
    healthCheck(is_int($stage[$key]) && $stage[$key] >= 0, 'Invalid stage operational counter: ' . $key);
}
$pastFreshness = $container->get(StageOperationalStatusService::class)->snapshot(time() + 600);
healthCheck($pastFreshness['stale_status_count'] === $pastFreshness['stored_status_count'], 'Aged replica successes remained fresh');
healthCheck(($pastFreshness['last_verified_graph_states']['succeeded'] ?? 0) === 0 &&
    ($pastFreshness['last_verified_publication_states']['published'] ?? 0) === 0, 'Historical successes counted as fresh processing');
$all = $container->get(OperationalStatusService::class)->snapshot();
healthCheck(isset($all['processing']['stored_status_count']), 'Admin diagnostics omitted processing replica health');
$alerts = EventHealthEvaluator::evaluate($all);
foreach ($alerts as $alert) {
    healthCheck(array_keys($alert) === ['binding_id', 'code'] && is_string($alert['code']), 'Health emitted file/user/secret details');
}
$forced = $all;
$forced['processing'] = array_replace($forced['processing'], ['stale_status_count' => 1,
    'permission_unverified_count' => 1, 'graph_failure_count' => 1,
    'oldest_pending_directory_age_seconds' => 301, 'oldest_directory_discovery_age_seconds' => 11]);
$codes = array_column(EventHealthEvaluator::evaluate($forced), 'code');
foreach (['processing_status_stale', 'processing_permission_unverified', 'graph_processing_failed', 'directory_discovery_overdue'] as $code) {
    healthCheck(in_array($code, $codes, true), 'Missing operational failure alert: ' . $code);
}
echo json_encode(['scope' => 'administrator_replica_diagnostics', 'stored_status_count' => $stage['stored_status_count'],
    'stale_status_count' => $stage['stale_status_count'], 'alert_count' => count($alerts), 'user_visible_current_counts' => false], JSON_THROW_ON_ERROR) . "\n";
echo "Stage replica aggregation, stale result exclusion, diagnostics integration and fixed-code health alerts passed\n";
