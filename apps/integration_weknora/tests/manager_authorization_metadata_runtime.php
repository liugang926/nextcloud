<?php
declare(strict_types=1);
// Only the task-owned disposable NC/PostgreSQL runtime; transaction rolls back.
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
require_once __DIR__ . '/../lib/Service/DirectoryPublicationService.php';
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });
$db = \OCP\Server::get(\OCP\IDBConnection::class);
$config = \OCP\Server::get(\OCP\IConfig::class);
if ($config->getSystemValueString('dbtype') !== 'pgsql') { throw new RuntimeException('Actual PostgreSQL required'); }
$reflection = new ReflectionClass(DirectoryPublicationService::class);
$service = $reflection->newInstanceWithoutConstructor();
$reflection->getProperty('db')->setValue($service, $db);
$reflection->getProperty('config')->setValue($service, $config);
$method = $reflection->getMethod('managerIntentMetadata');
$id = '11334455-6677-4889-aabb-' . bin2hex(random_bytes(6));
$db->beginTransaction();
try {
    $q = $db->getQueryBuilder();
    $values = ['operation_id' => $id, 'binding_id' => 'manager-' . $id, 'root_key' => hash('sha256', $id),
        'rule_id' => 'manager-test-rule', 'owner_uid' => 'fixture', 'actor_uid' => 'fixture',
        'folder_name' => 'Must not be projected', 'state' => 'pending', 'error_code' => '',
        'request_json' => json_encode(['processing_template_id' => 'exact-template', 'name' => 'Must not be projected'], JSON_THROW_ON_ERROR)];
    $q->insert('weknora_dir_intent');
    foreach ($values as $column => $value) { $q->setValue($column, $q->createNamedParameter($value)); }
    $q->executeStatement();
    $row = $method->invoke($service, $id);
    $keys = array_keys($row); sort($keys);
    $expected = ['operation_id', 'binding_id', 'root_key', 'root_file_id', 'rule_id', 'state', 'processing_template_id']; sort($expected);
    if ($keys !== $expected || $row['processing_template_id'] !== 'exact-template' || $row['operation_id'] !== $id) {
        throw new RuntimeException('Manager metadata projection changed scope or included body');
    }
    if ($method->invoke($service, '22334455-6677-4889-aabb-' . bin2hex(random_bytes(6))) !== null) {
        throw new RuntimeException('Missing intent was not unknown');
    }
    echo json_encode(['actual_pg_metadata_projection' => 'PASS', 'full_manager_authorization' => false], JSON_THROW_ON_ERROR) . "\n";
} finally { $db->rollBack(); }
