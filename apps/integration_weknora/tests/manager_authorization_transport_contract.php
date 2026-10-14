<?php
declare(strict_types=1);
require_once __DIR__ . '/../lib/Service/SourcePairingRegistryService.php';
require_once __DIR__ . '/../lib/Service/IdentityMappingService.php';
require_once __DIR__ . '/../lib/Service/ManagerAuthorizationProtocol.php';
use OCA\IntegrationWeknora\Service\ManagerAuthorizationProtocol as Protocol;

$scope = ['instance_id'=>'instance', 'operation_id'=>'00112233-4455-4677-8899-aabbccddeeff',
    'tenant_id'=>'77', 'knowledge_base_id'=>'kb.with-dot', 'data_source_id'=>'source',
    'processing_template_id'=>'template', 'directory_id'=>'ad.example.test',
    'object_guid'=>'11223344-5566-4788-99aa-bbccddeeff00', 'purpose_sha256'=>str_repeat('a', 64)];
$raw = json_encode($scope, JSON_THROW_ON_ERROR);
$uri = '/index.php/apps/integration_weknora/api/v1/bindings/binding/authorize-manager';
foreach (['application/json', 'application/json; charset=UTF-8', 'Application/JSON; charset="utf-8"'] as $type) {
    if (Protocol::rawRequest('binding', 'POST', $type, $uri, $raw) !== $scope) { throw new RuntimeException('Signed JSON scope changed'); }
}
$cases = [
    ['GET','application/json',$uri,$raw],
    ['POST','text/plain',$uri,$raw],
    ['POST','application/x-www-form-urlencoded',$uri,$raw],
    ['POST','application/json; charset=ISO-8859-1',$uri,$raw],
    ['POST','application/json; ignored=1',$uri,$raw],
    ['POST','application/json',$uri.'?',$raw],
    ['POST','application/json',$uri.'?directory_id=foreign',$raw],
    ['POST','application/json',$uri,'null'],
    ['POST','application/json',$uri,'[]'],
    ['POST','application/json',$uri,'true'],
    ['POST','application/json',$uri,'{'],
    ['POST','application/json',$uri,json_encode($scope+['unknown'=>'must-not-be-dropped'],JSON_THROW_ON_ERROR)],
    ['POST','application/json',$uri,json_encode(array_diff_key($scope,['object_guid'=>true]),JSON_THROW_ON_ERROR)],
    ['POST','application/json',$uri,$raw.str_repeat(' ',Protocol::MAX_RAW_REQUEST_BYTES)],
];
$rejected = 0;
foreach ($cases as [$method,$type,$requestUri,$body]) {
    try { Protocol::rawRequest('binding',$method,$type,$requestUri,$body); }
    catch (InvalidArgumentException|JsonException $e) { $rejected++;continue; }
    throw new RuntimeException('Ambiguous manager transport accepted');
}
// Every effective field comes from the body rather than a merged parameter bag.
// The signature verifier already binds these exact raw bytes and nonce; this
// pure helper test does not claim actual authentication/LDAP/READ acceptance.
$changed=$scope;$changed['processing_template_id']='another';
if (Protocol::rawRequest('binding','POST','application/json',$uri,json_encode($changed,JSON_THROW_ON_ERROR))!==$changed) { throw new RuntimeException('Raw parser adopted a cached scope'); }
echo json_encode(['controlled_transport_contract'=>'PASS','invalid_transports_rejected'=>$rejected,
    'actual_authentication_or_manager_or_READ_acceptance'=>false],JSON_THROW_ON_ERROR)."\n";
