<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\BindingPublicationStoppedException;
use OCA\IntegrationWeknora\Service\MachineKeyRegistryService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IUserSession;

/** Session and CSRF protected one-time issuance of binding-scoped keys. */
final class MachineKeyAdminController extends Controller {
    public function __construct(
        IRequest $request,
        private IUserSession $userSession,
        private IGroupManager $groupManager,
        private MachineKeyRegistryService $keys,
    ) {
        parent::__construct('integration_weknora', $request);
    }

    public function index(string $id): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            return $this->json(['keys' => $this->keys->listForBinding($id)]);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'binding_not_found'], 404);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'key_registry_unavailable'], 503);
        }
    }

    public function issue(string $id): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $keyId = $this->request->getParam('key_id');
        if (!MachineKeyRegistryService::validKeyId($keyId)) {
            return $this->json(['error' => 'invalid_key_id'], 400);
        }
        try {
            $token = $this->keys->issue($id, $keyId, $this->userSession->getUser()->getUID());
            return $this->json([
                'binding_id' => $id,
                'key_id' => $keyId,
                'token' => $token,
            ], 201);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'binding_not_found'], 404);
        } catch (BindingPublicationStoppedException $exception) {
            return $this->json(['error' => 'publication_stopped'], 423);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'key_id_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'key_registry_unavailable'], 503);
        }
    }

    public function revoke(string $id, string $keyId): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            return $this->keys->revoke($id, $keyId)
                ? $this->json(['revoked' => true])
                : $this->json(['error' => 'key_not_found'], 404);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'binding_not_found'], 404);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'pairing_key_in_use'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'key_registry_unavailable'], 503);
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
