<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;
use OCP\IDBConnection;

/** Recovery restores metadata inventories, never old privileges or source bytes. */
final class ControlRecoveryService {
 public const TABLES=['weknora_dir_rules','weknora_dir_intent','weknora_dir_discover','weknora_group_identity','weknora_pair_handoff',
  'weknora_binding_id','weknora_machine_key','weknora_src_pair','weknora_src_pair_rot','weknora_src_decom',
  'weknora_root_move','weknora_target_move','weknora_pub_state','weknora_file_status','weknora_status_inbox','weknora_source_file_rev',
  'weknora_event_conn','weknora_identities','weknora_identity_audit','weknora_pub_audit','weknora_bind_pub_audit','weknora_recovery_head','weknora_recovery_log','weknora_recovery_apply','weknora_status_pending','weknora_status_scan','weknora_recovery_floor','weknora_control_gate'];
 public function __construct(private IDBConnection $db){}
 public function closed(): bool {$q=$this->db->getQueryBuilder();$r=$q->select('closed')->from('weknora_control_gate')->where($q->expr()->eq('id',$q->createNamedParameter(1)))->executeQuery();try{return (int)$r->fetchOne()===1;}finally{$r->closeCursor();}}
 public function floor(int $file): array {return $this->floors([$file])[$file];}
 public function floors(array $ids): array {
  $out=[];foreach($ids as $id){$out[$id]=['revision'=>0,'writing'=>false];}
  foreach(array_chunk(array_keys($out),1000) as $batch){if($batch===[]){continue;}$q=$this->db->getQueryBuilder();$r=$q->select('file_id','revision','writing')->from('weknora_recovery_floor')->where($q->expr()->in('file_id',$q->createNamedParameter($batch,\OCP\DB\QueryBuilder\IQueryBuilder::PARAM_INT_ARRAY)))->executeQuery();try{foreach($r->fetchAllAssociative() as $row){if(filter_var($row['revision'],FILTER_VALIDATE_INT,['options'=>['min_range'=>0]])===false||!in_array($row['writing'],[true,false,0,1,'0','1'],true)){throw new \UnexpectedValueException('Invalid retained recovery floor');}$out[(int)$row['file_id']]=['revision'=>(int)$row['revision'],'writing'=>(bool)$row['writing']];}}finally{$r->closeCursor();}}
  return $out;
 }
 public function closeFromPlan(array $plan,string $inventorySHA=''): void {
  $facts=$plan['latest_content_revisions']??[];if(!is_array($facts)||count($facts)>100000){throw new \InvalidArgumentException('Invalid content recovery inventory');}
  $digest=hash('sha256',json_encode($plan,JSON_THROW_ON_ERROR));
  $this->db->beginTransaction();try {
   $this->db->insertIgnoreConflict('weknora_bind_lock',['id'=>1]);$q=$this->db->getQueryBuilder();$r=$q->select('id')->from('weknora_bind_lock')->where($q->expr()->eq('id',$q->createNamedParameter(1)))->forUpdate()->executeQuery();try{if($r->fetchOne()===false){throw new \RuntimeException('Recovery closure lock unavailable');}}finally{$r->closeCursor();}
   $this->db->insertIgnoreConflict('weknora_control_gate',['id'=>1,'closed'=>1,'closed_at'=>time(),'plan_sha256'=>$digest,'inventory_sha256'=>$inventorySHA]);
   $q=$this->db->getQueryBuilder();$q->update('weknora_control_gate')->set('closed',$q->createNamedParameter(1))->set('closed_at',$q->createNamedParameter(time()))->set('plan_sha256',$q->createNamedParameter($digest))->set('inventory_sha256',$q->createNamedParameter($inventorySHA))->where($q->expr()->eq('id',$q->createNamedParameter(1)))->executeStatement();
   foreach($facts as $fact){$id=filter_var($fact['file_id']??null,FILTER_VALIDATE_INT,['options'=>['min_range'=>1]]);$revision=filter_var($fact['revision']??null,FILTER_VALIDATE_INT,['options'=>['min_range'=>0]]);
    if($id===false||$revision===false||!is_bool($fact['writing']??null)){throw new \InvalidArgumentException('Invalid retained content version');}
    $old=$this->floor($id);if($old['revision']>$revision){continue;}
    if($old['revision']===$revision && $old['writing']){$fact['writing']=true;}
    $values=['file_id'=>$id,'revision'=>$revision,'writing'=>$fact['writing']?1:0,'plan_sha256'=>$digest,'verified_at'=>time()];$this->db->insertIgnoreConflict('weknora_recovery_floor',$values);
    $q=$this->db->getQueryBuilder();$q->update('weknora_recovery_floor');foreach($values as $c=>$v){if($c!=='file_id'){$q->set($c,$q->createNamedParameter($v));}}$q->where($q->expr()->eq('file_id',$q->createNamedParameter($id)))->andWhere($q->expr()->lte('revision',$q->createNamedParameter($revision)))->executeStatement();
   }
   $this->db->commit();
  }catch(\Throwable $e){$this->db->rollBack();throw $e;}
 }
 /** Only counts/hashes are exported; handoff ciphertext and identifiers stay private. */
 public function metadataInventory(): array {
  $schema=$this->db->createSchema();$names=$schema->getTableNames();$out=[];$missing=[];
  $prefix=\OCP\Server::get(\OCP\IConfig::class)->getSystemValueString('dbtableprefix','oc_');
  foreach(self::TABLES as $table){if(!array_filter($names,static fn(string $name):bool=>$name===$prefix.$table||str_ends_with($name,'.'.$prefix.$table))){$missing[]=$table;continue;}$q=$this->db->getQueryBuilder();$r=$q->select('*')->from($table)->executeQuery();try{$rows=$r->fetchAllAssociative();}finally{$r->closeCursor();}
   $hashes=[];foreach($rows as $row){ksort($row,SORT_STRING);$hashes[]=hash('sha256',json_encode($row,JSON_THROW_ON_ERROR));}sort($hashes,SORT_STRING);$out[$table]=['rows'=>count($rows),'sha256'=>hash('sha256',json_encode($hashes,JSON_THROW_ON_ERROR))];
  }
  return ['schema_version'=>1,'closed'=>$this->closed(),'tables'=>$out,'missing_tables'=>$missing,'sha256'=>hash('sha256',json_encode(['tables'=>$out,'missing_tables'=>$missing],JSON_THROW_ON_ERROR))];
 }
}
