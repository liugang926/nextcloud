<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;

use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\IConfig;
use OCP\IDBConnection;

/** A stopped, immutable source move; the pair/KB/source identity never changes. */
final class RootMigrationService {
 public function __construct(private IDBConnection $db,private IRootFolder $files,private IConfig $config,
  private BindingRegistryService $bindings,private PublicationAclPolicyService $acl,
  private DirectoryOnboardingClient $remote,private ChangeOutboxService $changes) {}

 public static function storageIdentity(Folder $root): string {return hash('sha256','nextcloud-supported-storage-v1'."\0".$root->getStorage()->getId());}

 /** Called before a successful local root/ancestor rename can change its hash. */
 public function capture(Folder $source): void {
  foreach($this->bindings->listBindings() as $binding){
   $root=$this->root($binding['owner_uid'],$binding['root_file_id']);
   if(!DirectoryPublicationService::contains($source,$root)){continue;}
   $pair=$this->pair($binding['id']);$intent=$this->intent($binding['id']);
   if($pair===null||$intent===null||$pair['state']!=='active'){continue;}
   $this->prepare($binding,$pair,$intent,$root);
  }
 }

 private function prepare(array $binding,array $pair,array $intent,Folder $root,bool $forceResume=false): array {
  $this->db->beginTransaction();
  try {
   $this->lock();
   if(($pending=$this->pending($binding['id']))!==null){$this->db->commit();return $pending;}
   if(!hash_equals($pair['root_hash'],SourcePairingRegistryService::rootHash($root))){throw new \DomainException('Original physical root proof is unavailable');}
   $this->sameRule($intent,$root);$this->acl->assertSupported($root);
   $active=$binding['publication_state']==='active'||$forceResume;
   $stopped=$this->bindings->setPublicationState($binding['id'],'stopped','root-migration');
   if($stopped===null){throw new \DomainException('Root binding disappeared');}
   PublicationRecoveryLedger::appendInTransaction($this->db,$binding['id'],$root->getId(),'reconcile',$stopped['publication_epoch']);
   $head=$this->head();$now=time();
   $row=['operation_id'=>DirectoryPublicationService::uuid(),'pair_operation_id'=>$pair['operation_id'],
    'binding_id'=>$binding['id'],'instance_id'=>$pair['instance_id'],'tenant_id'=>$pair['tenant_id'],
    'knowledge_base_id'=>$pair['knowledge_base_id'],'data_source_id'=>$pair['data_source_id'],
    'rule_id'=>$intent['rule_id'],'owner_uid'=>$binding['owner_uid'],'actor_uid'=>$intent['actor_uid'],
    'root_file_id'=>$root->getId(),'physical_root_key'=>DirectoryPublicationService::physicalKey($root),
    'storage_identity'=>self::storageIdentity($root),'old_root_hash'=>$pair['root_hash'],'new_root_hash'=>'','old_root_path'=>$root->getPath(),'new_root_path'=>'',
    'old_epoch'=>$binding['publication_epoch'],'prepared_epoch'=>$stopped['publication_epoch'],
    'resume_epoch'=>$stopped['publication_epoch']+($active?1:0),'was_active'=>$active?1:0,
    'journal_stream_id'=>$head['stream_id'],'journal_sequence'=>$head['sequence'],'journal_chain_sha256'=>$head['chain_sha256'],
    'state'=>'prepared','created_at'=>$now,'updated_at'=>$now];
   if($this->db->insertIgnoreConflict('weknora_root_move',$row)!==1){throw new \DomainException('Root migration ID conflict');}
   $this->db->commit();return $row;
  }catch(\Throwable $e){$this->db->rollBack();throw $e;}
 }

