<?php
declare(strict_types=1);
// Real NC/PostgreSQL fault replay using an actual previously committed closure
// and the existing collector's independently authenticated private plan.
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1); require '/var/www/html/lib/base.php'; \OC_App::loadApp('user_ldap');
set_exception_handler(static function(Throwable $e): void { fwrite(STDERR, $e->getMessage() . "\n"); exit(1); });
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\DirectoryTargetClosureService as Closures;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\DirectoryTenantDeletionService;
use OCA\IntegrationWeknora\Service\TenantDeletionProtocol;
use OCA\IntegrationWeknora\Service\TargetDeletionProtocol;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\ControlRecoveryService;
use OCA\IntegrationWeknora\Service\PublicationRecoveryReplay;
use OCA\IntegrationWeknora\Service\FilePublicationStateService;
function closedReplayCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
function closedReplayDeny(callable $fn): void { try { $fn(); } catch (DomainException|\OCA\IntegrationWeknora\Service\BindingPublicationStoppedException $e) { return; } throw new RuntimeException('Restored target reopened'); }
$descriptor = $argv[1] ?? ''; $planPath = $argv[2] ?? '';
foreach ([$descriptor, $planPath] as $path) { if (!is_file($path) || (fileperms($path) & 0o077)) { throw new RuntimeException('Actual private descriptor/verified external plan required'); } }
$f = json_decode(file_get_contents($descriptor), true, 16, JSON_THROW_ON_ERROR);
$envelope = json_decode(file_get_contents($planPath), true, 64, JSON_THROW_ON_ERROR); $keyPath = $argv[3] ?? '';
if (!is_array($envelope) || array_keys($envelope) !== ['plan', 'hmac_sha256'] || !is_string($envelope['plan']) || !is_string($envelope['hmac_sha256']) ||
    !is_file($keyPath) || is_link($keyPath) || (fileperms($keyPath) & 0o077)) { throw new RuntimeException('Actual independently signed plan and private external journal key required'); }
