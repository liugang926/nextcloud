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
    foreach (['retired', 'target_deleted', 'pending', 'active', 'blocked'] as $state) {
        $id = DirectoryPublicationService::uuid(); $ids[$state] = $id;
        $db->insertIgnoreConflict('weknora_dir_intent', [
            'operation_id'=>$id, 'binding_id'=>'test_'.$state, 'root_key'=>hash('sha256', $id),
            'rule_id'=>'absent-rule', 'owner_uid'=>'fixture', 'actor_uid'=>'fixture',
            'folder_name'=>'fixture', 'state'=>$state, 'error_code'=>'', 'request_json'=>'{}',
            'updated_at'=>1, 'created_at'=>1,
            'next_attempt_at'=>$state === 'active' ? time() + 300 : 0,
        ]);
    }
    $read = static function (string $id) use ($db): array {
        $q=$db->getQueryBuilder(); $r=$q->select('*')->from('weknora_dir_intent')
            ->where($q->expr()->eq('operation_id',$q->createNamedParameter($id)))->executeQuery();
        try { return $r->fetchAssociative(); } finally { $r->closeCursor(); }
    };
    $terminal = [];
    foreach (['retired', 'target_deleted'] as $state) {
        $terminal[$state] = $read($ids[$state]);
        $process->invoke($directories, $ids[$state]);
        workerCheck($read($ids[$state]) === $terminal[$state], 'A terminal intent was claimed or rewritten by an old worker');
        foreach (['active', 'pending'] as $lateState) {
            $update->invoke($directories, $ids[$state], ['state'=>$lateState, 'permission_version'=>9]);
            workerCheck($read($ids[$state]) === $terminal[$state], 'Late completion or failure resurrected terminal metadata');
        }
    }

    // Execute the actual five-second CLI path: discovery alone must not leave intents idle.
    $command=$c->get(DiscoverDirectories::class);
    $exit=$command->run(new \Symfony\Component\Console\Input\ArrayInput([]), new \Symfony\Component\Console\Output\NullOutput());
    workerCheck($exit === 0, 'Fast worker failed');
    $pendingRow=$read($ids['pending']);
    workerCheck((int)$pendingRow['attempt_count'] === 1 && (int)$pendingRow['next_attempt_at'] > time(), 'Fast worker did not process a due pending intent');
    $activeRow=$read($ids['active']);
    workerCheck((int)$activeRow['attempt_count'] === 0 && (int)$activeRow['next_attempt_at'] > time(),
        'Fast worker reprocessed a completed active intent');
    $blockedRow=$read($ids['blocked']);
    workerCheck((int)$blockedRow['attempt_count'] === 0,
        'Fast worker retried blocked work without an explicit user request');
    $process->invoke($directories, $ids['blocked'], true);
    $blockedRow=$read($ids['blocked']);
    workerCheck((int)$blockedRow['attempt_count'] === 1,
        'Explicit retry could not claim blocked work');
    foreach ($terminal as $state => $before) {
        workerCheck($read($ids[$state]) === $before, 'Queue selection retried a terminal intent');
    }
    $readProbe = static function (string $id) use ($db): array {
        $q = $db->getQueryBuilder();
        $r = $q->select('*')->from('weknora_deletion_probe')
            ->where($q->expr()->eq('operation_id', $q->createNamedParameter($id)))->executeQuery();
        try { return $r->fetchAssociative(); } finally { $r->closeCursor(); }
    };
    $probes = [];
    foreach (array_keys($terminal) as $state) {
        $probes[$state] = $readProbe($ids[$state]);
        workerCheck((int)$probes[$state]['next_check_at'] > time() && (int)$probes[$state]['lease_until'] === 0,
            'Missing-probe metadata check did not persist backoff or release its lease');
    }
    $command->run(new \Symfony\Component\Console\Input\ArrayInput([]), new \Symfony\Component\Console\Output\NullOutput());
    workerCheck((int)$read($ids['pending'])['attempt_count'] === 1, 'Worker ignored persisted retry backoff');
    foreach ($probes as $state => $before) {
        workerCheck($readProbe($ids[$state]) === $before, 'Terminal metadata probe ignored persisted backoff');
    }
    // A due check owned by another worker must remain untouched.
    $q = $db->getQueryBuilder();
    $q->update('weknora_deletion_probe')->set('next_check_at', $q->createNamedParameter(0))
        ->set('lease_until', $q->createNamedParameter(time() + 600))
        ->where($q->expr()->eq('operation_id', $q->createNamedParameter($ids['target_deleted'])))->executeStatement();
    $leased = $readProbe($ids['target_deleted']);
    $directories->processDue();
    workerCheck($readProbe($ids['target_deleted']) === $leased, 'A second worker stole an active metadata lease');

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
    echo "PASS: retired/target_deleted intents reject late success and failure; real CLI processes pending/active intents with backoff; closed recovery blocks discovery, provisioning and outbound requests without metadata changes\n";
} finally { $db->rollBack(); }
