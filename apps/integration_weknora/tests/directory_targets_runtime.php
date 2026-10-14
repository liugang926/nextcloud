<?php
declare(strict_types=1);
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
\OC_App::loadApp('user_ldap');
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\IdentityMappingService;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;
function targetsCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
function targetsConflict(callable $fn): void { try { $fn(); } catch (DomainException $e) { return; } throw new RuntimeException('Target mutation was accepted'); }
putenv('WEKNORA_EVENT_DEV_HTTP=1'); putenv('WEKNORA_LOCAL_COMPOSE=1'); putenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=1');
$origin = 'http://nextcloud-auto-space-onboarding-mock-20261010:8080';
putenv('WEKNORA_EVENT_ALLOWED_ORIGINS=' . $origin);
$uid = 'auto-prd-admin'; $services = (new Application())->getContainer();
$services->get(IdentityMappingService::class)->create('prd-directory', '12345678-abcd-1234-abcd-123456789abc', $uid, $uid);
$config = \OCP\Server::get(\OCP\IConfig::class); $credentials = []; $targets = [];
foreach (['7', '8', '9'] as $tenant) {
    $id = 'space-' . $tenant;
    $credentials[$id] = ['tenant_id' => $tenant, 'weknora_origin' => $origin,
        'token' => 'synthetic-onboarding-scoped-secret-' . $tenant . '-0123456789abcdef'];
    $targets[] = ['tenant_id' => $tenant, 'processing_template_id' => 'template-' . $tenant, 'credential_id' => $id];
}
$credentials['space-7-required'] = ['tenant_id' => '7', 'weknora_origin' => $origin,
    'token' => 'synthetic-onboarding-scoped-secret-7-required-0123456789abcdef'];
array_splice($targets, 1, 0, [['tenant_id' => '7', 'processing_template_id' => 'template-7-required', 'credential_id' => 'space-7-required']]);
$config->setSystemValue('weknora_onboarding_credentials', $credentials);
$scope = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid)->newFolder('targets-test-' . bin2hex(random_bytes(4)));
$folder = $scope->newFolder('Choose target');
$directory = $services->get(DirectoryPublicationService::class);
$directory->saveRule(['id' => 'targets_' . bin2hex(random_bytes(4)), 'owner_uid' => $uid, 'container_file_id' => $scope->getId(),
    'tenant_id' => '7', 'processing_template_id' => 'template-7', 'weknora_origin' => $origin, 'base_url' => 'http://test-nextcloud',
    'discovery' => 'direct_children', 'delegates' => [$uid], 'auto_enabled' => false, 'targets' => $targets], 0);
$candidates = $directory->candidates($uid, $folder->getId());
targetsCheck(array_column($candidates['targets'], 'tenant_id') === ['7', '7', '9'], 'Unauthorized Tenant was disclosed or authorized spaces/templates lost');
targetsCheck(array_column($candidates['targets'], 'processing_template_id') === ['template-7', 'template-7-required', 'template-9'] &&
    array_column($candidates['targets'], 'graph_mode') === ['optional', 'required', 'optional'], 'Same-Tenant templates or safe graph modes were conflated');
targetsCheck(!str_contains(json_encode($candidates, JSON_THROW_ON_ERROR), 'scoped-secret') &&
    !str_contains(json_encode($candidates, JSON_THROW_ON_ERROR), 'credential_id'), 'Candidate response leaked a server credential');
$choice = ['operation_id' => 'dddddddd-2222-4333-8444-' . bin2hex(random_bytes(6)), 'mode' => 'existing',
    'tenant_id' => '9', 'processing_template_id' => 'template-9', 'name' => 'Explicit target nine', 'knowledge_base_id' => $candidates['targets'][2]['candidates'][0]['id']];
$operation = $directory->associate($uid, $folder->getId(), $choice);
$pair = $services->get(SourcePairingRegistryService::class)->status($operation['binding_id']);
targetsCheck($operation['state'] === 'pending' && $pair['tenant_id'] === '9' && $pair['knowledge_base_id'] === $choice['knowledge_base_id'],
    'Chosen space/KB was replaced after unknown ACK');
targetsConflict(fn () => $directory->associate($uid, $folder->getId(), array_replace($choice,
    ['tenant_id' => '7', 'knowledge_base_id' => 'blank-7', 'processing_template_id' => 'template-7'])));
$other = $scope->newFolder('Unsupported target');
targetsConflict(fn () => $directory->associate($uid, $other->getId(), array_replace($choice,
    ['operation_id' => 'eeeeeeee-2222-4333-8444-' . bin2hex(random_bytes(6)), 'tenant_id' => '10'])));
targetsConflict(fn () => $directory->associate($uid, $other->getId(), array_replace($choice,
    ['operation_id' => 'eeeeeeee-2222-4333-8444-' . bin2hex(random_bytes(6)), 'tenant_id' => '7', 'processing_template_id' => 'expired-or-unapproved'])));
$ambiguous = array_replace($choice, ['operation_id' => 'eeeeeeee-2222-4333-8444-' . bin2hex(random_bytes(6)), 'tenant_id' => '7']);
unset($ambiguous['processing_template_id']);
targetsConflict(fn () => $directory->associate($uid, $other->getId(), $ambiguous));
$requiredChoice = array_replace($choice, ['operation_id' => 'eeeeeeee-2222-4333-8444-' . bin2hex(random_bytes(6)),
    'tenant_id' => '7', 'processing_template_id' => 'template-7-required', 'knowledge_base_id' => $candidates['targets'][1]['candidates'][0]['id']]);
$required = $directory->associate($uid, $other->getId(), $requiredChoice);
$same = $directory->associate($uid, $other->getId(), $requiredChoice);
targetsCheck($required['state'] === 'pending' && $same['operation_id'] === $required['operation_id'], 'Required-template retry changed the durable operation');
$q = \OCP\Server::get(\OCP\IDBConnection::class)->getQueryBuilder();
$q->select('request_json')->from('weknora_dir_intent')->where($q->expr()->eq('operation_id', $q->createNamedParameter($required['operation_id'])));
$request = json_decode($q->executeQuery()->fetchOne(), true, 32, JSON_THROW_ON_ERROR);
targetsCheck((string)$request['tenant_id'] === '7' && $request['processing_template_id'] === 'template-7-required', 'Required template was not immutable in the intent');
targetsConflict(fn () => $directory->associate($uid, $other->getId(), array_replace($requiredChoice, ['processing_template_id' => 'template-7'])));
echo "Authorized multi-Tenant and same-Tenant optional/required templates, scoped credentials, immutable exact retry, ambiguity and unapproved template rejection passed\n";
