<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IDBConnection;
use OCP\IRequest;

/** Bearer authentication plus canonical HMAC and durable replay protection. */
final class PairedServiceToken {
    private const MAX_BODY_BYTES = 1048576;
    private const MAX_CLOCK_SKEW_SECONDS = 300;

    public function __construct(
        private IDBConnection $db,
        private MachineRequestNonceService $nonces,
    ) {
    }

    public function verify(IRequest $request): bool {
        try {
            $keys = $this->readCommittedKeys();
        } catch (\Throwable $exception) {
            return false;
        }
        $currentId = $keys['service_key_id'] ?? 'default';
        $currentHash = $keys['service_token_sha256'] ?? '';
        $previousId = $keys['service_previous_key_id'] ?? '';
        $previousHash = $keys['service_previous_token_sha256'] ?? '';
        if (!self::validKeyId($currentId) || !self::validHash($currentHash)) {
            return false;
        }
        // A rotation consists of two appconfig writes. Until both previous
        // fields are valid, only the current key is accepted; this avoids an
        // outage during setup and revokes the old key as soon as its ID is
        // removed. No partially configured previous credential is usable.
        $previousEnabled = self::validKeyId($previousId) && self::validHash($previousHash) &&
            $previousId !== $currentId;

        $keyId = $request->getHeader('X-WeKnora-Key-Id');
        if (!is_string($keyId) || !self::validKeyId($keyId)) {
            return false;
        }
        if ($keyId === $currentId) {
            $expectedHash = strtolower($currentHash);
        } elseif ($previousEnabled && $keyId === $previousId) {
            $expectedHash = strtolower($previousHash);
        } else {
            return false;
        }
        $authorization = $request->getHeader('Authorization');
        if (!is_string($authorization) ||
            !preg_match('/\ABearer[ \t]+([^\s]+)\z/iD', $authorization, $matches)) {
            return false;
        }
        if (!hash_equals($expectedHash, hash('sha256', $matches[1]))) {
            return false;
        }

        $timestamp = $request->getHeader('X-WeKnora-Timestamp');
        $nonce = $request->getHeader('X-WeKnora-Nonce');
        $signature = $request->getHeader('X-WeKnora-Signature');
        if (!is_string($timestamp) || !preg_match('/\A(?:0|[1-9][0-9]{0,11})\z/D', $timestamp) ||
            !is_string($nonce) || !preg_match('/\A[a-f0-9]{32}\z/D', $nonce) ||
            !is_string($signature) || !preg_match('/\A[a-f0-9]{64}\z/D', $signature)) {
            return false;
        }
        $now = time();
        if (abs($now - (int)$timestamp) > self::MAX_CLOCK_SKEW_SECONDS) {
            return false;
        }

        $method = strtoupper($request->getMethod());
        $uri = $request->getRequestUri();
        if (!preg_match('/\A[A-Z]+\z/D', $method) || !is_string($uri) ||
            strlen($uri) > 8192 || !str_starts_with($uri, '/') ||
            preg_match('/[\x00-\x20\x7f#]/', $uri)) {
            return false;
        }
        [$path, $rawQuery] = array_pad(explode('?', $uri, 2), 2, '');
        $query = self::canonicalQuery($rawQuery);
        if ($query === null) {
            return false;
        }
        $body = file_get_contents('php://input', false, null, 0, self::MAX_BODY_BYTES + 1);
        if ($body === false || strlen($body) > self::MAX_BODY_BYTES) {
            return false;
        }
        $canonical = implode("\n", [
            'weknora-hmac-sha256-v1',
            $method,
            $path,
            $query,
            hash('sha256', $body),
            $timestamp,
            $nonce,
            $keyId,
        ]);
        $key = hex2bin($expectedHash);
        if ($key === false || !hash_equals(hash_hmac('sha256', $canonical, $key), $signature)) {
            return false;
        }
        try {
            return $this->nonces->consume($nonce, $keyId, $now);
        } catch (\Throwable $exception) {
            // A missing migration or database outage cannot bypass replay checks.
            return false;
        }
    }

    private static function validKeyId(mixed $keyId): bool {
        return is_string($keyId) && (bool)preg_match('/\A[A-Za-z0-9._-]{1,64}\z/D', $keyId);
    }

    /**
     * Read committed appconfig rows directly. Nextcloud's IConfig cache is
     * process-local, so using it here could keep a revoked key alive until a
     * Web worker restart. One SQL statement sees a consistent configuration.
     *
     * @return array<string, string>
     */
    private function readCommittedKeys(): array {
        $query = $this->db->getQueryBuilder();
        $query->select('configkey', 'configvalue')->from('appconfig')
            ->where($query->expr()->eq('appid', $query->createNamedParameter('integration_weknora')));
        $result = $query->executeQuery();
        try {
            $keys = [];
            while (($row = $result->fetchAssociative()) !== false) {
                if (in_array($row['configkey'], [
                    'service_key_id', 'service_token_sha256',
                    'service_previous_key_id', 'service_previous_token_sha256',
                ], true)) {
                    $keys[$row['configkey']] = (string)$row['configvalue'];
                }
            }
            return $keys;
        } finally {
            $result->closeCursor();
        }
    }

    private static function validHash(mixed $hash): bool {
        return is_string($hash) && (bool)preg_match('/\A[a-fA-F0-9]{64}\z/D', $hash);
    }

    /** RFC 3986 encoded, sorted query pairs; repeated pairs are retained. */
    private static function canonicalQuery(string $rawQuery): ?string {
        if ($rawQuery === '') {
            return '';
        }
        if (str_contains($rawQuery, ';')) {
            return null;
        }
        $pairs = [];
        $seenKeys = [];
        foreach (explode('&', $rawQuery) as $part) {
            if ($part === '') {
                continue;
            }
            [$rawKey, $rawValue] = array_pad(explode('=', $part, 2), 2, '');
            if (preg_match('/%(?![a-fA-F0-9]{2})/', $rawKey) ||
                preg_match('/%(?![a-fA-F0-9]{2})/', $rawValue)) {
                return null;
            }
            $decodedKey = urldecode($rawKey);
            // PHP normalizes dots, spaces and bracket notation in parameter
            // names. Accept only keys with unambiguous parsing semantics.
            if (!preg_match('/\A[A-Za-z0-9_-]{1,64}\z/D', $decodedKey)) {
                return null;
            }
            $keyIdentity = bin2hex($decodedKey);
            // PHP request parameter parsing may use the last duplicate value.
            // Sorting duplicate pairs would make a reordered request share a
            // signature while changing that effective value, so reject it.
            if (isset($seenKeys[$keyIdentity])) {
                return null;
            }
            $seenKeys[$keyIdentity] = true;
            $pairs[] = [rawurlencode($decodedKey), rawurlencode(urldecode($rawValue))];
        }
        usort($pairs, static fn (array $left, array $right): int =>
            strcmp($left[0], $right[0]) ?: strcmp($left[1], $right[1]));
        return implode('&', array_map(static fn (array $pair): string => $pair[0] . '=' . $pair[1], $pairs));
    }
}
