<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\Http\Client\IClientService;
use OCP\IConfig;
use OCP\Security\ICrypto;

/** A short, signed machine read of WeKnora's currently published file version. */
final class RemoteFileStatusService {
    private const PATH = '/api/v1/integrations/nextcloud/files/status';
    private const MAX_BODY_BYTES = 4096;

    public function __construct(
        private EventConnectionService $connections,
        private SourcePairingRegistryService $pairings,
        private EventReceiverPolicy $policy,
        private ICrypto $crypto,
        private IClientService $clientService,
        private IConfig $config,
    ) {
    }

    /** @return array{knowledge_state: string, knowledge_ready_at: ?int, published_source_etag: ?string, qa_available: false}|null */
    public function status(string $bindingId, int $fileId, string $sourceEtag): ?array {
        if ($fileId < 1 || !preg_match('/\A[A-Za-z0-9._:-]{1,256}\z/D', $sourceEtag)) {
            return null;
        }
        try {
            $pair = $this->pairings->status($bindingId);
            $row = $this->connections->row($bindingId);
            $instanceId = $this->config->getSystemValueString('instanceid');
            if ($pair === null || $pair['state'] !== 'active' ||
                $pair['binding_id'] !== $bindingId || $pair['instance_id'] !== $instanceId ||
                $pair['data_source_id'] === null ||
                $row === null || $row['status'] !== 'active' ||
                $row['binding_id'] !== $bindingId ||
                !preg_match('/\A[A-Za-z0-9_-]{16,128}\z/D', (string)$row['connection_id']) ||
                !preg_match('/\A[A-Za-z0-9._-]{1,64}\z/D', (string)$row['key_id'])) {
                return null;
            }
            $approved = $this->policy->requireApproved((string)$row['receiver_url']);
            $secret = $this->crypto->decrypt((string)$row['secret_ciphertext']);
            if (strlen($secret) < 32 || strlen($secret) > 128) {
                return null;
            }
            $connectionId = (string)$row['connection_id'];
            $keyId = (string)$row['key_id'];
            $query = 'connection_id=' . $connectionId . '&file_id=' . $fileId .
                '&source_etag=' . $sourceEtag;
            $timestamp = (string)time();
            $nonce = bin2hex(random_bytes(16));
            $canonical = self::canonicalRequest($query, $timestamp, $nonce, $connectionId, $keyId);
            $requestSignature = hash_hmac('sha256', $canonical, $secret);
            $response = $this->clientService->newClient()->get(
                $approved['origin'] . self::PATH . '?' . $query,
                [
                    'headers' => [
                        'Accept' => 'application/json',
                        'X-Nextcloud-Connection-Id' => $connectionId,
                        'X-Nextcloud-Key-Id' => $keyId,
                        'X-Nextcloud-Timestamp' => $timestamp,
                        'X-Nextcloud-Nonce' => $nonce,
                        'X-Nextcloud-Signature' => $requestSignature,
                    ],
                    'allow_redirects' => false,
                    'http_errors' => false,
                    'verify' => true,
                    'stream' => true,
                    'timeout' => 5,
                    'connect_timeout' => 2,
                    'nextcloud' => ['allow_local_address' => $approved['allow_local']],
                ],
            );
            $responseBody = $response->getBody();
            if (is_resource($responseBody)) {
                $raw = $response->getStatusCode() === 200
                    ? stream_get_contents($responseBody, self::MAX_BODY_BYTES + 1) : null;
                fclose($responseBody);
            } else {
                $raw = $response->getStatusCode() === 200 ? $responseBody : null;
            }
            if (!is_string($raw) || strlen($raw) > self::MAX_BODY_BYTES) {
                return null;
            }
            $verified = self::validatedResponse($raw,
                $response->getHeader('X-WeKnora-Status-Signature'), $requestSignature,
                $secret, $connectionId, $keyId, $nonce, $instanceId,
                $bindingId, $pair, $fileId, $sourceEtag);
            if ($verified === null) {
                return null;
            }
            $pairNow = $this->pairings->status($bindingId);
            $rowNow = $this->connections->row($bindingId);
            if ($pairNow === null || $pairNow['state'] !== 'active' ||
                $pairNow['operation_id'] !== $pair['operation_id'] ||
                $pairNow['data_source_id'] !== $pair['data_source_id'] ||
                $rowNow === null || $rowNow['status'] !== 'active' ||
                $rowNow['connection_id'] !== $connectionId || $rowNow['key_id'] !== $keyId) {
                return null;
            }
            return $verified;
        } catch (\Throwable $exception) {
            // Any transport, parser, rotation or source-pairing fault stays
            // unverified. No remote error text is exposed to the employee.
            return null;
        }
    }

