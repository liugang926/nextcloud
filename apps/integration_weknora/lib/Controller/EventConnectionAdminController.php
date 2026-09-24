<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\BindingPublicationStoppedException;
use OCA\IntegrationWeknora\Service\EventConnectionService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IUserSession;

/** Administrator session and CSRF protected sender pairing. */
final class EventConnectionAdminController extends Controller {
    public function __construct(
        IRequest $request,
        private IUserSession $userSession,
        private IGroupManager $groupManager,
        private EventConnectionService $connections,
    ) {
        parent::__construct('integration_weknora', $request);
    }

    public function show(string $id): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            return $this->json($this->connections->status($id));
        } catch (\OutOfBoundsException $exception) {
            return $this->json(['error' => 'connection_not_found'], 404);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_binding_id'], 400);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'connection_unavailable'], 503);
        }
    }

    public function configure(string $id): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $fields = [];
        foreach (['binding_id', 'nextcloud_instance_id', 'connection_id',
            'key_id', 'secret', 'receiver_url'] as $name) {
            $value = $this->request->getParam($name);
            if (!is_string($value)) {
                return $this->json(['error' => 'invalid_connection'], 400);
            }
            $fields[$name] = $value;
        }
        try {
            $configured = $this->connections->configure(
                $id,
                $fields['binding_id'],
                $fields['nextcloud_instance_id'],
                $fields['connection_id'],
                $fields['key_id'],
                $fields['secret'],
                $fields['receiver_url'],
            );
            return $this->json($configured['status'], $configured['created'] ? 201 : 200);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_connection'], 400);
        } catch (BindingPublicationStoppedException $exception) {
            return $this->json(['error' => 'publication_stopped'], 423);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'connection_conflict'], 409);
        } catch (\UnexpectedValueException $exception) {
            return $this->json(['error' => 'binding_unavailable'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'connection_unavailable'], 503);
        }
    }

    public function revoke(string $id): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            return $this->connections->revoke($id)
                ? $this->json(['revoked' => true])
                : $this->json(['error' => 'connection_not_found'], 404);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_binding_id'], 400);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'connection_unavailable'], 503);
        }
    }

    public function retry(string $id): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $connectionId = $this->request->getParam('connection_id');
        $keyId = $this->request->getParam('key_id');
        $receivedId = $this->request->getParam('received_through_event_id');
        if (!is_string($connectionId) || !is_string($keyId) || !is_string($receivedId)) {
            return $this->json(['error' => 'invalid_connection'], 400);
        }
        try {
            return $this->json($this->connections->retryPaused($id, $connectionId, $keyId, $receivedId));
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_connection'], 400);
        } catch (\OutOfBoundsException $exception) {
            return $this->json(['error' => 'connection_not_found'], 404);
        } catch (BindingPublicationStoppedException $exception) {
            return $this->json(['error' => 'publication_stopped'], 423);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'connection_changed'], 409);
        } catch (\UnexpectedValueException $exception) {
            return $this->json(['error' => 'binding_unavailable'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'connection_unavailable'], 503);
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
