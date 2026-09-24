<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\EmployeeFileStatusService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IRequest;
use OCP\IUserSession;

/** Session-scoped file status for the Files sidebar. */
final class EmployeeFileStatusController extends Controller {
    public function __construct(
        IRequest $request,
        private IUserSession $userSession,
        private EmployeeFileStatusService $statuses,
    ) {
        parent::__construct('integration_weknora', $request);
    }

    #[NoAdminRequired]
    public function show(int $fileId): JSONResponse {
        $user = $this->userSession->getUser();
        if ($user === null) {
            return $this->json(['error' => 'unauthorized'], 401);
        }
        if ($fileId < 1) {
            return $this->json(['error' => 'not_found'], 404);
        }
        try {
            $status = $this->statuses->forUser($user->getUID(), $fileId);
            return $status === null
                ? $this->json(['error' => 'not_found'], 404)
                : $this->json($status);
        } catch (\Throwable $exception) {
            // A binding, mount, or publication-state failure must never be
            // interpreted by the browser as evidence of a ready document.
            return $this->json(['error' => 'status_unavailable'], 503);
        }
    }

    private function json(array $data, int $status = 200): JSONResponse {
        return new JSONResponse($data, $status, ['Cache-Control' => 'no-store']);
    }
}
