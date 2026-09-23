<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\Http\Client\IClientService;
use OCP\IConfig;
use OCP\IDBConnection;
use OCP\Security\ICrypto;

/** Sends immutable outbox hints; the checkpoint records durable receipt only. */
final class EventDeliveryService {
    private const MAX_BODY_BYTES = 1048576;
    private const MAX_RECEIPT_BYTES = 4096;
    private const MAX_BINDINGS_PER_RUN = 10;
    private const MAX_BATCHES_PER_BINDING = 5;

    public function __construct(
        private IDBConnection $db,
        private ChangeOutboxService $outbox,
        private EventConnectionService $connections,
        private EventReceiverPolicy $policy,
        private BindingRegistryService $bindings,
        private ICrypto $crypto,
        private IClientService $clientService,
        private IConfig $config,
    ) {
    }

    /** @return int Number of batches with a verified durable receipt. */
    public function deliverDue(?int $now = null): int {
        $now ??= time();
        $query = $this->db->getQueryBuilder();
        $query->select('binding_id')->from('weknora_event_conn')
            ->where($query->expr()->eq('status', $query->createNamedParameter('active')))
            ->andWhere($query->expr()->lte('next_attempt_at', $query->createNamedParameter($now)))
            ->orderBy('next_attempt_at', 'ASC')
            ->addOrderBy('binding_id', 'ASC')
            ->setMaxResults(self::MAX_BINDINGS_PER_RUN);
        $result = $query->executeQuery();
        try {
            $bindingIds = $result->fetchFirstColumn();
        } finally {
            $result->closeCursor();
        }

        $sent = 0;
        $deadline = time() + 45;
        foreach ($bindingIds as $bindingId) {
            $completed = 0;
            for ($batch = 0; $batch < self::MAX_BATCHES_PER_BINDING && time() < $deadline; $batch++) {
                if (!$this->deliverOne((string)$bindingId)) {
                    break;
                }
                $sent++;
                $completed++;
            }
            if ($completed > 0) {
                // A busy binding may have more than five pages. Yield its
                // position so a later binding is not starved on every run.
                // An empty page or error already installed its own schedule.
                $this->deferPoll((string)$bindingId, 1);
            }
        }
        return $sent;
    }

