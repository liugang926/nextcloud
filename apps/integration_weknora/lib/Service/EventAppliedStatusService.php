<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\Http\Client\IClientService;
use OCP\IConfig;
use OCP\IDBConnection;
use OCP\Security\ICrypto;

/** Verify WeKnora's applied watermark before allowing connected outbox pruning. */
final class EventAppliedStatusService {
    private const MAX_STATUS_BYTES = 4096;
    private const POLL_INTERVAL_SECONDS = 30;
    private const MAX_BINDINGS_PER_RUN = 10;

    public function __construct(
        private IDBConnection $db,
        private EventConnectionService $connections,
        private EventReceiverPolicy $policy,
        private BindingRegistryService $bindings,
        private ICrypto $crypto,
        private IClientService $clientService,
        private IConfig $config,
    ) {
    }

    /** @return int Number of connections whose signed status was verified. */
    public function pollDue(?int $now = null): int {
        $now ??= time();
        $query = $this->db->getQueryBuilder();
        $query->select('binding_id')->from('weknora_event_conn')
            ->where($query->expr()->eq('status', $query->createNamedParameter('active')))
            ->andWhere($query->expr()->lte('applied_checked_at',
                $query->createNamedParameter($now - self::POLL_INTERVAL_SECONDS)))
            ->orderBy('applied_checked_at', 'ASC')
            ->addOrderBy('binding_id', 'ASC')
            ->setMaxResults(self::MAX_BINDINGS_PER_RUN);
        $result = $query->executeQuery();
        try {
            $bindingIds = $result->fetchFirstColumn();
        } finally {
            $result->closeCursor();
        }

        $verified = 0;
        $deadline = time() + 45;
        foreach ($bindingIds as $bindingId) {
            if (time() >= $deadline) {
                break;
            }
            // poll() locks the same connection row as delivery and checks
            // the interval again, so parallel workers cannot trust stale
            // selections or a credential rotated after this query.
            if ($this->poll((string)$bindingId)) {
                $verified++;
            }
        }
        return $verified;
    }

    /** A failed or unsupported status query keeps the existing outbox intact. */
    public function poll(string $bindingId, ?int $now = null): bool {
        $now ??= time();
        $this->db->beginTransaction();
        try {
            // The same row lock serializes credential rotation, revocation,
            // delivery and watermark updates across concurrent cron workers.
            $row = $this->connections->row($bindingId, true);
            if ($row === null || $row['status'] !== 'active' ||
                (int)$row['applied_checked_at'] > $now - self::POLL_INTERVAL_SECONDS) {
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
                $this->recordResult($bindingId, $row, $now, null,
                    'status_configuration_unavailable');
                $this->db->commit();
                return false;
            }

            $connectionId = (string)$row['connection_id'];
            $keyId = (string)$row['key_id'];
            $query = 'connection_id=' . $connectionId;
            $timestamp = (string)$now;
            $nonce = bin2hex(random_bytes(16));
            $canonical = self::canonicalRequest($query, $timestamp, $nonce,
                $connectionId, $keyId);
            $headers = [
                'Accept' => 'application/json',
                'X-Nextcloud-Connection-Id' => $connectionId,
                'X-Nextcloud-Key-Id' => $keyId,
                'X-Nextcloud-Timestamp' => $timestamp,
                'X-Nextcloud-Nonce' => $nonce,
                'X-Nextcloud-Signature' => hash_hmac('sha256', $canonical, $secret),
            ];
            unset($secret);
            $url = $approved['origin'] . EventReceiverPolicy::STATUS_PATH . '?' . $query;
            try {
                $response = $this->clientService->newClient()->get($url, [
                    'headers' => $headers,
                    'allow_redirects' => false,
                    'http_errors' => false,
                    'verify' => true,
                    'stream' => true,
                    'timeout' => 8,
                    'connect_timeout' => 3,
                    'nextcloud' => ['allow_local_address' => $approved['allow_local']],
                ]);
            } catch (\Throwable $exception) {
                $this->recordResult($bindingId, $row, $now, null,
                    'status_transport_error');
                $this->db->commit();
                return false;
            }

            $status = $response->getStatusCode();
            $body = $response->getBody();
            if (is_resource($body)) {
                $raw = $status === 200
                    ? stream_get_contents($body, self::MAX_STATUS_BYTES + 1)
                    : null;
                fclose($body);
            } else {
                $raw = $status === 200 ? $body : null;
            }
            if ($status !== 200) {
                $code = in_array($status, [301, 302, 303, 307, 308], true)
                    ? 'status_redirect_rejected' : 'status_unavailable';
                $this->recordResult($bindingId, $row, $now, null, $code);
                $this->db->commit();
                return false;
            }
            $appliedId = $this->validatedAppliedId($raw, $row);
            if ($appliedId === null) {
                $this->recordResult($bindingId, $row, $now, null,
                    'status_invalid');
                $this->db->commit();
                return false;
            }
            $this->recordResult($bindingId, $row, $now, $appliedId, '');
            $this->db->commit();
            return true;
        } catch (\Throwable $exception) {
            $this->db->rollBack();
            // Delivery still runs. No unverified status may advance pruning.
            return false;
        }
    }

