<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\PairedServiceToken;
use OCA\IntegrationWeknora\Service\SourceAuthorizationService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoCSRFRequired;
use OCP\AppFramework\Http\Attribute\PublicPage;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IRequest;

/** Paired connector requests a fresh decision for one verified AD principal. */
final class AuthorizationController extends Controller {
    public function __construct(
        IRequest $request,
        private PairedServiceToken $serviceToken,
        private SourceAuthorizationService $authorization,
    ) {
        parent::__construct('integration_weknora', $request);
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function authorize(string $id): JSONResponse {
        if (!$this->serviceToken->verify($this->request, $id)) {
            return $this->json(['allow' => false, 'error' => 'unauthorized'], 401,
                ['WWW-Authenticate' => 'Bearer realm="WeKnora Integration"']);
        }

        $directoryId = $this->request->getParam('directory_id');
        $objectGuid = $this->request->getParam('object_guid');
        $fileId = $this->request->getParam('file_id');
        $contentRevision = $this->request->getParam('content_revision', '0');
        if (!is_string($directoryId) || !is_string($objectGuid) || !is_int($fileId) ||
            !is_string($contentRevision) || !preg_match('/\A(?:0|[1-9][0-9]{0,18})\z/D', $contentRevision)) {
            return $this->json(['allow' => false, 'error' => 'invalid_request'], 400);
        }
        try {
            return $this->json($this->authorization->authorize($id, $directoryId, $objectGuid, $fileId, $contentRevision));
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['allow' => false, 'error' => 'invalid_request'], 400);
        } catch (\Throwable $exception) {
            // Unknown source, directory, ACL, DB or mount state is a denial.
            return $this->json(['allow' => false, 'error' => 'authorization_unavailable'], 503);
        }
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function authorizeRoot(string $id): JSONResponse {
        if (!$this->serviceToken->verify($this->request, $id)) {
            return $this->json(['allow' => false, 'error' => 'unauthorized'], 401,
                ['WWW-Authenticate' => 'Bearer realm="WeKnora Integration"']);
        }
        $directoryId = $this->request->getParam('directory_id');
        $objectGuid = $this->request->getParam('object_guid');
        if (!is_string($directoryId) || !is_string($objectGuid)) {
            return $this->json(['allow' => false, 'error' => 'invalid_request'], 400);
        }
        try { return $this->json($this->authorization->authorizeRoot($id, $directoryId, $objectGuid)); }
        catch (\InvalidArgumentException $e) { return $this->json(['allow' => false, 'error' => 'invalid_request'], 400); }
        catch (\Throwable $e) { return $this->json(['allow' => false, 'error' => 'authorization_unavailable'], 503); }
    }

    private function json(array $data, int $status = 200, array $headers = []): JSONResponse {
        return new JSONResponse($data, $status, ['Cache-Control' => 'no-store'] + $headers);
    }
}
