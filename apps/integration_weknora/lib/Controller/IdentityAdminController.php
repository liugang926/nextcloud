<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\IdentityMappingService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IUserSession;

/** Session and CSRF protected, administrator-attested identity mapping. */
final class IdentityAdminController extends Controller {
    public function __construct(
        IRequest $request,
        private IUserSession $session,
        private IGroupManager $groups,
        private IdentityMappingService $identities,
    ) {
        parent::__construct('integration_weknora', $request);
    }

    public function index(): JSONResponse {
        if ($this->adminUid() === null) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            return $this->json(['identities' => $this->identities->listMappings()]);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'identity_registry_unavailable'], 503);
        }
    }

    public function create(): JSONResponse {
        $actorUid = $this->adminUid();
        if ($actorUid === null) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $input = $this->mappingInput();
        if ($input === null) {
            return $this->json(['error' => 'invalid_identity'], 400);
        }
        try {
            $created = $this->identities->create($input[0], $input[1], $input[2], $actorUid);
            return $this->json([
                'directory_id' => $input[0],
                'object_guid' => strtolower($input[1]),
                'nextcloud_uid' => $input[2],
            ], $created ? 201 : 200);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_identity'], 400);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'identity_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'identity_registry_unavailable'], 503);
        }
    }

    public function revoke(): JSONResponse {
        $actorUid = $this->adminUid();
        if ($actorUid === null) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $input = $this->mappingInput();
        if ($input === null) {
            return $this->json(['error' => 'invalid_identity'], 400);
        }
        try {
            $revoked = $this->identities->revoke($input[0], $input[1], $input[2], $actorUid);
            return $this->json(['revoked' => $revoked]);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_identity'], 400);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'identity_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'identity_registry_unavailable'], 503);
        }
    }

    /** @return array{string, string, string}|null */
    private function mappingInput(): ?array {
        $directoryId = $this->request->getParam('directory_id');
        $objectGuid = $this->request->getParam('object_guid');
        $uid = $this->request->getParam('nextcloud_uid');
        if (!is_string($directoryId) || !is_string($objectGuid) || !is_string($uid)) {
            return null;
        }
        return [$directoryId, $objectGuid, $uid];
    }

    private function adminUid(): ?string {
        $user = $this->session->getUser();
        if ($user === null || !$this->groups->isAdmin($user->getUID())) {
            return null;
        }
        return $user->getUID();
    }

    private function json(array $data, int $status = 200): JSONResponse {
        return new JSONResponse($data, $status, ['Cache-Control' => 'no-store']);
    }
}