    /** True only after a verified 202 receipt was committed locally. */
    private function deliverOne(string $bindingId): bool {
        $snapshot = $this->connections->row($bindingId);
        if ($snapshot === null || $snapshot['status'] !== 'active' ||
            (int)$snapshot['next_attempt_at'] > time()) {
            return false;
        }
        $afterId = (int)$snapshot['received_id'];
        try {
            $page = $this->outbox->page($bindingId, $afterId);
        } catch (\Throwable $exception) {
            $this->recordFailure($bindingId, $snapshot, 'outbox_unavailable', false);
            return false;
        }
        if ($page['expired']) {
            $this->recordFailure($bindingId, $snapshot, 'outbox_gap', true);
            return false;
        }
        if ($page['items'] === []) {
            // An empty sender must yield its position in the due queue.
            $this->deferPoll($bindingId, 30, $snapshot);
            return false;
        }

        $events = $page['items'];
        $body = '';
        try {
            while ($events !== []) {
                $body = json_encode([
                    'connection_id' => $snapshot['connection_id'],
                    'nextcloud_instance_id' => $this->localInstanceId(),
                    'binding_id' => $bindingId,
                    'after_event_id' => (string)$afterId,
                    'events' => $events,
                ], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
                if (strlen($body) <= self::MAX_BODY_BYTES) {
                    break;
                }
                array_pop($events);
            }
        } catch (\JsonException $exception) {
            $this->recordFailure($bindingId, $snapshot, 'event_encoding', true);
            return false;
        }
        if ($events === []) {
            $this->recordFailure($bindingId, $snapshot, 'event_too_large', true);
            return false;
        }
        $lastId = (string)$events[count($events) - 1]['event_id'];

        // A row lock spans the bounded HTTP request. Revocation/rotation and
        // binding deletion wait for this transaction, so no old credential is
        // used after their commit. It also serializes independent workers.
        $this->db->beginTransaction();
        try {
            $row = $this->connections->row($bindingId, true);
            if ($row === null || $row['status'] !== 'active' ||
                (int)$row['next_attempt_at'] > time() ||
                (int)$row['received_id'] !== $afterId ||
                $row['connection_id'] !== $snapshot['connection_id'] ||
                $row['key_id'] !== $snapshot['key_id']) {
                $this->db->commit();
                return false;
            }
            try {
                $this->bindings->requireActiveRoot($bindingId);
                $approved = $this->policy->requireApproved((string)$row['receiver_url']);
                $secret = $this->crypto->decrypt((string)$row['secret_ciphertext']);
                if ($secret === '') {
                    throw new \UnexpectedValueException('Empty event secret');
                }
            } catch (\Throwable $exception) {
                $this->updateFailure($bindingId, $row, 'configuration_unavailable', true);
                $this->db->commit();
                return false;
            }

            $timestamp = (string)time();
            $nonce = bin2hex(random_bytes(16));
            $canonical = implode("\n", [
                'nextcloud-event-hmac-sha256-v1', 'POST', EventReceiverPolicy::PATH, '',
                hash('sha256', $body), $timestamp, $nonce,
                (string)$row['connection_id'], (string)$row['key_id'],
            ]);
            $headers = [
                'Content-Type' => 'application/json',
                'Accept' => 'application/json',
                'X-Nextcloud-Connection-Id' => (string)$row['connection_id'],
                'X-Nextcloud-Key-Id' => (string)$row['key_id'],
                'X-Nextcloud-Timestamp' => $timestamp,
                'X-Nextcloud-Nonce' => $nonce,
                'X-Nextcloud-Signature' => hash_hmac('sha256', $canonical, $secret),
            ];
            unset($secret);
            try {
                $response = $this->clientService->newClient()->post((string)$row['receiver_url'], [
                    'headers' => $headers,
                    'body' => $body,
                    'allow_redirects' => false,
                    'http_errors' => false,
                    'verify' => true,
                    'stream' => true,
                    'timeout' => 8,
                    'connect_timeout' => 3,
                    'nextcloud' => ['allow_local_address' => $approved['allow_local']],
                ]);
            } catch (\Throwable $exception) {
                $this->updateFailure($bindingId, $row, 'transport_error', false);
                $this->db->commit();
                return false;
            }
            $status = $response->getStatusCode();
            $responseBody = $response->getBody();
            if (is_resource($responseBody)) {
                $receiptRaw = $status === 202
                    ? stream_get_contents($responseBody, self::MAX_RECEIPT_BYTES + 1)
                    : null;
                fclose($responseBody);
            } else {
                $receiptRaw = $status === 202 ? $responseBody : null;
            }
            $receipt = $status === 202
                ? $this->receiptState($receiptRaw, (string)$row['connection_id'], $lastId)
                : 'invalid';
            if ($status === 202 && $receipt === 'exact') {
                $update = $this->db->getQueryBuilder();
                $update->update('weknora_event_conn')
                    ->set('received_id', $update->createNamedParameter($lastId))
                    ->set('attempt_count', $update->createNamedParameter(0))
                    ->set('next_attempt_at', $update->createNamedParameter(0))
                    ->set('last_error_code', $update->createNamedParameter(''))
                    ->set('updated_at', $update->createNamedParameter(time()))
                    ->where($update->expr()->eq('binding_id', $update->createNamedParameter($bindingId)))
                    ->executeStatement();
                $this->db->commit();
                return true;
            }
            if ($status === 202 && $receipt === 'diverged') {
                $this->updateFailure($bindingId, $row, 'checkpoint_divergence', true);
            } elseif ($status === 301 || $status === 302 || $status === 303 ||
                $status === 307 || $status === 308) {
                $this->updateFailure($bindingId, $row, 'redirect_rejected', true);
            } elseif ($status === 401 || $status === 403) {
                $this->updateFailure($bindingId, $row, 'receiver_unauthorized', true);
            } elseif ($status === 409) {
                $this->updateFailure($bindingId, $row, 'receiver_checkpoint_conflict', true);
            } elseif ($status === 429 || $status >= 500 || $status === 202) {
                $this->updateFailure($bindingId, $row,
                    $status === 202 ? 'invalid_receipt' : 'receiver_retryable', false);
            } else {
                $this->updateFailure($bindingId, $row, 'receiver_rejected', true);
            }
            $this->db->commit();
            return false;
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /** @param array<string, mixed> $snapshot */
    private function recordFailure(string $bindingId, array $snapshot, string $code, bool $pause): void {
        $this->db->beginTransaction();
        try {
            $row = $this->connections->row($bindingId, true);
            if ($row !== null && $row['status'] === 'active' &&
                $row['connection_id'] === $snapshot['connection_id'] &&
                $row['key_id'] === $snapshot['key_id'] &&
                (int)$row['received_id'] === (int)$snapshot['received_id']) {
                $this->updateFailure($bindingId, $row, $code, $pause);
            }
            $this->db->commit();
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    /** @param array<string, mixed> $row */
    private function updateFailure(string $bindingId, array $row, string $code, bool $pause): void {
        $attempts = min(20, (int)$row['attempt_count'] + 1);
        $delay = min(3600, 5 * (2 ** min(10, $attempts - 1))) + random_int(0, 5);
        $update = $this->db->getQueryBuilder();
        $update->update('weknora_event_conn')
            ->set('status', $update->createNamedParameter($pause ? 'paused' : 'active'))
            ->set('attempt_count', $update->createNamedParameter($attempts))
            ->set('next_attempt_at', $update->createNamedParameter($pause ? 0 : time() + $delay))
            ->set('last_error_code', $update->createNamedParameter($code))
            ->set('updated_at', $update->createNamedParameter(time()))
            ->where($update->expr()->eq('binding_id', $update->createNamedParameter($bindingId)))
            ->executeStatement();
    }

    /** @return 'exact'|'diverged'|'invalid' */
    private function receiptState(mixed $raw, string $connectionId, string $lastId): string {
        if (!is_string($raw) || strlen($raw) > self::MAX_RECEIPT_BYTES) {
            return 'invalid';
        }
        try {
            $receipt = json_decode($raw, true, 16, JSON_THROW_ON_ERROR);
        } catch (\JsonException $exception) {
            return 'invalid';
        }
        if (!is_array($receipt) ||
            ($receipt['connection_id'] ?? null) !== $connectionId ||
            ($receipt['durable_receipt_only'] ?? null) !== true) {
            return 'invalid';
        }
        $received = $receipt['received_through_event_id'] ?? null;
        if (!is_string($received) ||
            !preg_match('/\A(?:0|[1-9][0-9]{0,18})\z/D', $received)) {
            return 'invalid';
        }
        return $received === $lastId ? 'exact' : 'diverged';
    }

    /** @param array<string, mixed>|null $snapshot */
    private function deferPoll(string $bindingId, int $seconds, ?array $snapshot = null): void {
        $this->db->beginTransaction();
        try {
            $row = $this->connections->row($bindingId, true);
            if ($row !== null && $row['status'] === 'active' &&
                (int)$row['next_attempt_at'] <= time() &&
                (string)$row['last_error_code'] === '' &&
                ($snapshot === null ||
                    ($row['connection_id'] === $snapshot['connection_id'] &&
                    $row['key_id'] === $snapshot['key_id'] &&
                    (int)$row['received_id'] === (int)$snapshot['received_id']))) {
                $update = $this->db->getQueryBuilder();
                $update->update('weknora_event_conn')
                    ->set('next_attempt_at', $update->createNamedParameter(time() + $seconds))
                    ->set('updated_at', $update->createNamedParameter(time()))
                    ->where($update->expr()->eq('binding_id', $update->createNamedParameter($bindingId)))
                    ->executeStatement();
            }
            $this->db->commit();
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            throw $exception;
        }
    }

    private function localInstanceId(): string {
        // The configured connection was checked against this instance at
        // provisioning. Read the current value each time to fail receiver
        // scope checks after an instance restore or identity change.
        return $this->config->getSystemValueString('instanceid');
    }
}
