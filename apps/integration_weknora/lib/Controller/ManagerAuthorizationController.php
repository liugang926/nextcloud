<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\CurrentManagerDeniedException;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\ManagerAuthorizationProtocol;
use OCA\IntegrationWeknora\Service\PairedServiceToken;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoCSRFRequired;
use OCP\AppFramework\Http\Attribute\PublicPage;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IRequest;

final class ManagerAuthorizationController extends Controller {
    public function __construct(IRequest $request, private PairedServiceToken $token,
        private DirectoryPublicationService $publication) {
        parent::__construct('integration_weknora', $request);
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function authorize(string $id): JSONResponse {
        if (!$this->token->verify($this->request, $id)) {
            return $this->json(['allow' => false, 'error' => 'unauthorized'], 401);
        }
        try {
            // Authenticate the actual body first, then parse those same bytes.
            // Merged query/form getParam values cannot establish a JSON scope.
            $raw = file_get_contents('php://input', false, null, 0, ManagerAuthorizationProtocol::MAX_RAW_REQUEST_BYTES + 1);
            if ($raw === false) { return $this->json(['allow' => false, 'reason' => 'manager_unavailable'], 503); }
            $scope = ManagerAuthorizationProtocol::rawRequest($id, $this->request->getMethod(),
                $this->request->getHeader('Content-Type'), $this->request->getRequestUri(), $raw);
        } catch (\InvalidArgumentException | \JsonException $e) {
            return $this->json(['allow' => false, 'error' => 'invalid_request'], 400);
        }
        try {
            return $this->json($this->publication->authorizeCurrentManager($id, $scope));
        } catch (CurrentManagerDeniedException $e) {
            return $this->json(ManagerAuthorizationProtocol::decision($id, $scope, false, 'manager_denied'), 403);
        } catch (\Throwable $e) {
            return $this->json(ManagerAuthorizationProtocol::decision($id, $scope, false, 'manager_unavailable'), 503);
        }
    }

    private function json(array $body, int $status = 200): JSONResponse {
        return new JSONResponse($body, $status, ['Cache-Control' => 'no-store']);
    }
}
