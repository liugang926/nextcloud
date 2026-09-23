<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\BindingPublicationStoppedException;
use OCA\IntegrationWeknora\Service\PairedServiceToken;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoCSRFRequired;
use OCP\AppFramework\Http\Attribute\PublicPage;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IUserSession;

/** Admin intent and signed source-pair commit; unrelated to event delivery. */
final class SourcePairingController extends Controller {
    public function __construct(
        IRequest $request,
        private IUserSession $userSession,
        private IGroupManager $groupManager,
        private SourcePairingRegistryService $pairings,
        private PairedServiceToken $serviceToken,
    ) {
        parent::__construct('integration_weknora', $request);
    }

    public function show(string $id): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            $pairing = $this->pairings->status($id);
            return $pairing === null
                ? $this->json(['error' => 'pairing_not_found'], 404)
                : $this->json(['pairing' => $pairing]);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'pairing_unavailable'], 503);
        }
    }

    public function prepare(string $id): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $operationId = $this->request->getParam('operation_id');
        $tenantId = $this->request->getParam('tenant_id');
        $knowledgeBaseId = $this->request->getParam('knowledge_base_id');
        if (!SourcePairingRegistryService::validOperationId($operationId) ||
            !SourcePairingRegistryService::validTenantId($tenantId) ||
            !SourcePairingRegistryService::validRemoteId($knowledgeBaseId)) {
            return $this->json(['error' => 'invalid_pairing'], 400);
        }
        try {
            $result = $this->pairings->prepare($id, $operationId, $tenantId,
                $knowledgeBaseId, $this->userSession->getUser()->getUID());
            $body = ['pairing' => $result['pairing']];
            if ($result['created']) {
                $body['token'] = $result['token'];
            }
            return $this->json($body, $result['created'] ? 201 : 200);
        } catch (BindingPublicationStoppedException $exception) {
            return $this->json(['error' => 'publication_stopped'], 423);
        } catch (\OutOfBoundsException $exception) {
            return $this->json(['error' => 'binding_not_found'], 404);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_pairing'], 400);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'pairing_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'pairing_unavailable'], 503);
        }
    }

    public function abort(string $id): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $operationId = $this->request->getParam('operation_id');
        if (!SourcePairingRegistryService::validOperationId($operationId)) {
            return $this->json(['error' => 'invalid_pairing'], 400);
        }
        try {
            return $this->json($this->pairings->abort($id, $operationId));
        } catch (\OutOfBoundsException $exception) {
            return $this->json(['error' => 'pairing_not_found'], 404);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'pairing_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'pairing_unavailable'], 503);
        }
    }

    public function rotationStatus(string $id): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            $rotation = $this->pairings->rotationStatus($id);
            return $rotation === null
                ? $this->json(['error' => 'rotation_not_found'], 404)
                : $this->json(['rotation' => $rotation]);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'rotation_unavailable'], 503);
        }
    }

    public function prepareRotation(string $id): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $operationId = $this->request->getParam('operation_id');
        if (!SourcePairingRegistryService::validOperationId($operationId)) {
            return $this->json(['error' => 'invalid_rotation'], 400);
        }
        try {
            $result = $this->pairings->prepareRotation($id, $operationId,
                $this->userSession->getUser()->getUID());
            $body = ['rotation' => $result['rotation']];
            if ($result['created']) {
                $body['token'] = $result['token'];
            }
            return $this->json($body, $result['created'] ? 201 : 200);
        } catch (BindingPublicationStoppedException $exception) {
            return $this->json(['error' => 'publication_stopped'], 423);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_rotation'], 400);
        } catch (\DomainException|\OutOfBoundsException $exception) {
            return $this->json(['error' => 'rotation_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'rotation_unavailable'], 503);
        }
    }

    public function abortRotation(string $id): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $operationId = $this->request->getParam('operation_id');
        if (!SourcePairingRegistryService::validOperationId($operationId)) {
            return $this->json(['error' => 'invalid_rotation'], 400);
        }
        try {
            return $this->json($this->pairings->abortRotation($id, $operationId));
        } catch (\OutOfBoundsException $exception) {
            return $this->json(['error' => 'rotation_not_found'], 404);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'rotation_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'rotation_unavailable'], 503);
        }
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function commitRotation(string $id): JSONResponse {
        return $this->machineRotation($id, 'commitRotation');
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function finalizeRotation(string $id): JSONResponse {
        return $this->machineRotation($id, 'finalizeRotation');
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function abortRotationMachine(string $id): JSONResponse {
        return $this->machineRotation($id, 'abortRotationMachine');
    }

    private function machineRotation(string $id, string $method): JSONResponse {
        if ($this->serviceToken->authenticatedBinding($this->request, $id) === null) {
            return $this->json(['error' => 'unauthorized'], 401);
        }
        $keyId = $this->request->getHeader('X-WeKnora-Key-Id');
        $operationId = $this->request->getParam('operation_id');
        $pairOperationId = $this->request->getParam('pair_operation_id');
        $instanceId = $this->request->getParam('instance_id');
        $tenantId = $this->request->getParam('tenant_id');
        $knowledgeBaseId = $this->request->getParam('knowledge_base_id');
        $dataSourceId = $this->request->getParam('data_source_id');
        if (!is_string($keyId) ||
            !SourcePairingRegistryService::validOperationId($operationId) ||
            !SourcePairingRegistryService::validOperationId($pairOperationId) ||
            !is_string($instanceId) || $instanceId === '' || strlen($instanceId) > 64 ||
            !SourcePairingRegistryService::validTenantId($tenantId) ||
            !SourcePairingRegistryService::validRemoteId($knowledgeBaseId) ||
            !SourcePairingRegistryService::validRemoteId($dataSourceId)) {
            return $this->json(['error' => 'invalid_rotation'], 400);
        }
        try {
            return $this->json($this->pairings->$method($id, $keyId, $operationId,
                $pairOperationId, $instanceId, $tenantId, $knowledgeBaseId, $dataSourceId));
        } catch (BindingPublicationStoppedException $exception) {
            return $this->json(['error' => 'publication_stopped'], 423);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_rotation'], 400);
        } catch (\DomainException|\OutOfBoundsException $exception) {
            return $this->json(['error' => 'rotation_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'rotation_unavailable'], 503);
        }
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function commit(string $id): JSONResponse {
        // A valid HMAC consumes a nonce; then the registry checks that this
        // precise key was issued for this precise pending operation.
        if ($this->serviceToken->authenticatedBinding($this->request, $id) === null) {
            return $this->json(['error' => 'unauthorized'], 401);
        }
        $keyId = $this->request->getHeader('X-WeKnora-Key-Id');
        $operationId = $this->request->getParam('operation_id');
        $instanceId = $this->request->getParam('instance_id');
        $tenantId = $this->request->getParam('tenant_id');
        $knowledgeBaseId = $this->request->getParam('knowledge_base_id');
        $dataSourceId = $this->request->getParam('data_source_id');
        if (!is_string($keyId) || !SourcePairingRegistryService::validOperationId($operationId) ||
            !is_string($instanceId) || $instanceId === '' || strlen($instanceId) > 64 ||
            !SourcePairingRegistryService::validTenantId($tenantId) ||
            !SourcePairingRegistryService::validRemoteId($knowledgeBaseId) ||
            !SourcePairingRegistryService::validRemoteId($dataSourceId)) {
            return $this->json(['error' => 'invalid_pairing'], 400);
        }
        try {
            return $this->json($this->pairings->commit($id, $keyId, $operationId,
                $instanceId, $tenantId, $knowledgeBaseId, $dataSourceId));
        } catch (BindingPublicationStoppedException $exception) {
            return $this->json(['error' => 'publication_stopped'], 423);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_pairing'], 400);
        } catch (\DomainException|\OutOfBoundsException $exception) {
            return $this->json(['error' => 'pairing_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'pairing_unavailable'], 503);
        }
    }

    private function isAdmin(): bool {
        $user = $this->userSession->getUser();
        return $user !== null && $this->groupManager->isAdmin($user->getUID());
    }

    private function json(array $data, int $status = 200): JSONResponse {
        return new JSONResponse($data, $status, [
            'Cache-Control' => 'no-store',
            'Referrer-Policy' => 'no-referrer',
        ]);
    }
}
