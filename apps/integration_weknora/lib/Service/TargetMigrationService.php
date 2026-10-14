<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;
use OCP\IConfig;
use OCP\IDBConnection;
use OCP\IGroupManager;
use OCP\Files\IRootFolder;
use OCP\Files\Folder;
use OCP\IUserManager;

/** One-way target handoff; old resources stay attributed to their old source. */
final class TargetMigrationService {
 public function __construct(private IDBConnection $db,private IConfig $config,private BindingRegistryService $bindings,
  private DirectoryOnboardingClient $remote,private IRootFolder $files,private IGroupManager $groups,
  private IdentityMappingService $identities,private PublicationAclPolicyService $acl,
  private IUserManager $users,private SourceAudienceVerifier $audience) {}
 public function begin(array $oldIntent,array $newRequest,array $rule,string $actorUid,string $migrationId,?array $oldRule=null): array {
  if(!SourcePairingRegistryService::validOperationId($migrationId)){throw new \InvalidArgumentException('Invalid target migration');}
  $migrationId=strtolower($migrationId);
  $this->db->beginTransaction();try {
   $this->lock();$old=$this->one($migrationId);
   if($old!==null){$stored=json_decode($old['request_json'],true,32,JSON_THROW_ON_ERROR);$wanted=$newRequest;unset($wanted['operation_id'],$wanted['binding_id']);$actual=$stored['request'];unset($actual['operation_id'],$actual['binding_id']);
    if($old['old_operation_id']!==$oldIntent['operation_id']||$old['actor_uid']!==$actorUid||$wanted!==$actual){throw new \DomainException('Target migration intent changed');}
    $this->db->commit();$this->attempt($old);return $this->publicRow($this->one($migrationId));}
   foreach($this->rows() as $other){if($other['old_binding_id']===$oldIntent['binding_id']&&$other['state']!=='active'){throw new \DomainException('Source already has a target migration');}}
   $op=DirectoryPublicationService::uuid();$binding='dir_'.str_replace('-','',$op);$newRequest['operation_id']=$op;$newRequest['binding_id']=$binding;
   $oldRequest=json_decode($oldIntent['request_json'],true,32,JSON_THROW_ON_ERROR);
   $payload=['request'=>$newRequest,'old_request'=>$oldRequest,'rule'=>$rule,'old_rule'=>$oldRule??$rule,'owner_uid'=>$rule['owner_uid'],'actor_directory_id'=>$newRequest['actor_directory_id'],'actor_object_guid'=>$newRequest['actor_object_guid']];
   $row=['operation_id'=>strtolower($migrationId),'old_operation_id'=>$oldIntent['operation_id'],'new_operation_id'=>$op,
    'old_binding_id'=>$oldIntent['binding_id'],'new_binding_id'=>$binding,'root_key'=>$oldIntent['root_key'],'rule_id'=>$rule['id'],
    'actor_uid'=>$actorUid,'root_file_id'=>$oldIntent['root_file_id'],'old_epoch'=>0,'revision'=>1,'state'=>'prepared',
    'request_json'=>json_encode($payload,JSON_THROW_ON_ERROR),'created_at'=>time(),'updated_at'=>time()];
   if($this->db->insertIgnoreConflict('weknora_target_move',$row)!==1){throw new \DomainException('Target migration ID already used');}
   $this->db->commit();$this->attempt($row);return $this->publicRow($this->one($migrationId));
  }catch(\Throwable $e){if($this->db->inTransaction()){$this->db->rollBack();}throw $e;}
 }
 public function existingChoice(string $id,int $rootId): ?array { $row=$this->one(strtolower($id));if($row===null){return null;}if((int)$row['root_file_id']!==$rootId){throw new \DomainException('Target migration does not belong to this root');}$payload=json_decode($row['request_json'],true,32,JSON_THROW_ON_ERROR);return $payload['request']; }
 public function status(string $id,int $rootId): array { $row=$this->one(strtolower($id));if($row===null){throw new TargetMigrationNotFoundException('Target migration was not found');}if((int)$row['root_file_id']!==$rootId){throw new \DomainException('Target migration does not belong to this root');}return $this->publicRow($row); }
 public function retry(string $id,int $rootId,array $actor,?string $actorUid=null): array { $row=$this->one(strtolower($id));if($row===null){throw new TargetMigrationNotFoundException('Target migration was not found');}if((int)$row['root_file_id']!==$rootId){throw new \DomainException('Target migration does not belong to this root');}try{$this->process($row,$actor,$actorUid);}catch(\RuntimeException $e){}return $this->publicRow($this->one($id)); }
 public function managerForOperation(string $operationId): ?string {$q=$this->db->getQueryBuilder();$r=$q->select('actor_uid')->from('weknora_target_move')->where($q->expr()->eq('new_operation_id',$q->createNamedParameter($operationId)))->andWhere($q->expr()->in('state',$q->createNamedParameter(['building','active'],\OCP\DB\QueryBuilder\IQueryBuilder::PARAM_STR_ARRAY)))->setMaxResults(1)->executeQuery();try{$uid=$r->fetchOne();return is_string($uid)?$uid:null;}finally{$r->closeCursor();}}
 public function originalRule(string $id,int $rootId): string {$row=$this->one(strtolower($id));if($row===null){throw new TargetMigrationNotFoundException('Target migration was not found');}if((int)$row['root_file_id']!==$rootId){throw new \DomainException('Target migration does not belong to this root');}$payload=json_decode($row['request_json'],true,32,JSON_THROW_ON_ERROR);return ($payload['old_rule']??$payload['rule'])['id'];}
 public function reconcile(): void {foreach($this->rows() as $row){if($row['state']==='active'){continue;}try{$this->process($row);}catch(\Throwable $e){/* Durable progress retains originals and the exact target. */}}}
 private function attempt(array $row): void {
  try{$this->process($row);}catch(\RuntimeException $e){/* Unknown remote outcomes retain the exact durable migration for retry. */}
 }
 private function process(array $row,?array $currentActor=null,?string $actorUid=null): void {
  DirectoryTargetClosureService::requireOpen($this->db,$row['old_binding_id']);
  DirectoryTargetClosureService::requireOpen($this->db,$row['new_binding_id']);
  $payload=json_decode($row['request_json'],true,32,JSON_THROW_ON_ERROR);$request=$payload['request'];
  $actorUid=$actorUid??$row['actor_uid'];
  try{[$newRule,$oldRule,$actor]=$this->currentAuthority($row,$payload,$actorUid,$currentActor);}
  catch(\Throwable $e){$this->closeUnverified($row);throw $e;}
  if($actorUid!==$row['actor_uid']){$this->update($row['operation_id'],['actor_uid'=>$actorUid]);$row['actor_uid']=$actorUid;}
  if($row['state']==='prepared') {
   // ACK loss may already have reserved this immutable operation under its
   // first creator. Management is checked using the current verified actor.
   try{$reserved=$this->remote->request($newRule,'GET','/operations/'.$row['new_operation_id'],$actor);}
   catch(\DomainException $e){if($e->getCode()!==404){throw $e;}$reserveRequest=array_replace($request,$actor);$reserved=$this->remote->request($newRule,'POST','/operations',$reserveRequest);}
   if(($reserved['operation']['operation_id']??null)!==$row['new_operation_id']||!SourcePairingRegistryService::validRemoteId($reserved['operation']['knowledge_base_id']??null)){throw new \RuntimeException('New target reservation is unverified');}
   // Stop the old root before the target retires its read/build/GC generation.
   try{[$newRule,$oldRule,$actor]=$this->currentAuthority($row,$payload,$actorUid,$actor);}catch(\Throwable $e){$this->closeUnverified($row);throw $e;}
   $stopped=$this->bindings->setPublicationState($row['old_binding_id'],'stopped',$actorUid);if($stopped===null){throw new \DomainException('Old source binding disappeared');}
   $this->update($row['operation_id'],['state'=>'reserved','old_epoch'=>$stopped['publication_epoch']]);$row['state']='reserved';$row['old_epoch']=$stopped['publication_epoch'];
  }
  if($row['state']==='reserved') {
   try{[$newRule,$oldRule,$actor]=$this->currentAuthority($row,$payload,$actorUid,$actor);}catch(\Throwable $e){$this->closeUnverified($row);throw $e;}
   $ack=$this->remote->request($oldRule,'POST','/operations/'.$row['old_operation_id'].'/target-migrations',
    ['operation_id'=>$row['operation_id'],'new_operation_id'=>$row['new_operation_id']]+$actor);
   $migration=$ack['migration']??null;
   if(!is_array($migration)||($migration['operation_id']??null)!==$row['operation_id']||($migration['old_operation_id']??null)!==$row['old_operation_id']||($migration['new_operation_id']??null)!==$row['new_operation_id']||($migration['new_binding_id']??null)!==$row['new_binding_id']||($migration['state']??null)!=='building'){throw new \RuntimeException('Old target retirement ACK is unverified');}
   try{[$newRule,$oldRule,$actor]=$this->currentAuthority($row,$payload,$actorUid,$actor);}catch(\Throwable $e){$this->closeUnverified($row);throw $e;}
   $this->transferLocal($row,$payload);$row['state']='building';
  }
  if($row['state']==='building') {
   try{[$newRule,$oldRule,$actor]=$this->currentAuthority($row,$payload,$actorUid,$actor);}catch(\Throwable $e){$this->closeUnverified($row);throw $e;}
   $ack=$this->remote->request($oldRule,'POST','/operations/'.$row['old_operation_id'].'/target-migrations/'.$row['operation_id'].'/finalize',$actor);
   $migration=$ack['migration']??null;
   if(is_array($migration)&&($migration['operation_id']??null)===$row['operation_id']&&($migration['new_operation_id']??null)===$row['new_operation_id']&&($migration['state']??null)==='active'&&!isset($ack['error'])){try{$this->currentAuthority($row,$payload,$actorUid,$actor);}catch(\Throwable $e){$this->closeUnverified($row);throw $e;}$this->update($row['operation_id'],['state'=>'active']);}
  }
 }
 private function currentAuthority(array $row,array $payload,string $uid,?array $expected): array {
  $rules=[];foreach([$payload['rule'],($payload['old_rule']??$payload['rule'])] as $definition){$q=$this->db->getQueryBuilder();$r=$q->select('config_json')->from('weknora_dir_rules')->where($q->expr()->eq('rule_id',$q->createNamedParameter($definition['id'])))->executeQuery();try{$raw=$r->fetchOne();}finally{$r->closeCursor();}if(!is_string($raw)){throw new \DomainException('Current publication rule is unavailable');}$rule=json_decode($raw,true,32,JSON_THROW_ON_ERROR);if(!$this->groups->isAdmin($uid)&&!in_array($uid,$rule['delegates'],true)){throw new \DomainException('Current NC publication delegate was revoked');}$rules[]=$rule;}
  $identity=$this->identities->forUser($uid);$actor=['actor_directory_id'=>$identity['directory_id'],'actor_object_guid'=>$identity['object_guid']];
  if($expected!==null&&(($expected['actor_directory_id']??null)!==$actor['actor_directory_id']||($expected['actor_object_guid']??null)!==$actor['actor_object_guid'])){throw new \DomainException('Current migration actor identity changed');}
  $root=$this->readable($rules[0]['owner_uid'],(int)$row['root_file_id']);$container=$this->readable($rules[0]['owner_uid'],(int)$rules[0]['container_file_id']);
  if($rules[0]['owner_uid']!==$payload['owner_uid']||$root->getId()===$container->getId()||!hash_equals($row['root_key'],DirectoryPublicationService::physicalKey($root))||!DirectoryPublicationService::contains($container,$root)||($rules[0]['discovery']==='direct_children'&&$root->getParent()->getId()!==$container->getId())){throw new \DomainException('Source root left its current target rule');}
  $this->readable($uid,$root->getId());$this->acl->assertTreeSupported($root);
  $user=$this->users->get($uid);if($user===null||!$user->isEnabled()){throw new \DomainException('Current migration actor is disabled');}
  if($user->getBackendClassName()==='LDAP'){$this->audience->invalidate();if(!$this->audience->allows($root,$rules[0]['owner_uid'],$user,$actor['actor_directory_id'],$actor['actor_object_guid'])){throw new \DomainException('Current actor lost source audience access');}}
  return [$this->targetRule($rules[0],(string)$payload['request']['tenant_id'],(string)$payload['request']['processing_template_id']),$this->targetRule($rules[1],(string)$payload['old_request']['tenant_id'],(string)$payload['old_request']['processing_template_id']),$actor];
 }
 private function readable(string $uid,int $id): Folder {$view=$this->files->getUserFolder($uid);foreach($view->getById($id) as $node){if($node instanceof Folder&&$node->getId()===$id&&$view->isSubNode($node)&&$node->isReadable()){return $node;}}throw new \DomainException('Current actor cannot read the source root');}
 private function closeUnverified(array $row): void {foreach($this->bindings->listBindings() as $binding){if(in_array($binding['id'],[$row['old_binding_id'],$row['new_binding_id']],true)&&(int)$binding['root_file_id']===(int)$row['root_file_id']){try{$this->bindings->setPublicationState($binding['id'],'stopped','target-migration-policy');}catch(\Throwable $ignored){}}}}
 private function transferLocal(array $row,array $payload): void {
  $this->db->beginTransaction();try {
   $this->lock();DirectoryTargetClosureService::requireOpen($this->db,$row['old_binding_id']);DirectoryTargetClosureService::requireOpen($this->db,$row['new_binding_id']);$current=$this->one($row['operation_id']);if($current===null||$current['state']!=='reserved'){if($current!==null&&in_array($current['state'],['building','active'],true)){$this->db->commit();return;}throw new \DomainException('Target migration phase changed');}
   $all=$this->bindings->listBindings();$kept=[];$old=null;
   foreach($all as $binding){if($binding['id']===$row['old_binding_id']){$old=$binding;continue;}$kept[]=['id'=>$binding['id'],'name'=>$binding['name'],'owner_uid'=>$binding['owner_uid'],'root_file_id'=>$binding['root_file_id']];}
   if($old===null||$old['publication_state']!=='stopped'||$old['publication_epoch']!==(int)$row['old_epoch']||$old['root_file_id']!==(int)$row['root_file_id']){throw new \DomainException('Old source retirement fence changed');}
   $q=$this->db->getQueryBuilder();$q->update('weknora_binding_id')->set('retired_at',$q->createNamedParameter(time()))->where($q->expr()->eq('binding_id',$q->createNamedParameter($row['old_binding_id'])))->andWhere($q->expr()->eq('retired_at',$q->createNamedParameter(0)))->executeStatement();
   $this->config->setAppValue('integration_weknora','bindings',json_encode($kept,JSON_THROW_ON_ERROR));
   foreach(['weknora_machine_key','weknora_event_conn'] as $table){$q=$this->db->getQueryBuilder();if($table==='weknora_machine_key'){$q->delete($table);}else{$q->update($table)->set('status',$q->createNamedParameter('retired'));}$q->where($q->expr()->eq('binding_id',$q->createNamedParameter($row['old_binding_id'])))->executeStatement();}
   $q=$this->db->getQueryBuilder();$q->update('weknora_src_pair')->set('state',$q->createNamedParameter('retired'))->where($q->expr()->eq('operation_id',$q->createNamedParameter($row['old_operation_id'])))->executeStatement();
   $q=$this->db->getQueryBuilder();$q->update('weknora_dir_intent')->set('state',$q->createNamedParameter('retired'))
    ->set('root_key',$q->createNamedParameter(hash('sha256','retired-source'."\0".$row['old_operation_id']."\0".$row['root_key'])))
    ->set('lease_until',$q->createNamedParameter(PHP_INT_MAX))->set('updated_at',$q->createNamedParameter(time()))
    ->where($q->expr()->eq('operation_id',$q->createNamedParameter($row['old_operation_id'])))->executeStatement();
   $request=$payload['request'];$intent=['operation_id'=>$row['new_operation_id'],'binding_id'=>$row['new_binding_id'],
    'root_key'=>$row['root_key'],'rule_id'=>$row['rule_id'],'owner_uid'=>$payload['owner_uid'],'actor_uid'=>$row['actor_uid'],
    'root_file_id'=>$row['root_file_id'],'parent_file_id'=>0,'folder_name'=>$request['name'],'request_json'=>json_encode($request,JSON_THROW_ON_ERROR),
    'state'=>'pending','error_code'=>'','permission_version'=>0,'revision'=>1,'attempt_count'=>0,'next_attempt_at'=>0,'lease_until'=>0,'created_at'=>time(),'updated_at'=>time()];
   if($this->db->insertIgnoreConflict('weknora_dir_intent',$intent)!==1){throw new \DomainException('New physical source intent is occupied');}
   PublicationRecoveryLedger::appendInTransaction($this->db,$row['old_binding_id'],(int)$row['root_file_id'],'binding_retired',(int)$row['old_epoch']);
   $this->update($row['operation_id'],['state'=>'building']);$this->db->commit();
  }catch(\Throwable $e){$this->db->rollBack();throw $e;}
 }
 private function targetRule(array $rule,string $tenant,string $template): array {
  foreach($rule['targets']??[['tenant_id'=>$rule['tenant_id'],'processing_template_id'=>$rule['processing_template_id']]] as $target){if($target['tenant_id']===$tenant&&$target['processing_template_id']===$template){return array_replace($rule,$target);}}
  throw new \DomainException('Migration target scope is not allowed');
 }
 private function one(string $id): ?array {$q=$this->db->getQueryBuilder();$r=$q->select('*')->from('weknora_target_move')->where($q->expr()->eq('operation_id',$q->createNamedParameter($id)))->executeQuery();try{$row=$r->fetchAssociative();return $row===false?null:$row;}finally{$r->closeCursor();}}
 private function rows(): array {$q=$this->db->getQueryBuilder();$r=$q->select('*')->from('weknora_target_move')->orderBy('created_at')->setMaxResults(100)->executeQuery();try{return $r->fetchAllAssociative();}finally{$r->closeCursor();}}
 private function update(string $id,array $fields): void {$q=$this->db->getQueryBuilder();$q->update('weknora_target_move')->where($q->expr()->eq('operation_id',$q->createNamedParameter($id)));foreach($fields+['updated_at'=>time()] as $name=>$value){$q->set($name,$q->createNamedParameter($value));}$q->executeStatement();}
 private function lock(): void {$this->db->insertIgnoreConflict('weknora_bind_lock',['id'=>1]);$q=$this->db->getQueryBuilder();$r=$q->select('id')->from('weknora_bind_lock')->where($q->expr()->eq('id',$q->createNamedParameter(1)))->forUpdate()->executeQuery();try{if($r->fetchOne()===false){throw new \RuntimeException('Binding lock unavailable');}}finally{$r->closeCursor();}}
 private function publicRow(?array $row): array {if($row===null){throw new \RuntimeException('Target migration unavailable');}return array_intersect_key($row,array_flip(['operation_id','old_operation_id','new_operation_id','old_binding_id','new_binding_id','state','revision']));}
}