 /** Node operation HTTP does no remote request; cron performs the handshake. */
 public function observe(): void {
  foreach($this->rows(['prepared','observed']) as $row){
   try {$this->observeRow($row);}catch(\Throwable $e){/* Keep source stopped; never adopt an unknown mount. */}
  }
 }
 private function observeRow(array $row, bool $allowUnchanged = false): array {
  $root=$this->root($row['owner_uid'],(int)$row['root_file_id']);$this->assertPhysical($row,$root);
  $new=SourcePairingRegistryService::rootHash($root);
  if($row['state']==='observed'){
   if(!hash_equals($row['new_root_hash'],$new)){throw new \DomainException('Root changed after migration observation');}
   return $row;
  }
  if(hash_equals($row['old_root_hash'],$new) && !$allowUnchanged){return $row;}
  $this->db->beginTransaction();try {
   $this->lock();$current=$this->one($row['operation_id']);
   if($current===null||$current['state']!=='prepared'){throw new \DomainException('Root migration changed');}
   $this->assertPhysical($current,$root);$this->assertStopped($current);
   PublicationRecoveryLedger::appendInTransaction($this->db,$row['binding_id'],$root->getId(),'reconcile',(int)$row['prepared_epoch']);
   $head=$this->head();$fields=['new_root_hash'=>$new,'new_root_path'=>$root->getPath(),'state'=>'observed','journal_stream_id'=>$head['stream_id'],
    'journal_sequence'=>$head['sequence'],'journal_chain_sha256'=>$head['chain_sha256'],'updated_at'=>time()];
   $this->update($row['operation_id'],$fields);$this->db->commit();return array_replace($current,$fields);
  }catch(\Throwable $e){$this->db->rollBack();throw $e;}
 }
 public function reconcile(): void {
  $this->observe();
  foreach($this->rows(['observed']) as $row){try {
   $rule=$this->sameRule($row,$this->root($row['owner_uid'],(int)$row['root_file_id']));
   $this->remote->request($rule,'POST','/operations/'.$row['pair_operation_id'].'/root-migrations',['operation_id'=>$row['operation_id']]);
  }catch(\Throwable $e){/* Unknown ACK preserves exactly this stopped operation. */}}
 }


