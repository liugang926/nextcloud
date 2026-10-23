<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\OperationalStatusService;
use OCA\IntegrationWeknora\Service\ConnectionService;
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
        private ConnectionService $connection,
    ) {
        parent::__construct('integration_weknora', $request);
    }

    public function index(): JSONResponse {
        $user = $this->session->getUser();
        if ($user === null || !$this->groups->isAdmin($user->getUID())) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            return $this->json($this->snapshot());
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'diagnostics_unavailable'], 503);
        }
    }

    public function connection(): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            return $this->json($this->connectionDiagnostics());
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'diagnostics_unavailable'], 503);
        }
    }

    public function syncHealth(): JSONResponse {
        if (!$this->isAdmin()) {
            return $this->json(['error' => 'forbidden'], 403);
        }
        try {
            $snapshot = $this->status->snapshot();
            return $this->json([
                'checked_at' => $snapshot['checked_at'] ?? time(),
                'binding_count' => $snapshot['binding_count'] ?? 0,
                'binding_roots_available' => $snapshot['binding_roots_available'] ?? false,
                'outbox_pending_delivery_hints' => $snapshot['outbox_pending_delivery_hints'] ?? 0,
                'outbox_pending_application_hints' => $snapshot['outbox_pending_application_hints'] ?? 0,
                'event_connections' => $snapshot['event_connections'] ?? [],
                'processing' => $snapshot['processing'] ?? null,
            ]);
        } catch (\Throwable $exception) {
            return $this->json(['error' => 'diagnostics_unavailable'], 503);
        }
    }

    private function snapshot(): array {
        $snapshot = $this->status->snapshot();
        $connection = $this->connection->status();
        $snapshot['connection'] = [
            'connected' => $connection['connected'] ?? false,
            'app_version' => $connection['app_version'] ?? '',
            'bundle_version' => $connection['bundle_version'] ?? '',
            'contract_version' => $connection['contract_version'] ?? null,
            'compatible' => $connection['compatible'] ?? false,
            'binding_allowed' => $connection['binding_allowed'] ?? false,
            'health' => $connection['health'] ?? null,
            'spaces' => array_map(static fn (array $space): array => [
                'tenant_id' => (string)($space['tenant_id'] ?? ''),
                'name' => (string)($space['name'] ?? ''),
                'processing_template_name' => (string)($space['processing_template_name'] ?? ''),
                'visible' => true,
            ], array_values(array_filter($connection['spaces'] ?? [], 'is_array'))),
        ];
        return $snapshot;
    }

    private function connectionDiagnostics(): array {
        $status = $this->connection->status();
        return [
            'connected' => $status['connected'] ?? false,
            'compatible' => $status['compatible'] ?? false,
            'app_version' => $status['app_version'] ?? '',
            'bundle_version' => $status['bundle_version'] ?? '',
            'contract_version' => $status['contract_version'] ?? null,
            'origin' => $status['origin'] ?? null,
            'binding_allowed' => $status['binding_allowed'] ?? false,
            'health' => $status['health'] ?? null,
            'spaces' => array_map(static fn (array $space): array => [
                'tenant_id' => (string)($space['tenant_id'] ?? ''),
                'name' => (string)($space['name'] ?? ''),
                'processing_template_name' => (string)($space['processing_template_name'] ?? ''),
                'visible' => true,
            ], array_values(array_filter($status['spaces'] ?? [], 'is_array'))),
        ];
    }

    private function isAdmin(): bool {
        $user = $this->session->getUser();
        return $user !== null && $this->groups->isAdmin($user->getUID());
    }

    private function json(array $data, int $status = 200): JSONResponse {
        return new JSONResponse($data, $status, ['Cache-Control' => 'no-store']);
    }
}
