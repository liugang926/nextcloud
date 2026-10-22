<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;

use OCP\Http\Client\IClientService;
use OCP\IConfig;

/** Scoped server credential; never sent to a browser or used to create Tenants. */
final class DirectoryOnboardingClient {
    public const PATH = '/api/v1/integrations/nextcloud/onboarding';
    public function __construct(private IClientService $clients, private EventReceiverPolicy $policy,
        private IConfig $config, private ControlRecoveryService $recovery) {}

    public function request(array $rule, string $method, string $suffix, array $body = []): array {
        $this->recovery->requireAutomationOpen($this->config);
        $approved = $this->policy->requireApproved($rule['weknora_origin'] . EventReceiverPolicy::PATH);
        $credentialId = $rule['credential_id'] ?? null;
        if ($credentialId !== null) {
            $credentials = $this->config->getSystemValue('weknora_onboarding_credentials', []);
            $credential = is_string($credentialId) && is_array($credentials) ? ($credentials[$credentialId] ?? null) : null;
            if (!is_array($credential) || ($credential['tenant_id'] ?? null) !== (string)$rule['tenant_id'] ||
                ($credential['weknora_origin'] ?? null) !== $rule['weknora_origin'] || !is_string($credential['token'] ?? null)) {
                throw new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::CREDENTIAL_UNAVAILABLE);
            }
            $token = $credential['token'];
        } else {
            $token = $this->config->getSystemValueString('weknora_onboarding_token', '');
        }
        if (strlen($token) < 32 || strlen($token) > 512 || preg_match('/[\x00-\x20\x7f]/', $token)) {
            throw new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::CREDENTIAL_UNAVAILABLE);
        }
        $suffix = self::operationGetSuffix($method, $suffix);
        $observeOperation = $method === 'GET' &&
            preg_match('~\A/operations/[a-f0-9-]{36}/observe/[a-f0-9]{24}\z~D', $suffix) === 1;
        $url = $approved['origin'] . self::PATH . $suffix;
        $options = ['headers' => [
            'Accept' => 'application/json',
            'Authorization' => 'Bearer ' . $token,
            'Cache-Control' => 'no-cache, no-store',
            'Pragma' => 'no-cache',
        ],
            'allow_redirects' => false, 'http_errors' => false, 'verify' => true, 'stream' => true,
            'timeout' => 15, 'connect_timeout' => 3, 'nextcloud' => ['allow_local_address' => $approved['allow_local']]];
        if ($method === 'GET') {
            $url = self::cacheBustedGetUrl($url, $body);
        } elseif ($method !== 'GET') {
            $options['headers']['Content-Type'] = 'application/json';
            $options['body'] = json_encode($body, JSON_THROW_ON_ERROR);
        }
        $client = $this->clients->newClient();
        try {
            $response = match ($method) {
                'GET' => $client->get($url, $options), 'POST' => $client->post($url, $options),
                'PUT' => $client->put($url, $options), default => throw new \InvalidArgumentException('Unsupported onboarding method'),
            };
            $raw = $response->getBody();
            if (is_resource($raw)) {
                $stream = $raw;
                try { $raw = stream_get_contents($stream, 65537); } finally { fclose($stream); }
            }
        } catch (DirectoryOnboardingRequestException $exception) {
            throw $exception;
        } catch (\Throwable $exception) {
            throw new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::TRANSPORT);
        }
        if (!is_string($raw) || strlen($raw) > 65536) {
            throw new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::INVALID_RESPONSE);
        }
        $status = $response->getStatusCode();
        if ($observeOperation && $status !== 200) {
            if (in_array($status, [400, 401, 403, 404, 409, 422], true)) {
                throw new DirectoryOnboardingRemoteRejectedException($status, $this->safeRemoteCode($raw));
            }
            $reason = $status === 429
                ? DirectoryOnboardingRequestException::RATE_LIMITED
                : ($status >= 500
                    ? DirectoryOnboardingRequestException::SERVER_ERROR
                    : DirectoryOnboardingRequestException::HTTP_ERROR);
            throw new DirectoryOnboardingRequestException($reason, $this->safeRemoteCode($raw), $status);
        }
        if ($status < 200 || $status >= 300) {
            // Never parse or forward remote errors: their body may contain
            // addresses, implementation details, or credentials.
            if (in_array($status, [400, 401, 403, 404, 409, 422], true)) {
                throw new DirectoryOnboardingRemoteRejectedException($status, $this->safeRemoteCode($raw));
            }
            $reason = $status === 429
                ? DirectoryOnboardingRequestException::RATE_LIMITED
                : ($status >= 500
                    ? DirectoryOnboardingRequestException::SERVER_ERROR
                    : DirectoryOnboardingRequestException::HTTP_ERROR);
            throw new DirectoryOnboardingRequestException($reason, $this->safeRemoteCode($raw), $status);
        }
        try {
            $data = json_decode($raw, true, 32, JSON_THROW_ON_ERROR);
        } catch (\Throwable $exception) {
            throw new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::INVALID_RESPONSE);
        }
        if (!is_array($data)) {
            throw new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::INVALID_RESPONSE);
        }
        return $data;
    }

    /**
     * Existing cached failures must not mask a newer bounded stage or a
     * recovered live target. Every scoped GET therefore receives a unique,
     * non-sensitive query value in addition to the request no-cache headers.
     */
    private static function cacheBustedGetUrl(string $url, array $body): string {
        $body['_nc_cache_bust'] = bin2hex(random_bytes(12));
        return $url . '?' . http_build_query($body, '', '&', PHP_QUERY_RFC3986);
    }

    /**
     * Use a dedicated path for live operation observations. A path-only cache
     * in front of WeKnora must not replay an earlier unavailable response.
     */
    private static function operationGetSuffix(string $method, string $suffix): string {
        if ($method === 'GET' && preg_match('~\A/operations/[a-f0-9-]{36}\z~D', $suffix) === 1) {
            return $suffix . '/observe/' . bin2hex(random_bytes(12));
        }
        return $suffix;
    }

    /**
     * Extract only a known machine code. Error text and every other response
     * field remain discarded because they are not trusted diagnostics.
     */
    private function safeRemoteCode(string $raw): ?string {
        try {
            $data = json_decode($raw, true, 4, JSON_THROW_ON_ERROR);
        } catch (\Throwable $exception) {
            return null;
        }
        $code = is_array($data) ? ($data['error'] ?? null) : null;
        if (!is_string($code) || !in_array($code, [
            'onboarding_unavailable',
            'nextcloud_onboarding_denied',
            'nextcloud_onboarding_not_found',
            'nextcloud_onboarding_conflict',
            'nextcloud_kb_blank_unverified',
            'nextcloud_kb_unavailable',
            'nextcloud_target_closed',
            'nextcloud_prepared_binding_unavailable',
            'source_pairing_not_found',
            'source_pairing_conflict',
            'source_pairing_unavailable',
            'event_connection_pending',
            'remote_outcome_uncertain',
        ], true)) {
            return null;
        }
        $stage = is_array($data) ? ($data['failure_stage'] ?? null) : null;
        if (is_string($stage) && in_array($stage, [
            'operation_read',
            'tenant_deletion',
            'target_observation',
            'operation_refresh',
        ], true)) {
            return $code . '_' . $stage;
        }
        return $code;
    }
}
