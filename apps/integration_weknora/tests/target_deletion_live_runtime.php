<?php
declare(strict_types=1);
// Requires an already paired actual NC/WK onboarding operation and real WK
// producer-generated target ledger. This script fabricates no remote receipt.
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1); require '/var/www/html/lib/base.php'; \OC_App::loadApp('user_ldap');
set_exception_handler(static function(Throwable $e): void { fwrite(STDERR, $e->getMessage() . "\n"); exit(1); });
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\DirectoryTargetClosureService;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;
use OCA\IntegrationWeknora\Service\ControlRecoveryService;
use OCA\IntegrationWeknora\Service\PublicationRecoveryLedger;
function closureLiveCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
function closureLiveDeny(callable $fn): void { try { $fn(); } catch (DomainException|\OCA\IntegrationWeknora\Service\BindingPublicationStoppedException $e) { return; } throw new RuntimeException('Permanent target reopened'); }
$path = $argv[1] ?? ''; $stage = $argv[2] ?? '';
if (!is_file($path) || (fileperms($path) & 0o077) || !in_array($stage, ['before', 'unverified', 'deleted'], true)) { throw new RuntimeException('Private actual operation descriptor and stage required'); }
$f = json_decode(file_get_contents($path), true, 16, JSON_THROW_ON_ERROR);
foreach (['operation_id', 'user_id'] as $key) { if (!is_string($f[$key] ?? null) || $f[$key] === '') { throw new RuntimeException('Invalid actual operation descriptor'); } }
$app = new Application(); $c = $app->getContainer(); $db = \OCP\Server::get(\OCP\IDBConnection::class);
$directory = $c->get(DirectoryPublicationService::class); $bindings = $c->get(BindingRegistryService::class); $pairs = $c->get(SourcePairingRegistryService::class);
$q = $db->getQueryBuilder(); $r = $q->select('*')->from('weknora_dir_intent')->where($q->expr()->eq('operation_id', $q->createNamedParameter($f['operation_id'])))->executeQuery();
try { $intent = $r->fetchAssociative(); } finally { $r->closeCursor(); }
closureLiveCheck($intent !== false, 'Actual persisted onboarding intent required');
$pair = $pairs->status($intent['binding_id']); closureLiveCheck($pair !== null && $pair['state'] === 'active', 'Actual paired source required; do not substitute virgin-source retirement');
$originalFile = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($f['user_id'])->getById((int)$f['file_id'])[0] ?? null;
closureLiveCheck($originalFile instanceof \OCP\Files\File, 'Actual retained source file required');
$original = ['etag' => $originalFile->getEtag(), 'body_sha256' => hash('sha256', $originalFile->getContent())];
$q = $db->getQueryBuilder(); $r = $q->select('key_id', 'token_sha256')->from('weknora_machine_key')->where($q->expr()->eq('binding_id', $q->createNamedParameter($intent['binding_id'])))->orderBy('key_id')->executeQuery();
try { $credentials = $r->fetchAllAssociative(); } finally { $r->closeCursor(); }
if ($stage === 'before') {
    $bindings->requirePublicationActive($intent['binding_id']); closureLiveCheck($intent['state'] === 'active', 'Actual live operation required');
    $f['baseline'] = ['pair' => $pair, 'original' => $original, 'credentials' => $credentials, 'local_revision' => (int)$intent['revision']];
    file_put_contents($path, json_encode($f, JSON_THROW_ON_ERROR)); chmod($path, 0600);
    echo "Actual paired source target baseline retained; delete the WK target through its production path before deleted stage\n"; exit;
}
closureLiveCheck(isset($f['baseline']), 'Actual before-stage evidence required');
if ($stage === 'unverified') {
    // The orchestrator must arrange a real revoked credential / unknown
    // operation / TLS or unavailable status. No fixture JSON is interpreted
    // as a deletion receipt in this phase.
    $pending = $directory->retry($f['user_id'], $f['operation_id']);
    closureLiveCheck($pending['state'] === 'pending' && $pending['error_code'] !== 'target_deleted', 'Transport/identity failure became terminal');
    closureLiveCheck(DirectoryTargetClosureService::lookup($db, 'operation_id', $f['operation_id']) === null, 'Unknown status installed a target receipt');
    closureLiveDeny(fn() => $bindings->requirePublicationActive($intent['binding_id']));
    closureLiveCheck($pairs->status($intent['binding_id']) === $f['baseline']['pair'] && $original === $f['baseline']['original'] && $credentials === $f['baseline']['credentials'], 'Unverified target changed originals/source credentials');
    echo "Actual unknown/authentication/transport outcome stopped publication without inventing target deletion or retirement\n"; exit;
}
// The public retry uses current NC delegate/LDAP checks and approved credential
// GET 200. An error status cannot become a terminal target observation.
$closed = $directory->retry($f['user_id'], $f['operation_id']);
closureLiveCheck($closed['state'] === 'target_deleted' && $closed['error_code'] === 'target_deleted', 'Actual WK closure has not been verified; do not call this PASS');
$fact = DirectoryTargetClosureService::lookup($db, 'operation_id', $f['operation_id']); closureLiveCheck($fact !== null, 'Durable NC exact closure missing');
closureLiveCheck((int)$closed['revision'] === $f['baseline']['local_revision'] + 1, 'Terminal intent CAS revision did not advance exactly once');
closureLiveCheck($pairs->status($intent['binding_id']) === $f['baseline']['pair'] && $original === $f['baseline']['original'] && $credentials === $f['baseline']['credentials'], 'Target closure changed source identity, original bytes or credentials');
closureLiveDeny(fn() => $bindings->requirePublicationActive($intent['binding_id']));
closureLiveDeny(fn() => $bindings->setPublicationState($intent['binding_id'], 'active', $f['user_id']));
closureLiveDeny(fn() => $directory->retry($f['user_id'], $f['operation_id']));
$shown = $directory->show($f['user_id'], (int)$intent['root_file_id']);
closureLiveCheck($shown['operation']['state'] === 'target_deleted' && !$shown['sync_enabled'], 'Files terminal state/sync not closed');
$inventory = $c->get(ControlRecoveryService::class)->metadataInventory(); closureLiveCheck(isset($inventory['tables']['weknora_target_close']), 'Recovery inventory omitted target closure');
$page = (new PublicationRecoveryLedger($db, \OCP\Server::get(\OCP\IConfig::class)))->page(0); $seen = false; $pages = 0;
do {
    foreach ($page['items'] as $event) { if ($event['kind'] === 'target_deleted' && ($event['target_closure'] ?? null) === $fact) { $seen = true; } }
    if ($seen || !$page['has_more'] || ++$pages >= 256) { break; }
    $page = (new PublicationRecoveryLedger($db, \OCP\Server::get(\OCP\IConfig::class)))->page((int)$page['next_sequence']);
} while (true);
closureLiveCheck($seen, 'Independent closure journal fact missing');
echo "Actual authenticated WK closure -> NC terminal CAS, current read/resume/retry denial, unchanged source/file/credentials and exact recovery fact passed; physical GC is not claimed\n";
