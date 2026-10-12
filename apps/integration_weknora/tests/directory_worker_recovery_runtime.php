<?php
declare(strict_types=1);
// Real Nextcloud/PostgreSQL; every fixture row is rolled back, no remote calls required.
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\ControlRecoveryService;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\DirectoryDiscoveryService;
use OCA\IntegrationWeknora\Service\DirectoryOnboardingClient;
use OCA\IntegrationWeknora\Command\DiscoverDirectories;
function workerCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
function workerClosed(callable $fn): void {
    try { $fn(); } catch (DomainException $e) {
        workerCheck($e->getMessage() === 'Recovery automation is closed', 'Wrong closure reason'); return;
    }
    throw new RuntimeException('Closed recovery allowed ordinary automation');
}
$c = (new Application())->getContainer();
$db = \OCP\Server::get(\OCP\IDBConnection::class);
$directories = $c->get(DirectoryPublicationService::class);
$discovery = $c->get(DirectoryDiscoveryService::class);
$controls = $c->get(ControlRecoveryService::class);
$process = new ReflectionMethod($directories, 'process');
$update = new ReflectionMethod($directories, 'update');
$db->beginTransaction();
try {
    // Transaction-local empty control view prevents touching existing fixtures or network.
    foreach (['weknora_dir_intent', 'weknora_dir_rules', 'weknora_dir_discover', 'weknora_control_gate'] as $table) {
        $db->getQueryBuilder()->delete($table)->executeStatement();
    }
    $ids = [];
    foreach (['retired', 'pending', 'active'] as $state) {
        $id = DirectoryPublicationService::uuid(); $ids[$state] = $id;
        $db->insertIgnoreConflict('weknora_dir_intent', [
            'operation_id'=>$id, 'binding_id'=>'test_'.$state, 'root_key'=>hash('sha256', $id),
            'rule_id'=>'absent-rule', 'owner_uid'=>'fixture', 'actor_uid'=>'fixture',
            'folder_name'=>'fixture', 'state'=>$state, 'error_code'=>'', 'request_json'=>'{}',
            'updated_at'=>1, 'created_at'=>1,
        ]);
    }
    $read = static function (string $id) use ($db): array {
        $q=$db->getQueryBuilder(); $r=$q->select('*')->from('weknora_dir_intent')
            ->where($q->expr()->eq('operation_id',$q->createNamedParameter($id)))->executeQuery();
        try { return $r->fetchAssociative(); } finally { $r->closeCursor(); }
    };
    $retired = $read($ids['retired']);
    $process->invoke($directories, $ids['retired']);
    workerCheck($read($ids['retired']) === $retired, 'A retired intent was claimed or rewritten by an old worker');
    $update->invoke($directories, $ids['retired'], ['state'=>'active', 'permission_version'=>9]);
    workerCheck($read($ids['retired']) === $retired, 'Late completion resurrected retired metadata');

    // Execute the actual five-second CLI path: discovery alone must not leave intents idle.
    $command=$c->get(DiscoverDirectories::class);
    $exit=$command->run(new \Symfony\Component\Console\Input\ArrayInput([]), new \Symfony\Component\Console\Output\NullOutput());
    workerCheck($exit === 0, 'Fast worker failed');
    foreach (['pending','active'] as $state) {
        $row=$read($ids[$state]);
        workerCheck((int)$row['attempt_count'] === 1 && (int)$row['next_attempt_at'] > time(), 'Fast worker did not process a due intent');
    }
    workerCheck($read($ids['retired']) === $retired, 'Queue selection retried retirement');
    $command->run(new \Symfony\Component\Console\Input\ArrayInput([]), new \Symfony\Component\Console\Output\NullOutput());
    workerCheck((int)$read($ids['pending'])['attempt_count'] === 1, 'Worker ignored persisted retry backoff');

    $controls->closeFromPlan(['latest_content_revisions'=>[]]);
    $before=$controls->metadataInventory()['sha256'];
    workerClosed(fn()=>$directories->processDue());
    workerClosed(fn()=>$directories->reconcile());
    workerClosed(fn()=>$discovery->deliverDue());
    workerClosed(fn()=>$directories->saveRule([],0));
    workerClosed(fn()=>$directories->create('fixture',1,'blocked',[]));
    workerClosed(fn()=>$directories->associate('fixture',1,[]));
    workerClosed(fn()=>$directories->retry('fixture',$ids['pending']));
    workerClosed(fn()=>$directories->switchTarget('fixture',1,[]));
    workerClosed(fn()=>$directories->targetMigration('fixture',1,$ids['pending'],true));
    workerClosed(fn()=>$directories->sync('fixture',1,true,0));
    workerClosed(fn()=>$c->get(DirectoryOnboardingClient::class)->request([], 'POST', '/operations'));
    workerCheck($before === $controls->metadataInventory()['sha256'], 'Closed automation mutated recovery inventory');
    echo "PASS: retired intents and late completion stay retired; real CLI processes pending/active intents with backoff; closed recovery blocks discovery, provisioning and outbound requests without metadata changes\n";
} finally { $db->rollBack(); }
