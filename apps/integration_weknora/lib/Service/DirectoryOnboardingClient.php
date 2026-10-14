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
                throw new \RuntimeException('Scoped onboarding credential is unavailable');
            }
            $token = $credential['token'];
        } else {
            $token = $this->config->getSystemValueString('weknora_onboarding_token', '');
        }
        if (strlen($token) < 32 || strlen($token) > 512 || preg_match('/[\x00-\x20\x7f]/', $token)) {
            throw new \RuntimeException('Onboarding server credential is unavailable');
        }
        $url = $approved['origin'] . self::PATH . $suffix;
        $options = ['headers' => ['Accept' => 'application/json', 'Authorization' => 'Bearer ' . $token],
            'allow_redirects' => false, 'http_errors' => false, 'verify' => true, 'stream' => true,
            'timeout' => 15, 'connect_timeout' => 3, 'nextcloud' => ['allow_local_address' => $approved['allow_local']]];
        if ($method === 'GET' && $body !== []) {
            $url .= '?' . http_build_query($body, '', '&', PHP_QUERY_RFC3986);
        } elseif ($method !== 'GET') {
            $options['headers']['Content-Type'] = 'application/json';
            $options['body'] = json_encode($body, JSON_THROW_ON_ERROR);
        }
        $client = $this->clients->newClient();
        $response = match ($method) {
            'GET' => $client->get($url, $options), 'POST' => $client->post($url, $options),
            'PUT' => $client->put($url, $options), default => throw new \InvalidArgumentException('Unsupported onboarding method'),
        };
        $raw = $response->getBody();
        if (is_resource($raw)) {
            $stream = $raw;
            try { $raw = stream_get_contents($stream, 65537); } finally { fclose($stream); }
        }
        if (!is_string($raw) || strlen($raw) > 65536) {
            throw new \RuntimeException('Onboarding response is unavailable');
        }
        $data = json_decode($raw, true, 32, JSON_THROW_ON_ERROR);
        if (!is_array($data) || $response->getStatusCode() < 200 || $response->getStatusCode() >= 300) {
            // Never forward remote errors (which may contain addresses or credentials).
            if (in_array($response->getStatusCode(), [400, 401, 403, 404, 409, 422], true)) {
                throw new \DomainException('Target authorization or reservation was rejected', $response->getStatusCode());
            }
            throw new \RuntimeException('Onboarding remote operation is unverified');
        }
        return $data;
    }
}
