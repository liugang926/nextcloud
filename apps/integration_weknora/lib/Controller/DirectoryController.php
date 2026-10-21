<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\ConnectionService;
use OCA\IntegrationWeknora\Service\FolderSummaryService;
use OCA\IntegrationWeknora\Service\UnsupportedPublicationAclException;
use OCA\IntegrationWeknora\Service\StaleFolderSummaryException;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IRequest;
use OCP\IUserSession;

/** All writes retain Nextcloud's session and CSRF middleware. */
final class DirectoryController extends Controller {
    public function __construct(IRequest $request, private IUserSession $session,
        private DirectoryPublicationService $directories, private FolderSummaryService $summaries,
        private ConnectionService $connection) {
        parent::__construct('integration_weknora', $request);
    }
    #[NoAdminRequired]
    public function show(int $folderId): JSONResponse {
        return $this->run(function (string $uid) use ($folderId): array {
            $connected = $this->connection->hasConnection();
            $canBind = $connected && $this->connection->canManage($uid, $folderId, false);
            $canCreate = $connected && $this->connection->canManage($uid, $folderId, true);
            $status = $this->directories->show($uid, $folderId);
            if ($canBind || $canCreate) {
                $status['connection_available'] = true;
                $status['can_bind'] = $canBind;
                $status['can_create'] = $canCreate;
            }
            return $status + ($this->summaries->forUser($uid, $folderId,
                is_string($this->request->getParam('cursor'))
                    ? $this->request->getParam('cursor') : null) ?? []);
        });
    }
    #[NoAdminRequired]
    public function candidates(int $folderId): JSONResponse {
        return $this->run(function (string $uid) use ($folderId): array {
            $forCreation = $this->request->getParam('for_creation') === '1';
            $allowed = null;
            if ($this->connection->hasConnection()) {
                $this->connection->ensureRule($uid, $folderId, $forCreation);
                $allowed = $this->connection->visibleTenantIds($uid);
            }
            $result = $this->directories->candidates($uid, $folderId, $forCreation, $allowed);
            return $this->connection->hasConnection()
                ? $this->connection->filterCandidates($uid, $result)
                : $result;
        });
    }
    #[NoAdminRequired]
    public function associate(int $folderId): JSONResponse {
        return $this->run(function (string $uid) use ($folderId): array {
            if ($this->connection->hasConnection()) {
                $this->connection->ensureRule($uid, $folderId, false);
            }
            $choice = $this->choice();
            $this->connection->assertTargetAllowed($uid, $choice);
            return ['operation' => $this->directories->associate($uid, $folderId, $choice)];
        }, 202);
    }
    #[NoAdminRequired]
    public function create(int $folderId): JSONResponse {
        return $this->run(function (string $uid) use ($folderId): array {
            $name = $this->request->getParam('folder_name');
            if (!is_string($name)) { throw new \InvalidArgumentException('Invalid folder name'); }
            if ($this->connection->hasConnection()) {
                $this->connection->ensureRule($uid, $folderId, true);
            }
            $choice = $this->choice();
            $this->connection->assertTargetAllowed($uid, $choice);
            return ['operation' => $this->directories->create($uid, $folderId, $name, $choice)];
        }, 202);
    }
    #[NoAdminRequired]
    public function retry(string $operationId): JSONResponse {
        return $this->run(fn (string $uid): array => ['operation' => $this->directories->retry($uid, $operationId)], 202);
    }
    #[NoAdminRequired]
    public function sync(int $folderId): JSONResponse {
        return $this->run(function (string $uid) use ($folderId): array {
            $enabled = $this->request->getParam('sync_enabled');
            $revision = $this->request->getParam('expected_revision');
            if (!is_bool($enabled) || !is_int($revision) || $revision < 0) { throw new \InvalidArgumentException('Invalid sync change'); }
            return $this->directories->sync($uid, $folderId, $enabled, $revision);
        });
    }
    private function choice(): array {
        return ['operation_id' => $this->request->getParam('operation_id'), 'mode' => $this->request->getParam('mode'),
            'name' => $this->request->getParam('name'), 'knowledge_base_id' => $this->request->getParam('knowledge_base_id'),
            'tenant_id' => $this->request->getParam('tenant_id'), 'processing_template_id' => $this->request->getParam('processing_template_id')];
    }
    private function run(callable $fn, int $success = 200): JSONResponse {
        $user = $this->session->getUser();
        if ($user === null) { return $this->json(['error' => 'unauthorized'], 401); }
        try { return $this->json($fn($user->getUID()), $success); }
        catch (\OutOfBoundsException $e) { return $this->json(['error' => 'not_found'], 404); }
        catch (\InvalidArgumentException $e) { return $this->json(['error' => 'invalid_directory_request'], 400); }
        catch (UnsupportedPublicationAclException $e) { return $this->json(['error' => 'unsupported_source_acl'], 409); }
        catch (StaleFolderSummaryException $e) { return $this->json(['error' => 'summary_generation_changed'], 409); }
        catch (\DomainException $e) { return $this->json(['error' => 'permission_or_target_conflict'], 409); }
        catch (\Throwable $e) { return $this->json(['error' => 'directory_unavailable'], 503); }
    }
    private function json(array $data, int $status): JSONResponse {
        return new JSONResponse($data, $status, ['Cache-Control' => 'no-store']);
    }
}
