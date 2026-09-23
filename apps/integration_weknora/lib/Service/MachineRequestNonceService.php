<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IDBConnection;

/** A database unique key makes nonce consumption atomic across PHP workers. */
final class MachineRequestNonceService {
    public function __construct(private IDBConnection $db) {
    }

    public function consume(string $nonce, string $keyId, int $now): bool {
        // A replayed request has a timestamp within 300 seconds; retain the
        // nonce for 601 seconds so even clock-boundary requests cannot reuse it.
        $inserted = $this->db->insertIgnoreConflict('weknora_request_nonce', [
            'nonce' => $nonce,
            'key_id' => $keyId,
            'expires_at' => $now + 601,
        ]);
        if ($inserted !== 1) {
            return false;
        }

        // Indexed deletion bounds the table without an extra cron dependency.
        // Any database error fails the request closed at the caller.
        $query = $this->db->getQueryBuilder();
        $query->delete('weknora_request_nonce')
            ->where($query->expr()->lt('expires_at', $query->createNamedParameter($now)))
            ->executeStatement();
        return true;
    }
}
