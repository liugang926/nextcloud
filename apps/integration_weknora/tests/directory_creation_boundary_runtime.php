<?php
declare(strict_types=1);
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1); require '/var/www/html/lib/base.php'; \OC_App::loadApp('user_ldap');
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });
putenv('WEKNORA_EVENT_DEV_HTTP=1'); putenv('WEKNORA_LOCAL_COMPOSE=1');
putenv('WEKNORA_EVENT_ALLOWED_ORIGINS=http://nextcloud-auto-space-onboarding-mock-20261010:8080');
putenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=0');
$uid = 'prd-sourceowner';
$services = (new \OCA\IntegrationWeknora\AppInfo\Application())->getContainer();
$directories = $services->get(\OCA\IntegrationWeknora\Service\DirectoryPublicationService::class);
$home = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid);
$scope = $home->newFolder('Creation boundary ' . bin2hex(random_bytes(4)));
$child = $scope->newFolder('Direct child');
$directories->saveRule(['id'=>'creation_boundary_'.bin2hex(random_bytes(5)), 'owner_uid'=>$uid,
    'container_file_id'=>$scope->getId(), 'tenant_id'=>'7', 'processing_template_id'=>'template-7',
    'weknora_origin'=>'http://nextcloud-auto-space-onboarding-mock-20261010:8080', 'base_url'=>'http://nextcloud',
    'discovery'=>'direct_children', 'delegates'=>[$uid], 'auto_enabled'=>false], 0);
if (!$directories->show($uid, $scope->getId())['can_create'] || $directories->show($uid, $child->getId())['can_create']) {
    throw new RuntimeException('Direct-child creation authority escaped its configured parent');
}
try {
    $directories->create($uid, $child->getId(), 'Forbidden grandchild', [
        'operation_id'=>\OCA\IntegrationWeknora\Service\DirectoryPublicationService::uuid(),
        'mode'=>'create', 'name'=>'Forbidden grandchild']);
    throw new RuntimeException('Out-of-rule grandchild creation was accepted');
} catch (DomainException $expected) {}
if ($child->nodeExists('Forbidden grandchild')) { throw new RuntimeException('Denied creation still wrote a source directory'); }
echo "Actual LDAP publisher: direct_children create is restricted to its configured container; child cannot create an independent grandchild root and no directory is written\n";
