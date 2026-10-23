<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\ConnectionService;
use OCA\IntegrationWeknora\Service\CandidateRevisionChangedException;
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
            if ($connected && $canBind && ($status['source_state'] ?? '') === 'bound') {
                try {
                    $this->connection->ensureRule($uid, $folderId, false, false);
                    $status = $this->directories->show($uid, $folderId);
                } catch (\Throwable $ignored) {}
            }
            $summary = $this->summaries->forUser($uid, $folderId,
                is_string($this->request->getParam('cursor'))
                    ? $this->request->getParam('cursor') : null);
            if ($canBind || $canCreate || ($status['manageable'] ?? false)) {
                $status['connection_available'] = $connected;
                $status['can_bind'] = $canBind || ($status['permissions']['can_bind_directory'] ?? false);
                $status['can_create'] = $canCreate || ($status['can_create'] ?? false);
            }
            return $this->directories->applySummary($status, $summary) + ($summary ?? []);
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
            $limit = $this->request->getParam('limit', 50);
            if (!is_int($limit) && !(is_string($limit) && ctype_digit($limit))) {
                throw new \InvalidArgumentException('Invalid candidate page');
            }
            return $this->directories->candidates($uid, $folderId, $forCreation, $allowed,
                is_string($this->request->getParam('q')) ? $this->request->getParam('q') : '',
                is_string($this->request->getParam('cursor')) ? $this->request->getParam('cursor') : '',
                (int)$limit);
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
    #[NoAdminRequired]
    public function syncNow(int $folderId): JSONResponse {
        return $this->run(fn (string $uid): array => $this->directories->syncNow($uid, $folderId), 202);
    }
    #[NoAdminRequired]
    public function bindingDiagnostics(int $folderId): JSONResponse {
        return $this->run(function (string $uid) use ($folderId): array {
            $status = $this->directories->show($uid, $folderId);
            if (($status['permissions']['can_bind_directory'] ?? false) !== true &&
                ($status['permissions']['can_admin_connection'] ?? false) !== true) {
                throw new \DomainException('Publication management is forbidden');
            }
            return [
                'folder_id' => $folderId,
                'binding_state' => $status['binding_state'] ?? 'unbound',
                'content_sync_state' => $status['content_sync_state'] ?? 'not_started',
                'overview_state' => $status['overview_state'] ?? 'unbound',
                'publication_state' => $status['publication_state'] ?? null,
                'pairing_state' => $status['pairing_state'] ?? null,
                'content_sync' => $status['content_sync'] ?? null,
                'operation' => $status['operation'] ?? null,
                'checked_at' => time(),
            ];
        });
    }
    private function choice(): array {
        return ['operation_id' => $this->request->getParam('operation_id'), 'mode' => $this->request->getParam('mode'),
            'name' => $this->request->getParam('name'), 'knowledge_base_id' => $this->request->getParam('knowledge_base_id'),
            'tenant_id' => $this->request->getParam('tenant_id'), 'processing_template_id' => $this->request->getParam('processing_template_id'),
            'candidate_revision' => $this->request->getParam('target_candidate_revision',
                $this->request->getParam('candidate_revision'))];
    }
    private function run(callable $fn, int $success = 200): JSONResponse {
        $user = $this->session->getUser();
        if ($user === null) { return $this->json(['error' => 'unauthorized'], 401); }
        try { return $this->json($fn($user->getUID()), $success); }
        catch (\OutOfBoundsException $e) { return $this->json(['error' => 'not_found'], 404); }
        catch (\InvalidArgumentException $e) { return $this->json(['error' => 'invalid_directory_request'], 400); }
        catch (UnsupportedPublicationAclException $e) { return $this->json($this->reason('unsupported_source_acl'), 409); }
        catch (CandidateRevisionChangedException $e) { return $this->json($this->reason('candidate_revision_changed'), 409); }
        catch (StaleFolderSummaryException $e) { return $this->json(['error' => 'summary_generation_changed'], 409); }
        catch (\DomainException $e) {
            $reason = str_contains($e->getMessage(), 'identity')
                ? 'directory_identity_missing'
                : (str_contains($e->getMessage(), 'Worker')
                    ? 'connection_degraded'
                    : (str_contains($e->getMessage(), 'overlap') ||
                       str_contains($e->getMessage(), 'already has a publication intent')
                        ? 'binding_root_conflict'
                        : (str_contains($e->getMessage(), 'management is forbidden') ||
                           str_contains($e->getMessage(), 'not visible')
                            ? 'directory_permission_denied' : 'permission_or_target_conflict')));
            return $this->json($this->reason($reason), 409);
        }
        catch (\Throwable $e) { return $this->json(['error' => 'directory_unavailable'], 503); }
    }
    private function reason(string $code): array {
        $catalog = [
            'directory_identity_missing' => ['当前用户没有有效的目录身份映射。', '检查 LDAP/AD 身份同步。'],
            'directory_permission_denied' => ['当前目录不可创建、修改或空间不在可见范围。', '申请目录管理权限，或联系管理员调整空间可见范围。'],
            'connection_degraded' => ['后台同步组件尚未验证，暂不能开始新的目录绑定。', '在管理端检查 cron、dispatcher 和 worker。'],
            'unsupported_source_acl' => ['当前挂载或分享权限模式不受支持。', '联系管理员检查目录挂载方式。'],
            'binding_root_conflict' => ['当前目录已有规则或绑定。', '查看现有绑定后再决定是否迁移。'],
            'candidate_revision_changed' => ['候选知识库已发生变化。', '取消本次表单，刷新候选后明确重新选择目标。'],
            'permission_or_target_conflict' => ['目录规则、管理委派或目标知识库存在冲突。', '查看当前绑定或选择其他目标。'],
        ];
        [$reason, $action] = $catalog[$code] ?? ['操作未能完成。', '联系管理员检查连接和目录状态。'];
        return ['error' => $code, 'reason_code' => $code, 'reason' => $reason,
            'next_action' => $action, 'request_id' => 'req_' . bin2hex(random_bytes(8))];
    }
    private function json(array $data, int $status): JSONResponse {
        return new JSONResponse($data, $status, ['Cache-Control' => 'no-store']);
    }
}
