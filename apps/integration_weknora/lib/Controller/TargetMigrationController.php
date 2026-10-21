<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Controller;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\ConnectionService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IRequest;
use OCP\IUserSession;
final class TargetMigrationController extends Controller {
 public function __construct(IRequest $request,private IUserSession $session,private DirectoryPublicationService $directories,private ConnectionService $connection){parent::__construct('integration_weknora',$request);}
 #[NoAdminRequired]
 public function begin(int $folderId): JSONResponse {
  $user=$this->session->getUser();if($user===null){return $this->json(['error'=>'unauthorized'],401);}
  $choice=[];foreach(['operation_id','mode','name','knowledge_base_id','tenant_id','processing_template_id'] as $field){$choice[$field]=$this->request->getParam($field);}
  try{$this->connection->assertTargetAllowed($user->getUID(),$choice);return $this->json(['migration'=>$this->directories->switchTarget($user->getUID(),$folderId,$choice)],202);}
  catch(\InvalidArgumentException $e){return $this->json(['error'=>'invalid_target_migration'],400);}
  catch(\DomainException $e){return $this->json(['error'=>'target_migration_conflict'],409);}
  catch(\Throwable $e){return $this->json(['error'=>'target_migration_unverified'],503);}
 }
 #[NoAdminRequired]
 public function status(int $folderId,string $operationId): JSONResponse { return $this->observe($folderId,$operationId,false); }
 #[NoAdminRequired]
 public function retry(int $folderId,string $operationId): JSONResponse { return $this->observe($folderId,$operationId,true); }
 private function observe(int $folderId,string $operationId,bool $retry): JSONResponse {
  $user=$this->session->getUser();if($user===null){return $this->json(['error'=>'unauthorized'],401);}
  try{return $this->json(['migration'=>$this->directories->targetMigration($user->getUID(),$folderId,$operationId,$retry)],202);}catch(\OCA\IntegrationWeknora\Service\TargetMigrationNotFoundException $e){return $this->json(['error'=>'target_migration_not_found'],404);}catch(\DomainException $e){return $this->json(['error'=>'target_migration_conflict'],409);}catch(\Throwable $e){return $this->json(['error'=>'target_migration_unverified'],503);}
 }
 private function json(array $value,int $status): JSONResponse{return new JSONResponse($value,$status,['Cache-Control'=>'no-store']);}
}
