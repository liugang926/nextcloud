<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\OperationalStatusService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IUserSession;

/** Source-side operational counters for the Nextcloud administrator. */
final class DiagnosticsController extends Controller {
    public function __construct(
        IRequest $request,
        private IUserSession $session,
        private IGroupManager $groups,
        private OperationalStatusService $status,
    ) {
        parent::__construct('integration_weknora', $request);
    }

    public function index(): JSONResponse {
        $user = $this->session->getUser();
        if ($user === null || !$this->groups->isAdmin($user->getUID())) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            return $this->json($this->status->snapshot());
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'diagnostics_unavailable'], 503);
        }
    }

    private function json(array $data, int $status = 200): JSONResponse {
        return new JSONResponse($data, $status, ['Cache-Control' => 'no-store']);
    }
}
