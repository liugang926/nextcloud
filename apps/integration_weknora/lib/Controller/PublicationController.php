<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\FilePublicationStateService;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\ChangeOutboxService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\JSONResponse;
use OCP\Files\File;
use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IUserSession;
use Psr\Log\LoggerInterface;

/** Session and CSRF protected administrator operations. */
final class PublicationController extends Controller {
    private const APP_ID = 'integration_weknora';

    public function __construct(
        IRequest $request,
        private BindingRegistryService $bindings,
        private IRootFolder $rootFolder,
        private IUserSession $userSession,
        private IGroupManager $groupManager,
        private FilePublicationStateService $states,
        private ChangeOutboxService $outbox,
        private LoggerInterface $logger,
    ) {
        parent::__construct(self::APP_ID, $request);
    }

    public function state(string $id, int $fileId): JSONResponse {
        if ($this->adminUid() === null) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        return $this->operate($id, $fileId, null, null);
    }

    public function withdraw(string $id, int $fileId): JSONResponse {
        $actorUid = $this->adminUid();
        if ($actorUid === null) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        return $this->operate($id, $fileId, $actorUid, 'withdraw');
    }

    public function republish(string $id, int $fileId): JSONResponse {
        $actorUid = $this->adminUid();
        if ($actorUid === null) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        return $this->operate($id, $fileId, $actorUid, 'republish');
    }

    private function operate(string $id, int $fileId, ?string $actorUid, ?string $action): JSONResponse {
        if ($fileId < 1) {
            return $this->json(['error' => 'not_found'], 404);
        }
        try {
            $binding = $this->findBinding($id);
            if ($binding === null) {
                return $this->json(['error' => 'not_found'], 404);
            }
            // A withdrawal is a denial for this binding and file identity.
            // It must remain possible after deletion, move, or source outage.
            // Republish still requires a currently readable bound file.
            if ($action === 'republish' && !$this->isCurrentBoundFile($binding, $fileId)) {
                return $this->json(['error' => 'not_found'], 404);
            }
            if ($action === null && !$this->isCurrentBoundFile($binding, $fileId) &&
                !$this->states->hasRecordedState($id, $fileId)) {
                return $this->json(['error' => 'not_found'], 404);
            }
            $hintRecorded = null;
            if ($action === 'withdraw' && $actorUid !== null) {
                $this->states->withdraw($id, $fileId, $actorUid);
            } elseif ($action === 'republish' && $actorUid !== null) {
                $this->states->republish($id, $fileId, $actorUid);
            }
            if ($action !== null) {
                $hintRecorded = true;
                try {
                    // The state transaction has committed. A file-scoped hint
                    // asks WeKnora to recheck the authoritative manifest and
                    // this file; it is never evidence of publication state.
                    // Repeat on idempotent requests to repair a failed append.
                    $this->outbox->append($id, $fileId, 'reconcile');
                } catch (\Throwable $exception) {
                    $hintRecorded = false;
                    try {
                        $this->logger->error('File publication reconciliation hint could not be recorded', [
                            'binding_id' => $id,
                            'file_id' => $fileId,
                            'action' => $action,
                            'error_type' => get_class($exception),
                        ]);
                    } catch (\Throwable) {
                        // Diagnostics must not hide a committed publication change.
                    }
                }
            }
            $decision = $this->states->getDecision($id, $fileId);
            $state = $decision['state'];
            $response = [
                'binding_id' => $id,
                'file_id' => $fileId,
                'state' => $state,
                'excluded' => $state === 'withdrawn',
                'decision_audit_id' => $decision['decision_audit_id'],
            ];
            if ($hintRecorded !== null) {
                $response['reconcile_hint_recorded'] = $hintRecorded;
            }
            return $this->json($response);
        } catch (\UnexpectedValueException $exception) {
            return $this->json(['error' => 'invalid_configuration'], 503);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'publication_state_unavailable'], 503);
        }
    }

    private function adminUid(): ?string {
        $user = $this->userSession->getUser();
        if ($user === null || !$this->groupManager->isAdmin($user->getUID())) {
            return null;
        }
        return $user->getUID();
    }

    /** @return array{id: string, owner_uid: string, root_file_id: int}|null */
    private function findBinding(string $id): ?array {
        foreach ($this->bindings->listBindings() as $binding) {
            if ($binding['id'] === $id) {
                return $binding;
            }
        }
        return null;
    }

    /** @param array{owner_uid: string, root_file_id: int} $binding */
    private function isCurrentBoundFile(array $binding, int $fileId): bool {
        $userFolder = $this->rootFolder->getUserFolder($binding['owner_uid']);
        if ($userFolder->getId() === $binding['root_file_id']) {
            return false;
        }
        $bindingRoot = null;
        foreach ($userFolder->getById($binding['root_file_id']) as $node) {
            if ($node instanceof Folder && $node->getId() === $binding['root_file_id'] &&
                $userFolder->isSubNode($node)) {
                $bindingRoot = $node;
                break;
            }
        }
        if ($bindingRoot === null) {
            return false;
        }
        foreach ($bindingRoot->getById($fileId) as $node) {
            if ($node instanceof File && $node->getId() === $fileId &&
                $bindingRoot->isSubNode($node) && $userFolder->isSubNode($node) &&
                $node->isReadable()) {
                return true;
            }
        }
        return false;
    }

    private function json(array $data, int $status = 200): JSONResponse {
        return new JSONResponse($data, $status, ['Cache-Control' => 'no-store']);
    }
}