$key = file_get_contents($keyPath);
if (strlen($key) !== 32 || !hash_equals(hash_hmac('sha256', $envelope['plan'], $key), $envelope['hmac_sha256'])) { throw new RuntimeException('External recovery plan authentication failed'); }
$plan = json_decode($envelope['plan'], true, 64, JSON_THROW_ON_ERROR); unset($key);
$app = new Application(); $c = $app->getContainer(); $db = \OCP\Server::get(\OCP\IDBConnection::class); $config = \OCP\Server::get(\OCP\IConfig::class);
$fact = Closures::lookup($db, 'operation_id', $f['operation_id']); closedReplayCheck($fact !== null, 'Actual authenticated target closure required');
$normalizedRoots = array_map(fn(array $item): array => TargetDeletionProtocol::fact($item), $plan['target_closures'] ?? []);
closedReplayCheck(in_array($fact, $normalizedRoots, true), 'Verified external history omitted actual permanent target fact');
$tenantFacts = [];
foreach ($plan['tenant_deletions'] ?? [] as $ownerFact) {
    $ownerFact = TenantDeletionProtocol::fact($ownerFact);
    $actual = DirectoryTenantDeletionService::lookup($db, $ownerFact['origin_sha256'], $ownerFact['tenant']['tenant_id']);
    closedReplayCheck($actual === $ownerFact, 'Recovery fixture requires actual GET200-collected Tenant fact, not fabricated signed test metadata'); $tenantFacts[] = $ownerFact;
}
$directory = $c->get(DirectoryPublicationService::class); $bindings = $c->get(BindingRegistryService::class);
$configDir = sys_get_temp_dir() . '/weknora-target-recovery-' . bin2hex(random_bytes(6)); mkdir($configDir, 0700);
file_put_contents($configDir . '/config.php', '<?php $CONFIG=' . var_export(['maintenance' => true, 'instanceid' => $config->getSystemValueString('instanceid')], true) . ';'); chmod($configDir . '/config.php', 0600);
$maintenance = new \OC\AllConfig(new \OC\SystemConfig(new \OC\Config($configDir . '/')));
$replay = new PublicationRecoveryReplay($db, $maintenance, $bindings, $c->get(FilePublicationStateService::class), $c->get(ControlRecoveryService::class));
$db->beginTransaction();
try {
    // A controlled rollback fault changes only metadata inside this outer
    // transaction. It never creates a message/body/proof or deletes files.
    foreach ($tenantFacts as $ownerFact) {
        $q = $db->getQueryBuilder(); $q->delete('weknora_tenant_close')->where($q->expr()->eq('namespace_key', $q->createNamedParameter(DirectoryTenantDeletionService::namespaceKeyFromFact($ownerFact))))->executeStatement();
    }
    $q = $db->getQueryBuilder(); $q->delete('weknora_target_close')->where($q->expr()->eq('operation_id', $q->createNamedParameter($f['operation_id'])))->executeStatement();
    $q = $db->getQueryBuilder(); $q->update('weknora_dir_intent')->set('state', $q->createNamedParameter('active'))
        ->set('error_code', $q->createNamedParameter(''))->set('lease_until', $q->createNamedParameter(0))->set('next_attempt_at', $q->createNamedParameter(0))
        ->where($q->expr()->eq('operation_id', $q->createNamedParameter($f['operation_id'])))->executeStatement();
    $q = $db->getQueryBuilder(); $q->update('weknora_binding_id')->set('publication_state', $q->createNamedParameter('active'))
        ->set('sync_enabled', $q->createNamedParameter(1))->where($q->expr()->eq('binding_id', $q->createNamedParameter($fact['target']['binding_id'])))->executeStatement();
    $result = $replay->apply($plan);
    closedReplayCheck(!$result['ingress_reopen_permitted'] && Closures::lookup($db, 'operation_id', $f['operation_id']) === $fact, 'External permanent target control was not restored exactly');
    foreach ($tenantFacts as $ownerFact) {
        closedReplayCheck(DirectoryTenantDeletionService::lookup($db, $ownerFact['origin_sha256'], $ownerFact['tenant']['tenant_id']) === $ownerFact, 'Independent pre-checkpoint/later Tenant fact was not restored exactly');
    }
    closedReplayCheck($directory->show($f['user_id'], (int)$fact['root_file_id'])['operation']['state'] === 'target_deleted', 'Restored Files state revived');
    closedReplayDeny(fn() => $bindings->requirePublicationActive($fact['target']['binding_id']));
    closedReplayDeny(fn() => $bindings->setPublicationState($fact['target']['binding_id'], 'active', $f['user_id']));
    DirectoryTenantDeletionService::requireHeldPermanentFacts($db, $plan);
    if ($tenantFacts !== []) {
        $ownerFact = $tenantFacts[0];
        $q = $db->getQueryBuilder(); $q->delete('weknora_tenant_close')->where($q->expr()->eq('namespace_key', $q->createNamedParameter(DirectoryTenantDeletionService::namespaceKeyFromFact($ownerFact))))->executeStatement();
        closedReplayDeny(fn() => DirectoryTenantDeletionService::requireHeldPermanentFacts($db, $plan));
        closedReplayCheck($c->get(ControlRecoveryService::class)->closed(), 'Missing independently retained Tenant fact cleared global controls');
    }
    // Loss of an old intent does not free the physical directory for discovery.
    $q = $db->getQueryBuilder(); $q->delete('weknora_dir_intent')->where($q->expr()->eq('operation_id', $q->createNamedParameter($f['operation_id'])))->executeStatement();
    $shown = $directory->show($f['user_id'], (int)$fact['root_file_id']);
    closedReplayCheck($shown['operation']['state'] === 'target_deleted', 'Missing restored intent freed a permanently closed root');
    echo "Real NC/PostgreSQL rollback metadata replay restored the actual permanent target and independently authenticated Tenant facts and stopped source gates; missing old intent did not free the root; all fault writes roll back\n";
} finally {
    $db->rollBack(); unlink($configDir . '/config.php'); rmdir($configDir);
}
