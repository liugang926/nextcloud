<?php
declare(strict_types=1);
// PostgreSQL filecache metadata fixture; this does NOT upload 100 GB of bytes.
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
\OC_App::loadApp('user_ldap');
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\FolderSummaryService;
$services = (new Application())->getContainer(); $uid = 'auto-prd-admin';
$db = \OCP\Server::get(\OCP\IDBConnection::class); $config = \OCP\Server::get(\OCP\IConfig::class);
if ($config->getSystemValueString('dbtype') !== 'pgsql') { throw new RuntimeException('Owned PostgreSQL fixture required'); }
$prefix = $config->getSystemValueString('dbtableprefix', 'oc_');
if (!preg_match('/\A[A-Za-z0-9_]+\z/D', $prefix)) { throw new RuntimeException('Unexpected table prefix'); }
$home = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid);
$inner = (new ReflectionProperty($db, 'inner'))->getValue($db);
$counter = new class implements \Doctrine\DBAL\Logging\SQLLogger {
    public int $count = 0;
    public function startQuery($sql, ?array $params = null, ?array $types = null): void { $this->count++; }
    public function stopQuery(): void {}
};
$oldLogger = $inner->getConfiguration()->getSQLLogger();
foreach ([205, 1000, 10000] as $size) {
    $root = $home->newFolder('summary-perf-' . $size . '-' . bin2hex(random_bytes(4)));
    $seed = $root->newFile('seed.txt', 'Real seed for a synthetic metadata workload.');
    $q = $db->getQueryBuilder(); $r = $q->select('*')->from('filecache')->where($q->expr()->eq('fileid', $q->createNamedParameter($seed->getId())))->executeQuery();
    try { $row = $r->fetchAssociative(); } finally { $r->closeCursor(); }
    if ($row === false) { throw new RuntimeException('Seed filecache missing'); }
    $columns = array_values(array_filter(array_keys($row), static fn (string $column): bool => $column !== 'fileid'));
    foreach ($columns as $column) { if (!preg_match('/\A[A-Za-z0-9_]+\z/D', $column)) { throw new RuntimeException('Unexpected cache column'); } }
    $name = "'synthetic-' || n::text || '.txt'";
    $path = ':path_prefix || ' . $name;
    $expressions = array_map(static fn (string $column): string => match ($column) {
        'name' => $name, 'path' => $path, 'path_hash' => 'md5(' . $path . ')', 'etag' => 'md5(n::text)',
        'size' => '10485760', 'unencrypted_size' => '10485760', default => 's.' . $column,
    }, $columns);
    $db->executeUpdate('INSERT INTO ' . $prefix . 'filecache (' . implode(',', $columns) . ') SELECT ' .
        implode(',', $expressions) . ' FROM ' . $prefix . 'filecache s CROSS JOIN generate_series(1,:count) n WHERE s.fileid=:seed',
        ['path_prefix' => dirname($row['path']) . '/', 'count' => $size - 1, 'seed' => $seed->getId()]);
    $binding = 'perf_' . bin2hex(random_bytes(8));
    $services->get(BindingRegistryService::class)->save($binding, 'Synthetic summary metadata workload', $uid, $root->getId());
    $summary = $services->get(FolderSummaryService::class);
    $counter->count = 0; $inner->getConfiguration()->setSQLLogger($counter);
    $start = hrtime(true); $first = $summary->forUser($uid, $root->getId())['summary'];
    $elapsed = (hrtime(true) - $start) / 1e9;
    $queries = $counter->count; $inner->getConfiguration()->setSQLLogger($oldLogger);
    if ($first['total_visible'] !== 200 || !$first['partial']) { throw new RuntimeException('Large metadata workload was truncated incorrectly'); }
    if ($queries > 500) { throw new RuntimeException('Per-file query regression: ' . $queries); }
    $counter->count = 0; $inner->getConfiguration()->setSQLLogger($counter);
    $start = hrtime(true); $next = $summary->forUser($uid, $root->getId(), $first['next_cursor'])['summary'];
    $nextElapsed = (hrtime(true) - $start) / 1e9;
    $nextQueries = $counter->count; $inner->getConfiguration()->setSQLLogger($oldLogger);
    if ($next['generation'] !== $first['generation'] || $next['page_offset'] !== 200) { throw new RuntimeException('Large summary changed its stable generation'); }
    echo json_encode(['fixture' => 'synthetic_filecache_metadata', 'files' => $size,
        'declared_metadata_bytes' => ($size - 1) * 10485760, 'actual_source_bytes_tested' => false,
        'first_page_seconds' => round($elapsed, 3), 'first_page_sql_queries' => $queries,
        'next_page_seconds' => round($nextElapsed, 3), 'next_page_sql_queries' => $nextQueries,
        'php_peak_memory_bytes' => memory_get_peak_usage(true)], JSON_THROW_ON_ERROR) . "\n";
}
