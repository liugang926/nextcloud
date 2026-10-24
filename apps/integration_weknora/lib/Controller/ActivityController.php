<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\ActivityService;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\EmployeeFileStatusService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IRequest;
use OCP\IUserSession;

/** Permission-filtered recent activity for the Files sidebar. */
final class ActivityController extends Controller {
    public function __construct(
        IRequest $request,
        private IUserSession $session,
        private DirectoryPublicationService $directories,
        private EmployeeFileStatusService $files,
        private ActivityService $activity,
    ) {
        parent::__construct('integration_weknora', $request);
    }

    #[NoAdminRequired]
    public function folder(int $folderId): JSONResponse {
        return $this->run(function (string $uid) use ($folderId): array {
            $status = $this->directories->show($uid, $folderId);
            $bindingId = $status['binding_id'] ?? null;
            if (!is_string($bindingId) || $bindingId === '') {
                return ['items' => [], 'next_cursor' => null];
            }
            return $this->activity->forScope($uid, $bindingId, null,
                $this->cursor(), $this->limit());
        });
    }

    #[NoAdminRequired]
    public function file(int $fileId): JSONResponse {
        return $this->run(function (string $uid) use ($fileId): array {
            $bindingId = $this->files->activityBinding($uid, $fileId);
            if ($bindingId === null) {
                throw new \OutOfBoundsException('File activity is not visible');
            }
            return $this->activity->forScope($uid, $bindingId, $fileId,
                $this->cursor(), $this->limit());
        });
    }

    private function run(callable $fn): JSONResponse {
        $user = $this->session->getUser();
        if ($user === null) {
            return $this->json(['error' => 'unauthorized'], 401);
        }
        try {
            return $this->json($fn($user->getUID()));
        } catch (\OutOfBoundsException) {
            return $this->json(['error' => 'not_found'], 404);
        } catch (\InvalidArgumentException) {
            return $this->json(['error' => 'invalid_activity_request'], 400);
        } catch (\Throwable) {
            return $this->json(['error' => 'activity_unavailable'], 503);
        }
    }

    private function cursor(): ?string {
        $cursor = $this->request->getParam('cursor');
        if ($cursor === null || $cursor === '') {
            return null;
        }
        if (!is_string($cursor)) {
            throw new \InvalidArgumentException('Invalid activity cursor');
        }
        return $cursor;
    }

    private function limit(): int {
        $limit = $this->request->getParam('limit', 20);
        if (!is_int($limit) && !(is_string($limit) && ctype_digit($limit))) {
            throw new \InvalidArgumentException('Invalid activity page');
        }
        return max(1, min(100, (int)$limit));
    }

    private function json(array $data, int $status = 200): JSONResponse {
        return new JSONResponse($data, $status, ['Cache-Control' => 'no-store']);
    }
}
