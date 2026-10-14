<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Controller;
use OCA\IntegrationWeknora\Service\RootMigrationService;
use OCA\IntegrationWeknora\Service\PairedServiceToken;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoCSRFRequired;
use OCP\AppFramework\Http\Attribute\PublicPage;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IRequest;
final class RootMigrationController extends Controller {
 public function __construct(IRequest $request,private RootMigrationService $moves,private PairedServiceToken $token){parent::__construct('integration_weknora',$request);}
 #[PublicPage]
 #[NoCSRFRequired]
 public function proof(string $id,string $operationId): JSONResponse {
  if($this->token->authenticatedBinding($this->request,$id)===null){return $this->json(['error'=>'unauthorized'],401);}
  if(!SourcePairingRegistryService::validOperationId($operationId)){return $this->json(['error'=>'invalid_root_migration'],400);}
  try{return $this->json(['migration'=>$this->moves->proof($id,$operationId)]);}catch(\DomainException $e){return $this->json(['error'=>'root_migration_conflict'],409);}catch(\Throwable $e){return $this->json(['error'=>'root_migration_unverified'],503);}
 }
 #[PublicPage]
 #[NoCSRFRequired]
 public function acknowledge(string $id,string $operationId): JSONResponse {
  if($this->token->authenticatedBinding($this->request,$id)===null){return $this->json(['error'=>'unauthorized'],401);}
  if(!SourcePairingRegistryService::validOperationId($operationId)){return $this->json(['error'=>'invalid_root_migration'],400);}
  try{$tuple=$this->request->getParams();return $this->json(['migration'=>$this->moves->acknowledge($id,$operationId,$tuple)]);}catch(\DomainException $e){return $this->json(['error'=>'root_migration_conflict'],409);}catch(\Throwable $e){return $this->json(['error'=>'root_migration_unverified'],503);}
 }
 private function json(array $value,int $status=200): JSONResponse{return new JSONResponse($value,$status,['Cache-Control'=>'no-store']);}
}
