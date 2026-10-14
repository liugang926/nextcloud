<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCA\IntegrationWeknora\Service\EventConnectionService;
use OCA\IntegrationWeknora\Service\MachineRequestNonceService;
use OCA\IntegrationWeknora\Service\StatusProjectionService;
use OCA\IntegrationWeknora\Service\StatusProtocol;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoCSRFRequired;
use OCP\AppFramework\Http\Attribute\PublicPage;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IRequest;
use OCP\Security\ICrypto;

final class StatusInboxController extends Controller {
    public function __construct(IRequest $request, private EventConnectionService $connections,
        private MachineRequestNonceService $nonces, private ICrypto $crypto,
        private StatusProjectionService $projection) { parent::__construct('integration_weknora', $request); }

    #[PublicPage]
    #[NoCSRFRequired]
    public function receive(): JSONResponse {
        try {
            $stream = fopen('php://input', 'rb');
            if ($stream === false) { return $this->json(['error' => 'invalid_request'], 400); }
            $raw = stream_get_contents($stream, StatusProtocol::MAX_BYTES + 1);
            fclose($stream);
            if (!is_string($raw) || strlen($raw) > StatusProtocol::MAX_BYTES) {
                return $this->json(['error' => 'invalid_request'], 400);
            }
            $s = StatusProtocol::decode($raw, time());
            $connection = $this->request->getHeader('X-WeKnora-Connection-Id');
            $keyId = $this->request->getHeader('X-WeKnora-Key-Id');
            $timestamp = $this->request->getHeader('X-WeKnora-Timestamp');
            $nonce = $this->request->getHeader('X-WeKnora-Nonce');
            $signature = $this->request->getHeader('X-WeKnora-Signature');
            $uri = $this->request->getRequestUri();
            $path = parse_url($uri, PHP_URL_PATH);
            // No alternate query spelling, redirects, or arbitrary signed destination.
            if ($this->request->getMethod() !== 'POST' || str_contains($uri, '?') ||
                !is_string($path) || !preg_match('#\A(?:/[A-Za-z0-9._~-]+)*/(?:index\.php/)?apps/integration_weknora/api/v1/status\z#D', $path) ||
                $connection !== $s['connection_id'] || !preg_match('/\A[A-Za-z0-9._-]{1,64}\z/D', $keyId) ||
                !StatusProtocol::decimal($timestamp) || abs(time() - (int)$timestamp) > 300 ||
                !preg_match('/\A[a-f0-9]{32}\z/D', $nonce) || !preg_match('/\A[a-f0-9]{64}\z/D', $signature)) {
                return $this->json(['error' => 'unauthorized'], 401);
            }
            $row = $this->connections->row($s['binding_id']);
            if ($row === null || $row['status'] !== 'active' || $row['connection_id'] !== $connection ||
                $row['key_id'] !== $keyId) { return $this->json(['error' => 'unauthorized'], 401); }
            $secret = $this->crypto->decrypt($row['secret_ciphertext']);
            if (strlen($secret) < 32 || strlen($secret) > 128 ||
                !hash_equals(hash_hmac('sha256', StatusProtocol::push($path, $raw, $timestamp,
                    $nonce, $connection, $keyId), StatusProtocol::key($secret)), $signature) ||
                !$this->nonces->consume($nonce, hash('sha256', 'state-v2:' . $connection . ':' . $keyId), time())) {
                return $this->json(['error' => 'unauthorized'], 401);
            }
            return $this->json($this->projection->accept($raw, null, $row));
        } catch (\InvalidArgumentException|\JsonException $e) {
            return $this->json(['error' => 'invalid_request'], 400);
        } catch (\DomainException $e) {
            return $this->json(['error' => 'status_conflict'], 409);
        } catch (\Throwable $e) {
            return $this->json(['error' => 'status_unavailable'], 503);
        }
    }

    private function json(array $body, int $code = 200): JSONResponse {
        return new JSONResponse($body, $code, ['Cache-Control' => 'no-store']);
    }
}
