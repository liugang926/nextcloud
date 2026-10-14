<?php
declare(strict_types=1);
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE',1);require '/var/www/html/lib/base.php';
set_exception_handler(static function(Throwable $e):void{fwrite(STDERR,(string)$e."\n");exit(1);});
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\ControlRecoveryService;
use OCA\IntegrationWeknora\Service\SourceContentRevisionService;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
function checkRecovery(bool $ok,string $why):void{if(!$ok){throw new RuntimeException($why);}}
function denyRecovery(callable $fn):void{try{$fn();}catch(DomainException|\OCA\IntegrationWeknora\Service\BindingPublicationStoppedException $e){return;}throw new RuntimeException('Recovery must remain closed');}
putenv('WEKNORA_EVENT_DEV_HTTP=1');putenv('WEKNORA_LOCAL_COMPOSE=1');putenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=1');\OC_App::loadApp('user_ldap');
$app=new Application();$c=$app->getContainer();$db=\OCP\Server::get(\OCP\IDBConnection::class);
$controls=$c->get(ControlRecoveryService::class);$revisions=$c->get(SourceContentRevisionService::class);$bindings=$c->get(BindingRegistryService::class);
// All closure and write operations are rolled back: the shared disposable
// runtime remains available to concurrent LDAP/component tests.
$db->beginTransaction();try{
 $fileId=random_int(1500000000,2000000000);$floor=19;
 $db->insertIgnoreConflict('weknora_source_file_rev',['file_id'=>$fileId,'revision'=>3,'writing'=>1,'updated_at'=>time()]);
 $db->insertIgnoreConflict('weknora_recovery_floor',['file_id'=>$fileId,'revision'=>$floor,'writing'=>1,'verified_at'=>time(),'plan_sha256'=>str_repeat('a',64)]);
 denyRecovery(fn()=>$revisions->current($fileId));
 denyRecovery(fn()=>$revisions->complete($fileId,[]));
 $q=$db->getQueryBuilder();$r=$q->select('revision','writing')->from('weknora_source_file_rev')->where($q->expr()->eq('file_id',$q->createNamedParameter($fileId)))->executeQuery();try{$raw=$r->fetchAssociative();}finally{$r->closeCursor();}
 checkRecovery((int)$raw['revision']===3&&(int)$raw['writing']===1,'Stale completion restamped restored bytes');
 $revisions->begin($fileId,[]);denyRecovery(fn()=>$revisions->current($fileId));$revisions->complete($fileId,[]);
 checkRecovery($revisions->current($fileId)==='20','A genuine new write did not advance beyond the retained floor');
 $before=$controls->metadataInventory();checkRecovery(isset($before['tables']['weknora_root_move'],$before['tables']['weknora_target_move'],$before['tables']['weknora_source_file_rev']),'Recovery inventory omitted migration/content protection');
 $controls->closeFromPlan(['latest_content_revisions'=>[['binding_id'=>'recovery-fixture','file_id'=>(string)$fileId,'revision'=>'21','writing'=>true]]],$before['sha256']);
 checkRecovery($controls->closed()&&$controls->floor($fileId)['revision']===21,'External content facts failed to close controls');
 denyRecovery(fn()=>$revisions->current($fileId));denyRecovery(fn()=>$bindings->requirePublicationActive('recovery-fixture'));denyRecovery(fn()=>$bindings->setPublicationState('recovery-fixture','active','recovery-test'));
 $controls->closeFromPlan(['latest_content_revisions'=>[['binding_id'=>'recovery-fixture','file_id'=>(string)$fileId,'revision'=>'20','writing'=>false]]]);
 checkRecovery($controls->floor($fileId)['revision']===21&&$controls->floor($fileId)['writing'],'An older plan lowered the content floor');
 $after=$controls->metadataInventory();checkRecovery($after['closed']&&$after['sha256']!==$before['sha256'],'Protective disable state missing from metadata inventory');

 // Positive local maintenance phase uses an isolated current control view
 // within this transaction. Other test processes see the committed view.
 $home=\OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder('auto-prd-admin');$root=$home->newFolder('recovery-control-'.bin2hex(random_bytes(4)));$binding='recovery_'.bin2hex(random_bytes(6));$bindings->save($binding,$root->getName(),'auto-prd-admin',$root->getId());$bindings->setPublicationState($binding,'stopped','auto-prd-admin');
 $q=$db->getQueryBuilder();$q->update('appconfig')->set('configvalue',$q->createNamedParameter(json_encode([['id'=>$binding,'name'=>$root->getName(),'owner_uid'=>'auto-prd-admin','root_file_id'=>$root->getId()]],JSON_THROW_ON_ERROR)))->where($q->expr()->eq('appid',$q->createNamedParameter('integration_weknora')))->andWhere($q->expr()->eq('configkey',$q->createNamedParameter('bindings')))->executeStatement();
 $q=$db->getQueryBuilder();$r=$q->select('stream_id','sequence','chain_sha256')->from('weknora_recovery_head')->where($q->expr()->eq('id',$q->createNamedParameter(1)))->executeQuery();try{$head=$r->fetchAssociative();}finally{$r->closeCursor();}
 $tail=['instance_id'=>\OCP\Server::get(\OCP\IConfig::class)->getSystemValueString('instanceid'),'stream_id'=>$head['stream_id'],'through_sequence'=>(string)$head['sequence'],'through_database_chain_sha256'=>$head['chain_sha256']];
 $q=$db->getQueryBuilder();$r=$q->select('plan_sha256')->from('weknora_control_gate')->where($q->expr()->eq('id',$q->createNamedParameter(1)))->executeQuery();try{$original=$r->fetchOne();}finally{$r->closeCursor();}
 $configDir=sys_get_temp_dir().'/weknora-control-config-'.bin2hex(random_bytes(6));mkdir($configDir,0700);file_put_contents($configDir.'/config.php','<?php $CONFIG='.var_export(['maintenance'=>true,'instanceid'=>$tail['instance_id']],true).';');chmod($configDir.'/config.php',0600);
 try{
  $reconciler=new \OCA\IntegrationWeknora\Service\ControlRecoveryReconciler($db,new \OC\AllConfig(new \OC\SystemConfig(new \OC\Config($configDir.'/'))),$controls,$bindings,$c->get(\OCA\IntegrationWeknora\Service\IdentityMappingService::class),\OCP\Server::get(\OCP\IGroupManager::class),$revisions,$c->get(\OCA\IntegrationWeknora\Service\DirectoryGrantSnapshotService::class));
  $inventory=$controls->metadataInventory();checkRecovery($inventory['missing_tables']===[],'Expected complete recovery migration schema');
  $db->executeStatement('SAVEPOINT missing_control_table');try{$db->executeStatement('ALTER TABLE *PREFIX*weknora_group_identity RENAME TO missing_control_fixture');$missing=$controls->metadataInventory();checkRecovery(in_array('weknora_group_identity',$missing['missing_tables'],true),'Missing control table was silently accepted');denyRecovery(fn()=>$reconciler->reconcile($tail,$original,$missing['sha256'],'auto-prd-admin'));}finally{$db->executeStatement('ROLLBACK TO SAVEPOINT missing_control_table');$db->executeStatement('RELEASE SAVEPOINT missing_control_table');}
  $bad=$tail;$bad['through_sequence']='0';denyRecovery(fn()=>$reconciler->reconcile($bad,$original,$inventory['sha256'],'auto-prd-admin'));denyRecovery(fn()=>$reconciler->reconcile($tail,$original,str_repeat('f',64),'auto-prd-admin'));
  $result=$reconciler->reconcile($tail,$original,$inventory['sha256'],'auto-prd-admin');checkRecovery($result['sources_remain_stopped']&&!$controls->closed(),'Fresh private control reconciliation did not release only the global barrier');denyRecovery(fn()=>$bindings->requirePublicationActive($binding));
  $before=$bindings->listBindings()[0]['publication_epoch'];$bindings->setPublicationState($binding,'active','auto-prd-admin');checkRecovery($bindings->requirePublicationActive($binding)>$before,'Explicit fresh source resume did not create a new publication generation');checkRecovery($controls->floor($fileId)['revision']===21&&$controls->floor($fileId)['writing'],'Positive control reconciliation erased a retained content fence');
  echo "Fresh maintenance control reconciliation requires exact external tail and current inventory, leaves source readers stopped until explicit verified resume, preserves content floors; positive staged recovery passed\n";
 }finally{unlink($configDir.'/config.php');rmdir($configDir);}
 echo "Recovery typed floors reject stale completion, real next writes exceed floor, metadata inventory includes root/target state, global source/KB metadata/resume gates stay closed; rollback isolation passed\n";
}finally{$db->rollBack();}
