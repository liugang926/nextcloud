<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\ChangeOutboxService;
use OCA\IntegrationWeknora\Service\PairedServiceToken;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoCSRFRequired;
use OCP\AppFramework\Http\Attribute\PublicPage;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IConfig;
use OCP\IRequest;

/** Machine-only read API for durable hints; every deletion needs authority checks. */
final class ChangesController extends Controller {
    private const APP_ID = 'integration_weknora';

    public function __construct(
        IRequest $request,
        private IConfig $config,
        private PairedServiceToken $serviceToken,
        private BindingRegistryService $bindings,
        private ChangeOutboxService $outbox,
    ) {
        parent::__construct(self::APP_ID, $request);
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function index(string $id): JSONResponse {
        if (!$this->serviceToken->verify($this->request, $id)) {
            return $this->json(['error' => 'unauthorized'], 401, [
                'WWW-Authenticate' => 'Bearer realm="WeKnora Integration"',
            ]);
        }
        try {
            $found = false;
            foreach ($this->bindings->listBindings() as $binding) {
                if ($binding['id'] === $id) {
                    $found = true;
                    break;
                }
            }
            if (!$found) {
                return $this->json(['error' => 'not_found'], 404);
            }
            $afterId = $this->decodeCursor($id, $this->request->getParam('cursor', ''));
            if ($afterId === null) {
                return $this->json(['error' => 'invalid_cursor'], 400);
            }
            $page = $this->outbox->page($id, $afterId);
            if ($page['expired']) {
                return $this->json([
                    'error' => 'cursor_expired',
                    'rescan_required' => true,
                    'next_cursor' => $this->encodeCursor($id, $page['next_id']),
                ], 409);
            }
            return $this->json([
                'binding_id' => $id,
                'items' => $page['items'],
                'next_cursor' => $this->encodeCursor($id, $page['next_id']),
                'has_more' => $page['has_more'],
                'hint_only' => true,
                'rescan_required' => false,
            ]);
        } catch (\UnexpectedValueException $exception) {
            return $this->json(['error' => 'invalid_configuration'], 503);
        } catch (\Throwable $exception) {
            // Never return an empty successful page when the journal is down.
            return $this->json(['error' => 'changes_unavailable'], 503);
        }
    }

    private function encodeCursor(string $bindingId, int $id): string {
        $payload = '1:' . $bindingId . ':' . $id;
        $signature = hash_hmac('sha256', $payload, $this->config->getSystemValueString('secret'));
        $value = json_encode([1, $bindingId, (string)$id, $signature], JSON_THROW_ON_ERROR);
        return rtrim(strtr(base64_encode($value), '+/', '-_'), '=');
    }

    private function decodeCursor(string $bindingId, mixed $raw): ?int {
        if ($raw === '') {
            return 0;
        }
        if (!is_string($raw) || strlen($raw) > 512 || !preg_match('/\A[A-Za-z0-9_-]+\z/D', $raw)) {
            return null;
        }
        $decoded = base64_decode(strtr($raw, '-_', '+/'), true);
        if ($decoded === false) {
            return null;
        }
        try {
            $value = json_decode($decoded, true, 8, JSON_THROW_ON_ERROR);
        } catch (\JsonException $exception) {
            return null;
        }
        if (!is_array($value) || !array_is_list($value) || count($value) !== 4 ||
            $value[0] !== 1 || $value[1] !== $bindingId ||
            !is_string($value[2]) || !preg_match('/\A(0|[1-9][0-9]*)\z/D', $value[2]) ||
            !is_string($value[3]) || !preg_match('/\A[a-f0-9]{64}\z/D', $value[3])) {
            return null;
        }
        $id = filter_var($value[2], FILTER_VALIDATE_INT, ['options' => ['min_range' => 0]]);
        if ($id === false) {
            return null;
        }
        $expected = hash_hmac('sha256', '1:' . $bindingId . ':' . $value[2],
            $this->config->getSystemValueString('secret'));
        return hash_equals($expected, $value[3]) ? $id : null;
    }

    private function json(array $body, int $status = 200, array $headers = []): JSONResponse {
        return new JSONResponse($body, $status, ['Cache-Control' => 'no-store'] + $headers);
    }
}
