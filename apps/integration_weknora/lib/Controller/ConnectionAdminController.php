<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\ConnectionService;
use OCA\IntegrationWeknora\Service\PrincipalSearchService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IUserSession;

/** Administrator session and CSRF protected WeKnora connection setup. */
final class ConnectionAdminController extends Controller {
    public function __construct(
        IRequest $request,
        private IUserSession $session,
        private IGroupManager $groups,
        private PrincipalSearchService $principals,
        private ConnectionService $connection,
    ) {
        parent::__construct('integration_weknora', $request);
    }

    public function status(): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        return $this->json($this->connection->status());
    }

    public function discover(): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $origin = $this->request->getParam('origin');
        $token = $this->request->getParam('token');
        if (!is_string($origin) || !is_string($token)) {
            return $this->json(['error' => 'invalid_connection'], 400);
        }
        try {
            $query = $this->request->getParam('q', '');
            $cursor = $this->request->getParam('cursor', '');
            $limit = $this->request->getParam('limit', 50);
            if (!is_string($query) || !is_string($cursor) ||
                (!is_int($limit) && !(is_string($limit) && ctype_digit($limit)))) {
                throw new \InvalidArgumentException('Invalid workspace query');
            }
            return $this->json($this->connection->discover($origin, $token,
                $query, $cursor, (int)$limit));
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_connection'], 400);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'connection_denied'], 403);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'connection_unavailable'], 503);
        }
    }

    public function principals(): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $query = $this->request->getParam('query', '');
        if (!is_string($query) || strlen($query) > 64) {
            return $this->json(['error' => 'invalid_query'], 400);
        }
        return $this->json($this->principals->search(trim($query)));
    }

    public function connect(): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $user = $this->session->getUser();
        $origin = $this->request->getParam('origin');
        $token = $this->request->getParam('token');
        $spaces = $this->request->getParam('spaces');
        if ($user === null || !is_string($origin) || !is_string($token) ||
            !is_array($spaces) || $spaces === []) {
            return $this->json(['error' => 'invalid_connection'], 400);
        }
        try {
            return $this->json($this->connection->connect($origin, $token, $spaces, $user->getUID()));
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_connection'], 400);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'connection_denied'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'connection_unavailable'], 503);
        }
    }

    public function visibility(): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $spaces = $this->request->getParam('spaces');
        if (!is_array($spaces) || $spaces === []) {
            return $this->json(['error' => 'invalid_visibility'], 400);
        }
        try {
            $user = $this->session->getUser();
            return $this->json($this->connection->updateVisibility(
                $spaces, $user?->getUID() ?? 'system'));
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_visibility'], 400);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'visibility_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'visibility_unavailable'], 503);
        }
    }

    public function cleanupCredentials(): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            $user = $this->session->getUser();
            return $this->json($this->connection->cleanupStaleCredentials(
                $user?->getUID() ?? 'system'));
        } catch (\Throwable) {
            return $this->json(['error' => 'connection_unavailable'], 503);
        }
    }

    public function disconnect(): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            $user = $this->session->getUser();
            return $this->json($this->connection->disconnect($user?->getUID() ?? 'system'));
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'connection_denied'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'connection_unavailable'], 503);
        }
    }

    private function isAdmin(): bool {
        $user = $this->session->getUser();
        return $user !== null && $this->groups->isAdmin($user->getUID());
    }

    private function json(array $data, int $status = 200): JSONResponse {
        return new JSONResponse($data, $status, [
            'Cache-Control' => 'no-store',
            'Referrer-Policy' => 'no-referrer',
        ]);
    }
}
