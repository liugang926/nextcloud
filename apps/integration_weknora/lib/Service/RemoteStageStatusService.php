<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\Http\Client\IClientService;
use OCP\Security\ICrypto;

/** Signed current-head compensation, independent of a browser being open. */
final class RemoteStageStatusService {
    public function __construct(private EventConnectionService $connections,
        private EventReceiverPolicy $policy, private ICrypto $crypto,
        private IClientService $clients, private StatusProjectionService $projection, private StatusPollQueue $queue) {
    }

    public function refresh(string $bindingId, int $fileId, string $sourceEtag): bool {
        try {
            $row = $this->connections->row($bindingId);
            if ($row === null || $row['status'] !== 'active' || $fileId < 1 ||
                !preg_match('/\A[A-Za-z0-9._:-]{1,256}\z/D', $sourceEtag)) {
                return false;
            }
            $approved = $this->policy->requireApproved((string)$row['receiver_url']);
            $secret = $this->crypto->decrypt((string)$row['secret_ciphertext']);
            if (strlen($secret) < 32 || strlen($secret) > 128) { return false; }
            $key = StatusProtocol::key($secret);
            $connectionId = (string)$row['connection_id'];
            $keyId = (string)$row['key_id'];
            $query = 'connection_id=' . $connectionId . '&file_id=' . $fileId . '&source_etag=' . $sourceEtag;
            $started = time();
            $timestamp = (string)$started;
            $nonce = bin2hex(random_bytes(16));
            $signature = hash_hmac('sha256', StatusProtocol::request($query, $timestamp,
                $nonce, $connectionId, $keyId), $key);
            $response = $this->clients->newClient()->get($approved['origin'] . StatusProtocol::PATH . '?' . $query,
                ['headers' => ['Accept' => 'application/json', 'X-Nextcloud-Connection-Id' => $connectionId,
                    'X-Nextcloud-Key-Id' => $keyId, 'X-Nextcloud-Timestamp' => $timestamp,
                    'X-Nextcloud-Nonce' => $nonce, 'X-Nextcloud-Signature' => $signature],
                    'allow_redirects' => false, 'http_errors' => false, 'verify' => true, 'stream' => true,
                    'timeout' => 5, 'connect_timeout' => 2,
                    'nextcloud' => ['allow_local_address' => $approved['allow_local']]]);
            $body = $response->getBody();
            if (is_resource($body)) {
                $raw = $response->getStatusCode() === 200 ? stream_get_contents($body, StatusProtocol::MAX_BYTES + 1) : null;
                fclose($body);
            } else { $raw = $response->getStatusCode() === 200 ? $body : null; }
            if (!is_string($raw) || strlen($raw) > StatusProtocol::MAX_BYTES || time() - $started > 6) { return false; }
            $responseSignature = $response->getHeader('X-WeKnora-Status-Signature');
            if (!preg_match('/\A[a-f0-9]{64}\z/D', $responseSignature) ||
                !hash_equals(hash_hmac('sha256', StatusProtocol::response($signature, $raw,
                    $connectionId, $keyId, $nonce), $key), $responseSignature)) { return false; }
            $s = StatusProtocol::decode($raw, time());
            if ($s['binding_id'] !== $bindingId || $s['connection_id'] !== $connectionId ||
                $s['file_id'] !== (string)$fileId || $s['source_etag'] !== $sourceEtag ||
                $s['last_verified_at'] === null || $s['last_verified_at'] < $started - 1 ||
                $s['last_verified_at'] > time() + 5) { return false; }
            $current = $this->connections->row($bindingId);
            if ($current === null || $current['status'] !== 'active' ||
                $current['connection_id'] !== $connectionId || $current['key_id'] !== $keyId ||
                $current['secret_ciphertext'] !== $row['secret_ciphertext']) { return false; }
            // Use the request start as the conservative authenticated time
            // boundary; response receipt and old outbox dates are not freshness proof.
            $this->projection->accept($raw, $started, $row);
            return true;
        } catch (\Throwable $e) { return false; }
    }

    public function pollDue(int $now): void {
        $deadline = microtime(true) + 8;
        $this->queue->discover($now);
        for ($attempt = 0; $attempt < 200 && microtime(true) < $deadline; $attempt++) {
            $claim = $this->queue->claim(time());
            if ($claim === null) { break; }
            $etag = $this->projection->currentEtag($claim['binding_id'], (int)$claim['file_id']);
            if ($etag !== null && $this->refresh($claim['binding_id'], (int)$claim['file_id'], $etag)) { $this->queue->complete($claim); }
        }
        foreach ($this->projection->due($now) as $row) {
            if (microtime(true) >= $deadline) { break; }
            $this->projection->markPolled($row['binding_id'], (int)$row['file_id'], $now);
            $etag = $this->projection->currentEtag($row['binding_id'], (int)$row['file_id']);
            if ($etag !== null) { $this->refresh($row['binding_id'], (int)$row['file_id'], $etag); }
        }
    }
}