 public function retryUnchanged(string $binding,array $actor=[]): void {
  $row=$this->pending($binding);
  if($row!==null&&$row['state']==='prepared'&&$this->supersedeStalePrepared($row)){$row=null;}
  if($row===null){
   $pair=$this->pair($binding);$intent=$this->intent($binding);
   if($pair===null||$intent===null||$pair['state']!=='active'){return;}
   foreach($this->bindings->listBindings() as $current){if($current['id']===$binding&&$current['publication_state']==='stopped'){
    $row=$this->prepare($current,$pair,$intent,$this->root($current['owner_uid'],$current['root_file_id']),true);break;
   }}
   if($row===null){return;}
  }
  if($row['state']==='prepared'){$row=$this->observeRow($row,true);}
  $rule=$this->sameRule($row,$this->root($row['owner_uid'],(int)$row['root_file_id']));
  $this->remote->request($rule,'POST','/operations/'.$row['pair_operation_id'].'/root-migrations',
   ['operation_id'=>$row['operation_id']]+($actor===[]?[]:['actor_directory_id'=>$actor['directory_id'],'actor_object_guid'=>$actor['object_guid']]));
 }
 private function supersedeStalePrepared(array $row): bool {
  if(($row['state']??null)!=='prepared'){return false;}
  $binding=$this->binding((string)$row['binding_id']);$pair=$this->pair((string)$row['binding_id']);$intent=$this->intent((string)$row['binding_id']);
  if($binding===null||$pair===null||$intent===null){return false;}
  $root=$this->root((string)$row['owner_uid'],(int)$row['root_file_id']);
  $rootHash=SourcePairingRegistryService::rootHash($root);
  if(!self::exactStalePrepared($row,$binding,$pair,$intent,$rootHash)){return false;}
  $this->db->beginTransaction();
  try {
   $this->lock();$current=$this->one((string)$row['operation_id']);
   $binding=$this->binding((string)$row['binding_id']);$pair=$this->pair((string)$row['binding_id']);$intent=$this->intent((string)$row['binding_id']);
   if($current===null||$binding===null||$pair===null||$intent===null){throw new \DomainException('Stale root migration disappeared');}
   $root=$this->root((string)$current['owner_uid'],(int)$current['root_file_id']);
   $rootHash=SourcePairingRegistryService::rootHash($root);
   if(!self::exactStalePrepared($current,$binding,$pair,$intent,$rootHash)){throw new \DomainException('Stale root migration scope changed');}
   $this->assertPhysical($current,$root);
   $this->update((string)$current['operation_id'],['state'=>'superseded','updated_at'=>time()]);
   $this->db->commit();return true;
  }catch(\Throwable $e){if($this->db->inTransaction()){$this->db->rollBack();}throw $e;}
 }
 private static function exactStalePrepared(array $row,array $binding,array $pair,array $intent,string $rootHash): bool {
  if(($row['state']??null)!=='prepared'||($binding['publication_state']??null)!=='active'||
   ($pair['state']??null)!=='active'||($intent['state']??null)!=='blocked'||
   ($intent['error_code']??null)!=='publication_stopped'||
   (int)($binding['publication_epoch']??0)<=(int)($row['prepared_epoch']??0)||
   (string)($row['new_root_hash']??'')!==''){return false;}
  foreach([['binding_id','binding_id'],['pair_operation_id','operation_id'],['instance_id','instance_id'],
   ['tenant_id','tenant_id'],['knowledge_base_id','knowledge_base_id'],['data_source_id','data_source_id']] as [$moveKey,$pairKey]){
   if((string)($row[$moveKey]??'')!==(string)($pair[$pairKey]??'')){return false;}
  }
  if((string)($row['binding_id']??'')!==(string)($intent['binding_id']??'')||
   (string)($row['pair_operation_id']??'')!==(string)($intent['operation_id']??'')||
   (string)($row['physical_root_key']??'')!==(string)($intent['root_key']??'')||
   (int)($row['root_file_id']??0)!==(int)($intent['root_file_id']??0)||
   (int)($row['root_file_id']??0)!==(int)($binding['root_file_id']??0)||
   (string)($row['rule_id']??'')!==(string)($intent['rule_id']??'')||
   (string)($row['owner_uid']??'')!==(string)($intent['owner_uid']??'')||
   (string)($row['actor_uid']??'')===''||
   (string)($row['actor_uid']??'')!==(string)($intent['actor_uid']??'')||
   !hash_equals((string)($pair['source_hash']??''),MachineKeyRegistryService::sourceHash($binding))||
   !hash_equals((string)($row['old_root_hash']??''),(string)($pair['root_hash']??''))||
   !hash_equals((string)($pair['root_hash']??''),$rootHash)){return false;}
  try{$request=json_decode((string)($intent['request_json']??''),true,16,JSON_THROW_ON_ERROR);}catch(\Throwable $e){return false;}
  return is_array($request)&&isset($request['operation_id'],$request['tenant_id'],$request['instance_id'],$request['binding_id'],
   $request['knowledge_base_id'])&&
   (string)$request['operation_id']===(string)$row['pair_operation_id']&&
   (string)$request['tenant_id']===(string)$row['tenant_id']&&
   (string)$request['instance_id']===(string)$row['instance_id']&&
   (string)$request['binding_id']===(string)$row['binding_id']&&
   (string)$request['knowledge_base_id']===(string)$row['knowledge_base_id'];
 }
 public function proof(string $binding,string $operation): array {
  $row=$this->one($operation);
  if($row===null||$row['binding_id']!==$binding||!in_array($row['state'],['observed','finalized'],true)){throw new \DomainException('Root move proof is not ready');}
  $root=$this->root($row['owner_uid'],(int)$row['root_file_id']);$this->assertPhysical($row,$root);
  if(!hash_equals($row['new_root_hash'],SourcePairingRegistryService::rootHash($root))){throw new \DomainException('Root move proof is stale');}
  if($row['state']!=='finalized'){$this->assertStopped($row);}
  return $this->publicRow($row);
 }
 public function acknowledge(string $binding,string $operation,array $tuple): array {
  $this->db->beginTransaction();try {
   $this->lock();$row=$this->one($operation);
   if($row===null||$row['binding_id']!==$binding){throw new \DomainException('Root migration not found');}
   foreach($this->publicRow($row) as $field=>$value){if($field==='state'){continue;}if(!array_key_exists($field,$tuple)||(string)$tuple[$field]!== (string)$value){throw new \DomainException('Root migration ACK tuple changed');}}
   $root=$this->root($row['owner_uid'],(int)$row['root_file_id']);$this->assertPhysical($row,$root);
   if(!hash_equals($row['new_root_hash'],SourcePairingRegistryService::rootHash($root))){throw new \DomainException('Root moved again before ACK');}
   if($row['state']==='finalized'){$this->db->commit();return $this->publicRow($row);}
   if($row['state']!=='observed'){throw new \DomainException('Root migration is not observed');}
   $this->assertStopped($row);
   $q=$this->db->getQueryBuilder();
   if($q->update('weknora_src_pair')->set('root_hash',$q->createNamedParameter($row['new_root_hash']))
    ->set('updated_at',$q->createNamedParameter(time()))->where($q->expr()->eq('operation_id',$q->createNamedParameter($row['pair_operation_id'])))
    ->andWhere($q->expr()->eq('root_hash',$q->createNamedParameter($row['old_root_hash'])))
    ->andWhere($q->expr()->eq('state',$q->createNamedParameter('active')))->executeStatement()!==1){throw new \DomainException('Root pair changed');}
   // The updated hash is visible to requireActiveRoot inside this transaction.
   $this->bindings->save($binding,$root->getName(),$row['owner_uid'],$root->getId());
   if((int)$row['was_active']===1){$result=$this->bindings->setPublicationState($binding,'active','root-migration');
    if($result===null||$result['publication_epoch']!==(int)$row['resume_epoch']){throw new \DomainException('Root resume generation changed');}}
   PublicationRecoveryLedger::appendInTransaction($this->db,$binding,$root->getId(),'reconcile',(int)$row['resume_epoch']);
   $this->update($operation,['state'=>'finalized','updated_at'=>time()]);$row['state']='finalized';
   $this->db->commit();$this->changes->append($binding,$root->getId(),'subtree_scan');return $this->publicRow($row);
  }catch(\Throwable $e){if($this->db->inTransaction()){$this->db->rollBack();}throw $e;}
 }

