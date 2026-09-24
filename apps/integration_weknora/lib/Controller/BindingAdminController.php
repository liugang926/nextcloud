<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\ChangeOutboxService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IUserSession;
use Psr\Log\LoggerInterface;

/** Session and CSRF protected configuration of bounded publication roots. */
final class BindingAdminController extends Controller {
    public function __construct(
        IRequest $request,
        private IUserSession $userSession,
        private IGroupManager $groupManager,
        private BindingRegistryService $bindings,
        private ChangeOutboxService $outbox,
        private LoggerInterface $logger,
    ) {
        parent::__construct('integration_weknora', $request);
    }

    public function index(): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            return $this->json(['bindings' => $this->bindings->listBindings()]);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'invalid_configuration'], 503);
        }
    }

    public function save(): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        $id = $this->request->getParam('id');
        $name = $this->request->getParam('name');
        $ownerUid = $this->request->getParam('owner_uid');
        $rootFileId = $this->request->getParam('root_file_id');
        if (!is_string($id) || !is_string($name) || !is_string($ownerUid) || !is_int($rootFileId)) {
            return $this->json(['error' => 'invalid_binding'], 400);
        }
        try {
            $created = $this->bindings->save($id, $name, $ownerUid, $rootFileId);
            $saved = null;
            foreach ($this->bindings->listBindings() as $binding) {
                if ($binding['id'] === $id) {
                    $saved = $binding;
                    break;
                }
            }
            if ($saved === null) {
                throw new \UnexpectedValueException('Saved binding is missing');
            }
            return $this->json([
                'binding' => $saved,
            ], $created ? 201 : 200);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_binding'], 400);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'binding_conflict'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'binding_registry_unavailable'], 503);
        }
    }

    public function remove(string $id): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            $revokedKeys = $this->bindings->remove($id);
            return $revokedKeys === null
                ? $this->json(['error' => 'binding_not_found'], 404)
                : $this->json(['removed' => true, 'revoked_keys' => $revokedKeys]);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_binding'], 400);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'paired_binding_decommission_required'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'binding_registry_unavailable'], 503);
        }
    }

    public function stop(string $id): JSONResponse {
        return $this->publicationTransition($id, 'stopped');
    }

    public function resume(string $id): JSONResponse {
        return $this->publicationTransition($id, 'active');
    }

    private function publicationTransition(string $id, string $state): JSONResponse {
        $user = $this->userSession->getUser();
        if ($user === null || !$this->groupManager->isAdmin($user->getUID())) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            $result = $this->bindings->setPublicationState($id, $state, $user->getUID());
            if ($result === null) {
                return $this->json(['error' => 'binding_not_found'], 404);
            }
            $hintRecorded = true;
            try {
                // Append on idempotent retry too: if the first post-commit
                // append failed, the administrator can repair the hint simply
                // by repeating the same explicit operation.
                $this->outbox->append($id, null, 'reconcile');
            } catch (\Throwable $exception) {
                $hintRecorded = false;
                $this->logger->error('Binding publication reconciliation hint could not be recorded', [
                    'binding_id' => $id,
                    'error_type' => get_class($exception),
                ]);
            }
            return $this->json(['binding_id' => $id] + $result + [
                'reconcile_hint_recorded' => $hintRecorded,
            ]);
        } catch (\InvalidArgumentException $exception) {
            return $this->json(['error' => 'invalid_binding'], 400);
        } catch (\DomainException $exception) {
            return $this->json(['error' => 'binding_unavailable'], 409);
        } catch (\UnexpectedValueException $exception) {
            return $this->json(['error' => 'binding_unavailable'], 409);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'binding_registry_unavailable'], 503);
        }
    }

    private function isAdmin(): bool {
        $user = $this->userSession->getUser();
        return $user !== null && $this->groupManager->isAdmin($user->getUID());
    }

    private function json(array $data, int $status = 200): JSONResponse {
        return new JSONResponse($data, $status, ['Cache-Control' => 'no-store']);
    }
}