    public static function canonicalRequest(string $query, string $timestamp, string $nonce,
        string $connectionId, string $keyId): string {
        return implode("\n", [
            'nextcloud-event-status-hmac-sha256-v1', 'GET',
            EventReceiverPolicy::STATUS_PATH, $query, hash('sha256', ''),
            $timestamp, $nonce, $connectionId, $keyId,
        ]);
    }

    /** @param array<string, mixed> $row */
    private function validatedAppliedId(mixed $raw, array $row): ?int {
        if (!is_string($raw) || strlen($raw) > self::MAX_STATUS_BYTES) {
            return null;
        }
        try {
            $status = json_decode($raw, true, 16, JSON_THROW_ON_ERROR);
        } catch (\JsonException $exception) {
            return null;
        }
        if (!is_array($status) ||
            ($status['connection_id'] ?? null) !== (string)$row['connection_id'] ||
            ($status['binding_id'] ?? null) !== (string)$row['binding_id'] ||
            ($status['nextcloud_instance_id'] ?? null) !==
                $this->config->getSystemValueString('instanceid') ||
            ($status['status'] ?? null) !== 'active') {
            return null;
        }
        $received = self::parseEventId($status['received_through_event_id'] ?? null);
        $applied = self::parseEventId($status['applied_through_event_id'] ?? null);
        if ($received === null || $applied === null ||
            $received < (int)$row['received_id'] ||
            $applied < (int)$row['applied_id'] ||
            $applied > $received || $applied > (int)$row['received_id']) {
            return null;
        }
        return $applied;
    }

    private static function parseEventId(mixed $value): ?int {
        if (!is_string($value) ||
            !preg_match('/\A(?:0|[1-9][0-9]{0,18})\z/D', $value)) {
            return null;
        }
        $parsed = filter_var($value, FILTER_VALIDATE_INT,
            ['options' => ['min_range' => 0]]);
        return $parsed === false ? null : $parsed;
    }

    /** @param array<string, mixed> $row */
    private function recordResult(string $bindingId, array $row, int $now,
        ?int $appliedId, string $error): void {
        $update = $this->db->getQueryBuilder();
        $update->update('weknora_event_conn')
            ->set('applied_checked_at', $update->createNamedParameter($now))
            ->set('applied_error_code', $update->createNamedParameter($error))
            ->set('updated_at', $update->createNamedParameter(time()))
            ->where($update->expr()->eq('binding_id',
                $update->createNamedParameter($bindingId)))
            ->andWhere($update->expr()->eq('connection_id',
                $update->createNamedParameter((string)$row['connection_id'])))
            ->andWhere($update->expr()->eq('key_id',
                $update->createNamedParameter((string)$row['key_id'])));
        if ($appliedId !== null) {
            $update->set('applied_id', $update->createNamedParameter($appliedId));
        }
        if ($update->executeStatement() !== 1) {
            throw new \UnexpectedValueException('Event connection changed during status read');
        }
    }
}