 private function assertPhysical(array $row,Folder $root): void {
  if($root->getId()!==(int)$row['root_file_id']||!hash_equals($row['physical_root_key'],DirectoryPublicationService::physicalKey($root))||!hash_equals($row['storage_identity'],self::storageIdentity($root))){throw new \DomainException('Root physical identity changed');}
  $this->sameRule($row,$root);$this->acl->assertTreeSupported($root);
  foreach($this->bindings->listBindings() as $binding){if($binding['id']===$row['binding_id']){continue;}
   try{$other=$this->root($binding['owner_uid'],$binding['root_file_id']);}
   catch(\DomainException $e){
    if(self::quarantineUnavailableStoppedRoot($binding)){continue;}
    throw $e;
   }
   if(DirectoryPublicationService::contains($root,$other)||DirectoryPublicationService::contains($other,$root)){throw new \DomainException('Moved root overlaps another source');}}
 }
 private static function quarantineUnavailableStoppedRoot(array $binding): bool {
  return ($binding['publication_state']??null)==='stopped';
 }
 private function assertStopped(array $row): void {
  foreach($this->bindings->listBindings() as $b){if($b['id']===$row['binding_id']&&$b['publication_state']==='stopped'&&$b['publication_epoch']===(int)$row['prepared_epoch']){return;}}
  throw new \DomainException('Root migration fence changed');
 }
 private function sameRule(array $row,Folder $root): array {
  $q=$this->db->getQueryBuilder();$r=$q->select('config_json')->from('weknora_dir_rules')->where($q->expr()->eq('rule_id',$q->createNamedParameter($row['rule_id'])))->executeQuery();
  try{$raw=$r->fetchOne();}finally{$r->closeCursor();}if(!is_string($raw)){throw new \DomainException('Root publication rule is unavailable');}
  $rule=json_decode($raw,true,16,JSON_THROW_ON_ERROR);$container=$this->root($rule['owner_uid'],$rule['container_file_id']);
  if($rule['owner_uid']!==$row['owner_uid']||!DirectoryPublicationService::contains($container,$root)||$root->getId()===$container->getId()||($rule['discovery']==='direct_children'&&$root->getParent()->getId()!==$container->getId())){throw new \DomainException('Root left its authoritative rule');}

  $intent=isset($row['request_json'])?$row:$this->intent($row['binding_id']);$request=$intent===null?null:json_decode($intent['request_json'],true,16,JSON_THROW_ON_ERROR);
  $tenant=$request===null?($row['tenant_id']??null):(string)$request['tenant_id'];$template=$request['processing_template_id']??null;
  $matches=[];foreach($rule['targets']??[['tenant_id'=>$rule['tenant_id'],'processing_template_id'=>$rule['processing_template_id']]] as $target){if($target['tenant_id']===$tenant&&($template===null||$target['processing_template_id']===$template)){$matches[]=$target;}}
  if(count($matches)!==1){throw new \DomainException('Original tenant/template scope is unavailable or ambiguous');}$rule=array_replace($rule,$matches[0]);
  return $rule;
 }
 private function root(string $uid,int $id): Folder {foreach($this->files->getUserFolder($uid)->getById($id) as $node){if($node instanceof Folder&&$node->getId()===$id&&$node->isReadable()){return $node;}}throw new \DomainException('Root is unavailable');}
 private function one(string $op): ?array {return $this->lookup('weknora_root_move','operation_id',$op);}
 private function binding(string $id): ?array {foreach($this->bindings->listBindings() as $binding){if($binding['id']===$id){return $binding;}}return null;}
 private function pair(string $binding): ?array {return $this->lookup('weknora_src_pair','binding_id',$binding);}
 private function intent(string $binding): ?array {return $this->lookup('weknora_dir_intent','binding_id',$binding);}
 private function lookup(string $table,string $field,string $value): ?array {$q=$this->db->getQueryBuilder();$r=$q->select('*')->from($table)->where($q->expr()->eq($field,$q->createNamedParameter($value)))->setMaxResults(1)->executeQuery();try{$row=$r->fetchAssociative();return $row===false?null:$row;}finally{$r->closeCursor();}}
 private function pending(string $binding): ?array {foreach($this->rows(['prepared','observed']) as $row){if($row['binding_id']===$binding){return $row;}}return null;}
 private function rows(array $states): array {$q=$this->db->getQueryBuilder();$r=$q->select('*')->from('weknora_root_move')->where($q->expr()->in('state',$q->createNamedParameter($states,\OCP\DB\QueryBuilder\IQueryBuilder::PARAM_STR_ARRAY)))->orderBy('created_at')->setMaxResults(100)->executeQuery();try{return $r->fetchAllAssociative();}finally{$r->closeCursor();}}
 private function update(string $op,array $fields): void {$q=$this->db->getQueryBuilder();$q->update('weknora_root_move')->where($q->expr()->eq('operation_id',$q->createNamedParameter($op)));foreach($fields as $k=>$v){$q->set($k,$q->createNamedParameter($v));}$q->executeStatement();}
 private function head(): array {$q=$this->db->getQueryBuilder();$r=$q->select('stream_id','sequence','chain_sha256')->from('weknora_recovery_head')->where($q->expr()->eq('id',$q->createNamedParameter(1)))->executeQuery();try{$row=$r->fetchAssociative();if($row===false){throw new \RuntimeException('Recovery head unavailable');}return $row;}finally{$r->closeCursor();}}
 private function lock(): void {$this->db->insertIgnoreConflict('weknora_bind_lock',['id'=>1]);$q=$this->db->getQueryBuilder();$r=$q->select('id')->from('weknora_bind_lock')->where($q->expr()->eq('id',$q->createNamedParameter(1)))->forUpdate()->executeQuery();try{if($r->fetchOne()===false){throw new \RuntimeException('Binding lock unavailable');}}finally{$r->closeCursor();}}
 private function publicRow(array $row): array {$out=[];foreach(['operation_id','pair_operation_id','binding_id','instance_id','tenant_id','knowledge_base_id','data_source_id','physical_root_key','storage_identity','old_root_hash','new_root_hash','old_root_path','new_root_path','journal_stream_id','journal_chain_sha256','state'] as $k){$out[$k]=(string)$row[$k];}foreach(['root_file_id','old_epoch','prepared_epoch','resume_epoch','journal_sequence'] as $k){$out[$k]=(string)$row[$k];}return $out;}
}
