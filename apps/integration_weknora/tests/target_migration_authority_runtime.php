<?php
declare(strict_types=1);
if(getenv('WEKNORA_PRD_TEST_RUNTIME')!=='1'){throw new RuntimeException('Disposable runtime required');}
define('OC_CONSOLE',1);require '/var/www/html/lib/base.php';\OC_App::loadApp('user_ldap');
set_exception_handler(static function(Throwable $e):void{fwrite(STDERR,(string)$e."\n");exit(1);});
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\{DirectoryPublicationService,BindingRegistryService,SourcePairingRegistryService,TargetMigrationService,IdentityMappingService};
function authorityCheck(bool $ok,string $why):void{if(!$ok){throw new RuntimeException($why);}}
$origin='http://nextcloud-auto-space-target-authority-mock-20261010:8080';
putenv('WEKNORA_EVENT_DEV_HTTP=1');putenv('WEKNORA_LOCAL_COMPOSE=1');putenv('WEKNORA_EVENT_ALLOWED_ORIGINS='.$origin);putenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=0');
$s=(new Application())->getContainer();$db=\OCP\Server::get(\OCP\IDBConnection::class);$cfg=\OCP\Server::get(\OCP\IConfig::class);
$dirs=$s->get(DirectoryPublicationService::class);$bindings=$s->get(BindingRegistryService::class);$pairs=$s->get(SourcePairingRegistryService::class);$moves=$s->get(TargetMigrationService::class);
$uid='prd-sourceowner';$admin='prd-alice';$ids=$s->get(IdentityMappingService::class);$who=$ids->forUser($uid);
$users=\OCP\Server::get(\OCP\IUserManager::class);\OCP\Server::get(\OCP\IUserSession::class)->setUser($users->get($uid));
$home=\OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid);$scope=$home->newFolder('Target authority '.bin2hex(random_bytes(5)));$root=$scope->newFolder('Source root');$original=$root->newFile('retained.txt','Retained across revoked target migration authority.');$etag=$original->getEtag();
$credentials=$cfg->getSystemValue('weknora_onboarding_credentials',[]);$credentials['authority-7']=['tenant_id'=>'7','weknora_origin'=>$origin,'token'=>'synthetic-authority-credential-0123456789abcdef'];$cfg->setSystemValue('weknora_onboarding_credentials',$credentials);
$rule=['id'=>'authority_'.bin2hex(random_bytes(5)),'owner_uid'=>$uid,'container_file_id'=>$scope->getId(),'tenant_id'=>'7','processing_template_id'=>'template-7','weknora_origin'=>$origin,'base_url'=>'http://nextcloud','discovery'=>'direct_children','delegates'=>[$uid],'auto_enabled'=>false,'targets'=>[['tenant_id'=>'7','processing_template_id'=>'template-7','credential_id'=>'authority-7']]];
$dirs->saveRule($rule,0);$oldOp=DirectoryPublicationService::uuid();$oldKB='authority-old-'.$oldOp;
$intent=$dirs->associate($uid,$root->getId(),['operation_id'=>$oldOp,'mode'=>'existing','name'=>'Source root','knowledge_base_id'=>$oldKB],false);$binding=$intent['binding_id'];$bindings->save($binding,'Source root',$uid,$root->getId());$prepared=$pairs->prepare($binding,$oldOp,'7',$oldKB,$uid);$p=$prepared['pairing'];$pairs->commit($binding,$p['key_id'],$oldOp,$p['instance_id'],'7',$oldKB,'authority-source-'.$oldOp);
$migrationId=DirectoryPublicationService::uuid();$pending=$dirs->switchTarget($uid,$root->getId(),['operation_id'=>$migrationId,'mode'=>'create','name'=>'New source','tenant_id'=>'7','processing_template_id'=>'template-7']);authorityCheck($pending['state']==='prepared','Unknown reservation ACK changed the phase');
$before=json_decode(file_get_contents($origin.'/stats'),true);$rule['delegates']=[];$dirs->saveRule($rule,1);
$q=$db->getQueryBuilder();$q->select('*')->from('weknora_target_move')->where($q->expr()->eq('operation_id',$q->createNamedParameter($migrationId)));$row=$q->executeQuery()->fetchAssociative();
try{(new ReflectionMethod($moves,'process'))->invoke($moves,$row);throw new RuntimeException('Revoked background delegate continued');}catch(DomainException $expected){}
$after=json_decode(file_get_contents($origin.'/stats'),true);authorityCheck($before===$after,'Revoked delegate made a remote mutation');
$status=$moves->status($migrationId,$root->getId());authorityCheck($status===$pending,'Revocation changed UUID, IDs or durable phase');
foreach($bindings->listBindings() as $b){if($b['id']===$binding){authorityCheck($b['publication_state']==='stopped','Unverified source remained open');}}
authorityCheck($original->getContent()==='Retained across revoked target migration authority.'&&$original->getEtag()===$etag,'Revocation changed originals');
// A real, freshly verified LDAP manager temporarily holds NC administrator
// membership. Restore only this test's addition even when any assertion fails.
$group=\OCP\Server::get(\OCP\IGroupManager::class)->get('admin');$manager=$users->get($admin);
authorityCheck($manager!==null&&$manager->getBackendClassName()==='LDAP','Real LDAP manager required');
$addedAdmin=!$group->inGroup($manager);$shareId=null;
if($addedAdmin){$group->addUser($manager);}
try{
 $actor=$ids->forUser($admin);authorityCheck($actor['directory_id']==='prd-samba-ad'&&$actor['object_guid']==='780a4363-44d9-4101-a7d1-461cb64c7c54','Current manager GUID did not come from verified LDAP');
 $beforeUnreadable=json_decode(file_get_contents($origin.'/stats'),true);
 try{$moves->retry($migrationId,$root->getId(),['actor_directory_id'=>$actor['directory_id'],'actor_object_guid'=>$actor['object_guid']],$admin);throw new RuntimeException('Administrator bypassed source READ');}catch(DomainException $expected){}
 authorityCheck($beforeUnreadable===json_decode(file_get_contents($origin.'/stats'),true),'Unreadable administrator made a remote call');
 $shares=\OCP\Server::get(\OCP\Share\IManager::class);$share=$shares->newShare();
 $share->setNode($root)->setShareType(\OCP\Share\IShare::TYPE_USER)->setSharedWith($admin)->setSharedBy($uid)->setShareOwner($uid)->setPermissions(\OCP\Constants::PERMISSION_READ);
 $shareId=$shares->createShare($share)->getId();
 // Mount discovery is per request; reset the CLI request's already-cached
 // no-READ view before exercising the newly created real user share.
 \OCP\Server::get(\OCP\IUserSession::class)->setUser($manager);\OC_Util::tearDownFS();\OC_Util::setupFS($admin);
 $resumed=$moves->retry($migrationId,$root->getId(),['actor_directory_id'=>$actor['directory_id'],'actor_object_guid'=>$actor['object_guid']],$admin);
 authorityCheck($resumed['state']==='reserved'&&$resumed['operation_id']===$migrationId&&$resumed['new_operation_id']===$pending['new_operation_id']&&$resumed['new_binding_id']===$pending['new_binding_id'],'Current administrator did not resume the exact durable operation');
 $events=json_decode(file_get_contents($origin.'/stats'),true)['events'];$last=array_slice($events,-3);foreach($last as $event){authorityCheck($event['actor']===$actor['object_guid'],'Current administrator borrowed the old actor identity');}
 $current=$q->executeQuery()->fetchAssociative();authorityCheck($current['actor_uid']===$admin,'Background follow-up retained a revoked manager');
 echo json_encode(['result'=>'PASS','runtime'=>'Nextcloud34/PostgreSQL16/trustedLDAPS','dev_identity_exception'=>false,'revoked_delegate_remote_calls'=>0,'admin_without_read_remote_calls'=>0,'current_manager_directory'=>$actor['directory_id'],'current_manager_guid'=>$actor['object_guid'],'unknown_ack_phase'=>$resumed['state'],'same_operation_ids'=>true,'original_retained'=>true,'remote_boundary'=>'controlled HTTP reservation/unknown retirement ACK, not real Weknora roles'],JSON_THROW_ON_ERROR)."\n";
}finally{
 if($addedAdmin){$group->removeUser($manager);}
 if($shareId!==null){try{$shares->deleteShare($shares->getShareById($shareId));}catch(Throwable $ignored){}}
}
