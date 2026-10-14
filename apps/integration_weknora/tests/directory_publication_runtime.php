<?php
declare(strict_types=1);
// Real Nextcloud34/PostgreSQL16. Execute only in the task's disposable runtime.
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
\OC_App::loadApp('user_ldap');
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\DirectoryGrantSnapshotService;
use OCA\IntegrationWeknora\Service\IdentityMappingService;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;

function checkDir(bool $ok, string $message): void { if (!$ok) { throw new RuntimeException($message); } }
function conflictDir(callable $fn): void {
    try { $fn(); } catch (DomainException $e) { return; }
    throw new RuntimeException('Expected immutable directory/rule conflict');
}
putenv('WEKNORA_EVENT_DEV_HTTP=1');
putenv('WEKNORA_LOCAL_COMPOSE=1');
putenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=1');
putenv('WEKNORA_EVENT_ALLOWED_ORIGINS=http://test-weknora');
$app = new Application(); $services = $app->getContainer();
$uid = 'auto-prd-admin';
$identity = $services->get(IdentityMappingService::class);
$identity->create('prd-directory', '12345678-abcd-1234-abcd-123456789abc', $uid, $uid);
$home = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid);
$scope = $home->newFolder('directory-test-' . bin2hex(random_bytes(4)));
$root = $scope->newFolder('Existing publication');
$file = $root->newFile('original.txt', 'Source data must survive onboarding failure.');
$beforeEtag = $file->getEtag();
$directory = $services->get(DirectoryPublicationService::class);
$rule = ['id' => 'rule_' . bin2hex(random_bytes(4)), 'owner_uid' => $uid, 'container_file_id' => $scope->getId(),
    'tenant_id' => '7', 'processing_template_id' => 'template-1', 'weknora_origin' => 'http://test-weknora',
    'base_url' => 'http://test-nextcloud', 'discovery' => 'direct_children', 'delegates' => [$uid], 'auto_enabled' => false];
$saved = $directory->saveRule($rule, 0);
checkDir($saved['revision'] === 1, 'Rule revision did not advance');
conflictDir(fn () => $directory->saveRule($rule, 0));
$overlap = array_replace($rule, ['id' => $rule['id'] . '_overlap', 'container_file_id' => $root->getId()]);
conflictDir(fn () => $directory->saveRule($overlap, 0));
$before = $directory->show($uid, $root->getId());
checkDir($before['manageable'] && $before['source_state'] === 'unbound', 'Explicit directory delegation was unavailable');
$choice = ['operation_id' => '11111111-2222-4333-8444-' . bin2hex(random_bytes(6)), 'mode' => 'existing',
    'name' => 'Existing publication', 'knowledge_base_id' => 'empty-target-1'];
// No onboarding credential/network is available: persist and retain the exact
// target instead of switching to auto-create or deleting the source directory.
$first = $directory->associate($uid, $root->getId(), $choice);
checkDir($first['state'] === 'pending' && $first['folder_id'] === $root->getId(), 'Failed target did not remain pending');
$again = $directory->associate($uid, $root->getId(), $choice);
checkDir($again['operation_id'] === $first['operation_id'], 'Exact retry created a new operation');
conflictDir(fn () => $directory->associate($uid, $root->getId(), array_replace($choice,
    ['operation_id' => '44444444-2222-4333-8444-' . bin2hex(random_bytes(6)), 'knowledge_base_id' => 'another-target'])));
checkDir($file->getContent() === 'Source data must survive onboarding failure.' && $file->getEtag() === $beforeEtag,
    'Onboarding modified source bytes or ETag');
$child = $root->newFolder('Child');
conflictDir(fn () => $directory->associate($uid, $child->getId(), array_replace($choice,
    ['operation_id' => '55555555-2222-4333-8444-' . bin2hex(random_bytes(6))])));
$createChoice = ['operation_id' => '77777777-2222-4333-8444-' . bin2hex(random_bytes(6)), 'mode' => 'create', 'name' => 'New publication'];
$created = $directory->create($uid, $scope->getId(), 'New publication', $createChoice);
checkDir($created['state'] === 'pending' && $created['folder_id'] > 0, 'Create intent lost its original directory');
$createdAgain = $directory->create($uid, $scope->getId(), 'New publication', $createChoice);
checkDir($createdAgain['folder_id'] === $created['folder_id'], 'Repeated create made a duplicate directory');
$snapshot = $services->get(DirectoryGrantSnapshotService::class)->snapshot($root, $uid, $uid);
checkDir(count($snapshot) === 1 && $snapshot[0]['kind'] === 'user' && $snapshot[0]['object_guid'] === '12345678-abcd-1234-abcd-123456789abc',
    'Owner grant was not based on a verified stable identity');
$otherRoot = $home->newFolder('handoff-test-' . bin2hex(random_bytes(4)));
$binding = 'handoff_' . bin2hex(random_bytes(8));
$services->get(BindingRegistryService::class)->save($binding, 'Handoff test', $uid, $otherRoot->getId());
$pairs = $services->get(SourcePairingRegistryService::class);
$pairOp = '99999999-2222-4333-8444-' . bin2hex(random_bytes(6));
$prepared = $pairs->prepare($binding, $pairOp, '7', 'handoff-kb-' . $binding, $uid);
$retryPrepared = $pairs->prepare($binding, $pairOp, '7', 'handoff-kb-' . $binding, $uid);
checkDir(!isset($retryPrepared['token']) && $pairs->handoffToken($binding, $pairOp) === $prepared['token'],
    'One-time pairing token cannot be recovered after response loss');
$db = \OCP\Server::get(\OCP\IDBConnection::class); $q = $db->getQueryBuilder();
$result = $q->select('token_ciphertext')->from('weknora_pair_handoff')->where($q->expr()->eq('operation_id', $q->createNamedParameter($pairOp)))->executeQuery();
try { $ciphertext = $result->fetchOne(); } finally { $result->closeCursor(); }
checkDir(is_string($ciphertext) && $ciphertext !== $prepared['token'] && !str_contains($ciphertext, $prepared['token']), 'Pairing token stored in plaintext');
echo "Directory rules, overlap/CAS, exact create/associate retries, retained originals, stable identity grants and encrypted pairing handoff passed\n";
