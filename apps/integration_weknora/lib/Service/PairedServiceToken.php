<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IRequest;

/** Bearer authentication plus canonical HMAC and durable replay protection. */
final class PairedServiceToken {
    private const MAX_BODY_BYTES = 1048576;
    private const MAX_CLOCK_SKEW_SECONDS = 300;

    public function __construct(
        private MachineKeyRegistryService $keys,
        private MachineRequestNonceService $nonces,
    ) {
    }

    public function verify(IRequest $request, ?string $bindingId = null): bool {
        return $this->authenticatedBinding($request, $bindingId) !== null;
    }

    /** Return the sole binding attached to a fully authenticated request. */
    public function authenticatedBinding(IRequest $request, ?string $requiredBindingId = null): ?string {
        return $this->authenticate($request, $requiredBindingId, null);
    }

    /** An expired pair key may authenticate only a repeat of its own abort. */
    public function authenticatedBindingForPairAbort(IRequest $request,
        string $bindingId, string $operationId): ?string {
        if (!SourcePairingRegistryService::validOperationId($operationId)) {
            return null;
        }
        return $this->authenticate($request, $bindingId, $operationId);
    }

    private function authenticate(IRequest $request, ?string $requiredBindingId,
        ?string $abortOperationId): ?string {
        $keyId = $request->getHeader('X-WeKnora-Key-Id');
        if (!is_string($keyId) || !MachineKeyRegistryService::validKeyId($keyId)) {
            return null;
        }
        try {
            // Read the committed key row for every request. Deleting that row
            // revokes the key without waiting for a PHP worker to restart.
            $key = $this->keys->find($keyId);
            if ($key === null && $abortOperationId !== null && $requiredBindingId !== null) {
                $key = $this->keys->findForPairAbort($keyId, $requiredBindingId,
                    $abortOperationId);
            }
            if ($key === null || ($requiredBindingId !== null &&
                !hash_equals($key['binding_id'], $requiredBindingId)) ||
                !$this->keys->matchesCurrentBinding($key)) {
                return null;
            }
        } catch (\Throwable $exception) {
            return null;
        }
        $expectedHash = $key['token_sha256'];
        $authorization = $request->getHeader('Authorization');
        if (!is_string($authorization) ||
            !preg_match('/\ABearer[ \t]+([^\s]+)\z/iD', $authorization, $matches)) {
            return null;
        }
        if (!hash_equals($expectedHash, hash('sha256', $matches[1]))) {
            return null;
        }

        $timestamp = $request->getHeader('X-WeKnora-Timestamp');
        $nonce = $request->getHeader('X-WeKnora-Nonce');
        $signature = $request->getHeader('X-WeKnora-Signature');
        if (!is_string($timestamp) || !preg_match('/\A(?:0|[1-9][0-9]{0,11})\z/D', $timestamp) ||
            !is_string($nonce) || !preg_match('/\A[a-f0-9]{32}\z/D', $nonce) ||
            !is_string($signature) || !preg_match('/\A[a-f0-9]{64}\z/D', $signature)) {
            return null;
        }
        $now = time();
        if (abs($now - (int)$timestamp) > self::MAX_CLOCK_SKEW_SECONDS) {
            return null;
        }

        $method = strtoupper($request->getMethod());
        $uri = $request->getRequestUri();
        if (!preg_match('/\A[A-Z]+\z/D', $method) || !is_string($uri) ||
            strlen($uri) > 8192 || !str_starts_with($uri, '/') ||
            preg_match('/[\x00-\x20\x7f#]/', $uri)) {
            return null;
        }
        [$path, $rawQuery] = array_pad(explode('?', $uri, 2), 2, '');
        $query = self::canonicalQuery($rawQuery);
        if ($query === null) {
            return null;
        }
        $body = file_get_contents('php://input', false, null, 0, self::MAX_BODY_BYTES + 1);
        if ($body === false || strlen($body) > self::MAX_BODY_BYTES) {
            return null;
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
        $hmacKey = hex2bin($expectedHash);
        if ($hmacKey === false || !hash_equals(hash_hmac('sha256', $canonical, $hmacKey), $signature)) {
            return null;
        }
        try {
            if (!$this->nonces->consume($nonce, $keyId, $now)) {
                return null;
            }
            // Re-read after nonce consumption so a key deleted while the
            // signature was checked cannot use a stale credential snapshot.
            $stillActive = $this->keys->find($keyId);
            if ($stillActive === null && $abortOperationId !== null && $requiredBindingId !== null) {
                $stillActive = $this->keys->findForPairAbort($keyId,
                    $requiredBindingId, $abortOperationId);
            }
            return $stillActive !== null && $stillActive === $key &&
                $this->keys->matchesCurrentBinding($stillActive) ? $key['binding_id'] : null;
        } catch (\Throwable $exception) {
            // A missing migration or database outage cannot bypass replay checks.
            return null;
        }
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
