<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;
use OCP\IConfig;
use OCP\IDBConnection;
use OCP\IGroupManager;
use OCP\Files\Folder;
use OCP\Files\File;
/** Private maintenance phase: source gates remain stopped after global control reconciliation. */
final class ControlRecoveryReconciler {
 public function __construct(private IDBConnection $db,private IConfig $config,private ControlRecoveryService $controls,
  private BindingRegistryService $bindings,private IdentityMappingService $identities,private IGroupManager $groups,
  private SourceContentRevisionService $content,private DirectoryGrantSnapshotService $grants){}
 public function reconcile(array $tail,string $originalPlanSHA,string $inventorySHA,string $actorUid): array {
  if(!$this->config->getSystemValueBool('maintenance',false)||!preg_match('/\A[a-f0-9]{64}\z/D',$originalPlanSHA)||!preg_match('/\A[a-f0-9]{64}\z/D',$inventorySHA)||!$this->groups->isAdmin($actorUid)) {throw new \DomainException('Control reconciliation requires a current administrator in maintenance');}
  $this->identities->forUser($actorUid); // Live LDAP GUID validation, not a restored login/mapping.
  $this->db->beginTransaction();try{
   $this->db->insertIgnoreConflict('weknora_bind_lock',['id'=>1]);$q=$this->db->getQueryBuilder();$r=$q->select('id')->from('weknora_bind_lock')->where($q->expr()->eq('id',$q->createNamedParameter(1)))->forUpdate()->executeQuery();try{$r->fetchOne();}finally{$r->closeCursor();}
   $q=$this->db->getQueryBuilder();$r=$q->select('*')->from('weknora_control_gate')->where($q->expr()->eq('id',$q->createNamedParameter(1)))->executeQuery();try{$gate=$r->fetchAssociative();}finally{$r->closeCursor();}
   if($gate===false||!hash_equals((string)$gate['plan_sha256'],$originalPlanSHA)){throw new \DomainException('Control closure plan changed');}
   $q=$this->db->getQueryBuilder();$r=$q->select('stream_id','sequence','chain_sha256')->from('weknora_recovery_head')->where($q->expr()->eq('id',$q->createNamedParameter(1)))->executeQuery();try{$head=$r->fetchAssociative();}finally{$r->closeCursor();}
   if($head===false||($tail['instance_id']??null)!==$this->config->getSystemValueString('instanceid')||($tail['stream_id']??null)!==$head['stream_id']||(string)($tail['through_sequence']??'')!==(string)$head['sequence']||!hash_equals((string)$head['chain_sha256'],(string)($tail['through_database_chain_sha256']??''))){throw new \DomainException('Latest independently pinned external tail is required');}
   $inventory=$this->controls->metadataInventory();if($inventory['missing_tables']!==[]){throw new \DomainException('Recovery control schema is incomplete');}if(!hash_equals($inventory['sha256'],$inventorySHA)){throw new \DomainException('Current control inventory changed');}
   $checked=[];foreach($this->bindings->listBindings() as $binding){
    if($binding['publication_state']!=='stopped'){throw new \DomainException('Every source must remain stopped during control reconciliation');}
    $root=$this->bindings->requireActiveRoot($binding['id']);$this->bindings->assertTreeSupported($binding['id'],$root);
    $this->identities->forUser($binding['owner_uid']);$this->grants->snapshot($root,$binding['owner_uid'],$actorUid);
    $ids=[];$walk=function(Folder $folder)use(&$walk,&$ids):void{foreach($folder->getDirectoryListing() as $node){if($node instanceof Folder){$walk($node);}elseif($node instanceof File){$ids[]=$node->getId();}}};$walk($root);$this->content->verifyRecoveryVersions($ids);
    $checked[]=['binding_id'=>$binding['id'],'root_file_id'=>$root->getId(),'publication_epoch'=>$binding['publication_epoch'],'files'=>count($ids)];
   }
   if(!hash_equals($inventorySHA,$this->controls->metadataInventory()['sha256'])){throw new \DomainException('Current ACL/control inventory changed during reconciliation');}
   // This phase only removes the global maintenance barrier. Every per-source
   // publication gate stays stopped; the ordinary fresh resume protocol must
   // still pass before a machine manifest or personal root access is possible.
   $q=$this->db->getQueryBuilder();$changed=$q->update('weknora_control_gate')->set('closed',$q->createNamedParameter(0))->where($q->expr()->eq('id',$q->createNamedParameter(1)))->andWhere($q->expr()->eq('plan_sha256',$q->createNamedParameter($originalPlanSHA)))->executeStatement();
   if($changed!==1){throw new \DomainException('Control closure compare-and-swap failed');}$this->db->commit();
   return ['schema_version'=>1,'controls_reconciled'=>true,'sources'=>$checked,'sources_remain_stopped'=>true,'ingress_reopen_permitted'=>false,'automation_credentials_reauthorization_required'=>true];
  }catch(\Throwable $e){$this->db->rollBack();throw $e;}
 }
}