    public static function canonicalRequest(string $query, string $timestamp, string $nonce,
        string $connectionId, string $keyId): string {
        return implode("\n", [
            'nextcloud-file-status-hmac-sha256-v1', 'GET', self::PATH, $query,
            hash('sha256', ''), $timestamp, $nonce, $connectionId, $keyId,
        ]);
    }

    public static function canonicalResponse(string $requestSignature, string $body,
        string $connectionId, string $keyId, string $nonce): string {
        return implode("\n", [
            'weknora-file-status-hmac-sha256-v1', $requestSignature, hash('sha256', $body),
            $connectionId, $keyId, $nonce,
        ]);
    }

    /** @param array<string, mixed> $pair
     *  @return array{knowledge_state: string, knowledge_ready_at: ?int, published_source_etag: ?string, qa_available: false}|null
     */
    public static function validatedResponse(string $raw, string $signature,
        string $requestSignature, string $secret, string $connectionId, string $keyId,
        string $nonce, string $instanceId, string $bindingId, array $pair,
        int $fileId, string $sourceEtag): ?array {
        if (strlen($raw) > self::MAX_BODY_BYTES ||
            !preg_match('/\A[a-f0-9]{64}\z/D', $signature) ||
            !hash_equals(hash_hmac('sha256', self::canonicalResponse($requestSignature,
                $raw, $connectionId, $keyId, $nonce), $secret), $signature)) {
            return null;
        }
        try {
            $parsed = json_decode($raw, true, 16, JSON_THROW_ON_ERROR);
        } catch (\JsonException $exception) {
            return null;
        }
        if (!is_array($parsed) ||
            ($parsed['connection_id'] ?? null) !== $connectionId ||
            ($parsed['nextcloud_instance_id'] ?? null) !== $instanceId ||
            ($parsed['binding_id'] ?? null) !== $bindingId ||
            ($parsed['tenant_id'] ?? null) !== ($pair['tenant_id'] ?? null) ||
            ($parsed['knowledge_base_id'] ?? null) !== ($pair['knowledge_base_id'] ?? null) ||
            ($parsed['data_source_id'] ?? null) !== ($pair['data_source_id'] ?? null) ||
            ($parsed['file_id'] ?? null) !== (string)$fileId ||
            ($parsed['source_etag'] ?? null) !== $sourceEtag ||
            ($parsed['qa_available'] ?? null) !== false) {
            return null;
        }
        $state = $parsed['knowledge_state'] ?? null;
        $published = $parsed['published_source_etag'] ?? null;
        $readyAt = $parsed['knowledge_ready_at'] ?? null;
        if (!in_array($state, ['unverified', 'updating', 'failed', 'ready'], true) ||
            ($published !== null && (!is_string($published) ||
                !preg_match('/\A[A-Za-z0-9._:-]{1,256}\z/D', $published))) ||
            ($readyAt !== null && (!is_int($readyAt) || $readyAt < 1))) {
            return null;
        }
        if ($state === 'ready') {
            if ($published !== $sourceEtag || $readyAt === null) {
                return null;
            }
        } elseif ($readyAt !== null) {
            return null;
        }
        return [
            'knowledge_state' => $state,
            'knowledge_ready_at' => $readyAt,
            'published_source_etag' => $published,
            // A machine status never grants this user's retrieval access.
            'qa_available' => false,
        ];
    }
}
