<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\PairedServiceToken;
use OCA\IntegrationWeknora\Service\SourceDecommissionService;
use OCA\IntegrationWeknora\Service\SourcePairingRegistryService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoCSRFRequired;
use OCP\AppFramework\Http\Attribute\PublicPage;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IUserSession;

/** Session-protected retirement intent and signed, exact-pair empty-inventory ACK. */
final class SourceDecommissionController extends Controller {
    public function __construct(
        IRequest $request,
        private IUserSession $userSession,
        private IGroupManager $groupManager,
        private SourceDecommissionService $decommissions,
        private PairedServiceToken $serviceToken,
    ) {
        parent::__construct('integration_weknora', $request);
    }

    public function begin(string $id): JSONResponse {
        $actor = $this->adminUid();
        if ($actor === null) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $operationId = $this->request->getParam('operation_id');
        if (!SourcePairingRegistryService::validOperationId($operationId)) {
            return $this->json(['error' => 'invalid_decommission'], 400);
        }
        try {
            return $this->json(['decommission' => $this->decommissions->begin($id, $operationId, $actor)]);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_decommission'], 400);
        } catch (\OutOfBoundsException $exception) {
            return $this->json(['error' => 'binding_not_found'], 404);
        } catch (\DomainException|\UnexpectedValueException $exception) {
            return $this->json(['error' => 'decommission_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'decommission_unavailable'], 503);
        }
    }

    public function status(string $id): JSONResponse {
        if ($this->adminUid() === null) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            $row = $this->decommissions->status($id);
            return $row === null
                ? $this->json(['error' => 'decommission_not_found'], 404)
                : $this->json(['decommission' => $row]);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_decommission'], 400);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'decommission_unavailable'], 503);
        }
    }

    public function finalize(string $id): JSONResponse {
        if ($this->adminUid() === null) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $operationId = $this->request->getParam('operation_id');
        if (!SourcePairingRegistryService::validOperationId($operationId)) {
            return $this->json(['error' => 'invalid_decommission'], 400);
        }
        try {
            return $this->json(['decommission' => $this->decommissions->finalize($id, $operationId)]);
        } catch (\OutOfBoundsException $exception) {
            return $this->json(['error' => 'decommission_not_found'], 404);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_decommission'], 400);
        } catch (\DomainException|\UnexpectedValueException $exception) {
            return $this->json(['error' => 'decommission_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'decommission_unavailable'], 503);
        }
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function intent(string $id, string $operationId): JSONResponse {
        if ($this->serviceToken->authenticatedBinding($this->request, $id) === null) {
            return $this->json(['error' => 'unauthorized'], 401);
        }
        try {
            return $this->json(['decommission' => $this->decommissions->intent($id, $operationId,
                (string)$this->request->getHeader('X-WeKnora-Key-Id'))]);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_decommission'], 400);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'decommission_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'decommission_unavailable'], 503);
        }
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function acknowledge(string $id, string $operationId): JSONResponse {
        if ($this->serviceToken->authenticatedBinding($this->request, $id) === null) {
            return $this->json(['error' => 'unauthorized'], 401);
        }
        $pairOperationId = $this->request->getParam('pair_operation_id');
        $instanceId = $this->request->getParam('instance_id');
        $tenantId = $this->request->getParam('tenant_id');
        $knowledgeBaseId = $this->request->getParam('knowledge_base_id');
        $dataSourceId = $this->request->getParam('data_source_id');
        $epoch = $this->request->getParam('publication_epoch');
        $digest = $this->request->getParam('inventory_sha256');
        if (!SourcePairingRegistryService::validOperationId($pairOperationId) ||
            !is_string($instanceId) || !SourcePairingRegistryService::validTenantId($tenantId) ||
            !SourcePairingRegistryService::validRemoteId($knowledgeBaseId) ||
            !SourcePairingRegistryService::validRemoteId($dataSourceId) ||
            !is_int($epoch) || $epoch < 0 || !is_string($digest) ||
            $this->request->getParam('logical_withdrawn') !== true ||
            $this->request->getParam('inventory_complete') !== true ||
            $this->request->getParam('inventory_count') !== 0 ||
            $this->request->getParam('visible_count') !== 0 ||
            $this->request->getParam('running_jobs') !== 0) {
            return $this->json(['error' => 'invalid_decommission_ack'], 400);
        }
        try {
            return $this->json(['decommission' => $this->decommissions->acknowledge($id,
                $operationId, (string)$this->request->getHeader('X-WeKnora-Key-Id'),
                strtolower($pairOperationId), $instanceId, $tenantId, $knowledgeBaseId,
                $dataSourceId, $epoch, $digest)]);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_decommission_ack'], 400);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'decommission_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'decommission_unavailable'], 503);
        }
    }

    private function adminUid(): ?string {
        $user = $this->userSession->getUser();
        return $user !== null && $this->groupManager->isAdmin($user->getUID())
            ? $user->getUID() : null;
    }

    private function json(array $data, int $status = 200): JSONResponse {
        return new JSONResponse($data, $status, [
            'Cache-Control' => 'no-store',
            'Referrer-Policy' => 'no-referrer',
        ]);
    }
}
