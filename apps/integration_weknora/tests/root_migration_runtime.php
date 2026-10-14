<?php
declare(strict_types=1);
if(getenv('WEKNORA_PRD_TEST_RUNTIME')!=='1'){throw new RuntimeException('Disposable runtime required');}
define('OC_CONSOLE',1);require '/var/www/html/lib/base.php';set_exception_handler(static function(Throwable $e):void{fwrite(STDERR,(string)$e."\n");exit(1);});
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\RootMigrationService;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;
use OCA\IntegrationWeknora\Service\IdentityMappingService;
function assertMove(bool $ok,string $message):void{if(!$ok){throw new RuntimeException($message);}}
function moveConflict(callable $fn):void{try{$fn();}catch(DomainException|UnexpectedValueException $e){return;}throw new RuntimeException('Expected stale/mismatched move to fail closed');}
putenv('WEKNORA_EVENT_DEV_HTTP=1');putenv('WEKNORA_LOCAL_COMPOSE=1');putenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=1');putenv('WEKNORA_EVENT_ALLOWED_ORIGINS=http://test-weknora');
$app=new Application();$services=$app->getContainer();$db=\OCP\Server::get(\OCP\IDBConnection::class);
\OC_App::loadApp('user_ldap');
$uid='auto-prd-admin';$home=\OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid);
$scope=$home->newFolder('root-move-'.bin2hex(random_bytes(4)));$root=$scope->newFolder('Original root');$file=$root->newFile('original.txt','Unchanged source bytes during root migration.');$etag=$file->getEtag();$fileId=$file->getId();
$dirs=$services->get(DirectoryPublicationService::class);$bindings=$services->get(BindingRegistryService::class);$pairs=$services->get(SourcePairingRegistryService::class);$moves=$services->get(RootMigrationService::class);
$rule=['id'=>'move_rule_'.bin2hex(random_bytes(4)),'owner_uid'=>$uid,'container_file_id'=>$scope->getId(),'tenant_id'=>'7','processing_template_id'=>'move-template','weknora_origin'=>'http://test-weknora','base_url'=>'http://test-nextcloud','discovery'=>'direct_children','delegates'=>[$uid],'auto_enabled'=>false];$dirs->saveRule($rule,0);
$op=DirectoryPublicationService::uuid();$kbID='move-kb-'.$op;$dsID='move-source-'.$op;$pending=$dirs->associate($uid,$root->getId(),['operation_id'=>$op,'mode'=>'existing','name'=>'Original root','knowledge_base_id'=>$kbID],false);$binding='dir_'.str_replace('-','',$op);$bindings->save($binding,'Original root',$uid,$root->getId());
$prepared=$pairs->prepare($binding,$op,'7',$kbID,$uid);$pair=$prepared['pairing'];$pairs->commit($binding,$pair['key_id'],$op,$pair['instance_id'],'7',$kbID,$dsID);
$oldHash=SourcePairingRegistryService::rootHash($root);$oldId=$root->getId();$moves->capture($root);$moves->capture($root);
$db=\OCP\Server::get(\OCP\IDBConnection::class);$q=$db->getQueryBuilder();$r=$q->select('*')->from('weknora_root_move')->where($q->expr()->eq('binding_id',$q->createNamedParameter($binding)))->executeQuery();try{$rows=$r->fetchAllAssociative();}finally{$r->closeCursor();}
assertMove(count($rows)===1,'Concurrent/repeated before-rename capture created duplicate root operation');$migration=$rows[0]['operation_id'];
assertMove($bindings->listBindings()[array_search($binding,array_column($bindings->listBindings(),'id'))]['publication_state']==='stopped','Root reads were not stopped before the filesystem operation');
$root->move($scope->getPath().'/Renamed root');$renamed=$scope->get('Renamed root');$file=$renamed->get('original.txt');$moves->observe();$proof=$moves->proof($binding,$migration);
assertMove($renamed->getId()===$oldId&&$file->getId()===$fileId&&$file->getEtag()===$etag&&$file->getContent()==='Unchanged source bytes during root migration.','Root migration modified source identity, bytes or ETag');
assertMove($proof['old_root_hash']===$oldHash&&$proof['new_root_hash']!==$oldHash&&$proof['old_root_path']!==$proof['new_root_path'],'Migration did not preserve its exact old/new root proof');
moveConflict(fn()=>$bindings->requireActiveRoot($binding));$bad=$proof;$bad['knowledge_base_id']='wrong-kb';moveConflict(fn()=>$moves->acknowledge($binding,$migration,$bad));
// Crash/ACK-loss simulation: the source is still stopped and the exact durable
// proof can be fetched again before acknowledgement, then ACK can be repeated.
assertMove($moves->proof($binding,$migration)===$proof,'Observed proof changed after an ACK-less restart');
$ack=$moves->acknowledge($binding,$migration,$proof);$again=$moves->acknowledge($binding,$migration,$proof);
assertMove($ack['state']==='finalized'&&$again===$ack,'Lost final ACK did not recover idempotently');$current=$pairs->status($binding);
assertMove($current['operation_id']===$op&&$current['knowledge_base_id']===$kbID&&$current['data_source_id']===$dsID,'Root migration replaced the dedicated KB or source');
assertMove($bindings->requireActiveRoot($binding)->getId()===$oldId,'Moved root did not resume with its same identity');assertMove($bindings->requirePublicationActive($binding)===(int)$proof['resume_epoch'],'Root resume used the wrong publication generation');
// A second late physical move invalidates the first proof instead of accepting
// a cached ACK and silently adopting another boundary.
$moves->capture($renamed);$renamed->move($scope->getPath().'/Moved again');$moves->observe();moveConflict(fn()=>$moves->proof($binding,$migration));moveConflict(fn()=>$moves->acknowledge($binding,$migration,$proof));
echo "Root rename retained KB/source/file IDs and bytes; stopped-before-move, exact signed tuple contract, ACK-loss retry and late-move rejection passed\n";
