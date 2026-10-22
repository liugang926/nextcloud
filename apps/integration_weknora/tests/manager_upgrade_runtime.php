<?php
declare(strict_types=1);
// The harness owns this entire disposable PostgreSQL/Nextcloud installation.
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });
$db = \OCP\Server::get(\OCP\IDBConnection::class);
$config = \OCP\Server::get(\OCP\IConfig::class);
if ($config->getSystemValueString('dbtype') !== 'pgsql') { throw new RuntimeException('Actual PostgreSQL required'); }
$mode = $argv[1] ?? '';
$prefix = $config->getSystemValueString('dbtableprefix', 'oc_');
$schema = $db->createSchema();
$hasTable = static fn(string $name): bool => $schema->hasTable($prefix . $name);
foreach (['weknora_target_close', 'weknora_tenant_close', 'weknora_deletion_probe'] as $table) {
    if ($hasTable($table) !== ($mode !== 'baseline')) { throw new RuntimeException('Unexpected migration table: ' . $table); }
}
$log = $schema->getTable($prefix . 'weknora_recovery_log');
if ($log->hasColumn('tenant_namespace') !== ($mode !== 'baseline')) { throw new RuntimeException('Unexpected recovery selector schema'); }
if ($mode === 'baseline') {
    if ($config->getAppValue('integration_weknora', 'installed_version') !== '0.6.0') { throw new RuntimeException('Baseline version mismatch'); }
    $home = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder('auto-prd-admin');
    $folder = $home->newFolder('ManagerUpgradeWitness');
    $file = $folder->newFile('retained.txt', 'Synthetic original bytes retained across migration29');
    $db->beginTransaction();
    try {
        \OCA\IntegrationWeknora\Service\PublicationRecoveryLedger::appendInTransaction($db, 'manager-upgrade-witness', $file->getId(), 'metadata', 7);
        $db->commit();
    } catch (Throwable $e) { $db->rollBack(); throw $e; }
    $row = $db->executeQuery('SELECT sequence,binding_id,file_id,kind,source_revision,created_at,chain_sha256 FROM *PREFIX*weknora_recovery_log ORDER BY sequence')->fetchAllAssociative();
    $config->setAppValue('integration_weknora', 'manager_upgrade_witness', json_encode(['file_id' => $file->getId(), 'content_sha256' => hash('sha256', $file->getContent()), 'log_sha256' => hash('sha256', json_encode($row, JSON_THROW_ON_ERROR))], JSON_THROW_ON_ERROR));
} elseif ($mode === 'candidate') {
    if ($config->getAppValue('integration_weknora', 'installed_version') !== '0.8.16') { throw new RuntimeException('Candidate version mismatch'); }
    if ($log->getColumn('tenant_namespace')->getNotnull()) { throw new RuntimeException('Legacy rows require nullable tenant selector'); }
    $witness = json_decode($config->getAppValue('integration_weknora', 'manager_upgrade_witness'), true, 512, JSON_THROW_ON_ERROR);
    $row = $db->executeQuery('SELECT sequence,binding_id,file_id,kind,source_revision,created_at,chain_sha256 FROM *PREFIX*weknora_recovery_log ORDER BY sequence')->fetchAllAssociative();
    if (!hash_equals($witness['log_sha256'], hash('sha256', json_encode($row, JSON_THROW_ON_ERROR)))) { throw new RuntimeException('Migration altered existing journal bytes'); }
    $home = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder('auto-prd-admin');
    $file = $home->get('ManagerUpgradeWitness/retained.txt');
    if ($file->getId() !== $witness['file_id'] || !hash_equals($witness['content_sha256'], hash('sha256', $file->getContent()))) { throw new RuntimeException('Migration changed the original file'); }
    if ((int)$db->executeQuery('SELECT COUNT(*) FROM *PREFIX*weknora_recovery_log WHERE tenant_namespace IS NOT NULL')->fetchOne() !== 0) { throw new RuntimeException('Migration invented a tenant deletion selector'); }
    if ((int)$db->executeQuery('SELECT COUNT(*) FROM *PREFIX*weknora_control_gate')->fetchOne() !== 0) { throw new RuntimeException('Migration automatically opened or seeded recovery gate'); }
} else { throw new InvalidArgumentException('Unknown witness mode'); }
echo json_encode(['migration29_' . $mode => 'PASS', 'actual_database' => 'pgsql', 'production_modified' => false], JSON_THROW_ON_ERROR) . "\n";
