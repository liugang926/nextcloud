<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\Http\Client\IClientService;
use OCP\IConfig;
use OCP\IGroupManager;
use OCP\IURLGenerator;
use OCP\IUserManager;

/**
 * One administrator connection per Nextcloud instance.
 *
 * A connection discovers multiple workspaces and stores only scoped
 * onboarding credentials. Directory rules are generated from the selected
 * workspaces so administrators never copy tenant, template, file or pairing
 * identifiers into the ordinary setup form.
 */
final class ConnectionService {
    private const APP = 'integration_weknora';
    private const PROFILE_KEY = 'connection';
    private const ORIGINS_KEY = 'connection_origins';
    private const CREDENTIALS_KEY = 'weknora_onboarding_credentials';

    public function __construct(
        private IConfig $config,
        private IClientService $clients,
        private IURLGenerator $urls,
        private IRootFolder $files,
        private DirectoryPublicationService $directories,
        private IdentityMappingService $identities,
        private IGroupManager $groups,
        private IUserManager $users,
    ) {
    }

    public function hasConnection(): bool {
        return $this->profile() !== null;
    }

    public function canManage(string $uid, int $folderId, bool $creating): bool {
        try {
            $folder = $this->folder($uid, $folderId);
        } catch (\Throwable $ignored) {
            return false;
        }
        $folderAllowed = $creating
            ? $folder->isCreatable()
            : $folder->isCreatable() ||
                (($folder->getPermissions() & \OCP\Constants::PERMISSION_UPDATE) !== 0);
        if (!$folderAllowed) {
            return false;
        }
        return $this->visibleTargets($uid) !== [];
    }

    /** @return array<string, mixed> */
    public function status(): array {
        $profile = $this->profile();
        if ($profile === null) {
            return ['connected' => false, 'spaces' => []];
        }
        $profile['spaces'] = array_map(
            fn (mixed $space): mixed => is_array($space) ? $this->spaceWithPrincipalNames($space) : $space,
            is_array($profile['spaces'] ?? null) ? $profile['spaces'] : [],
        );
        return ['connected' => true] + $profile;
    }

    /**
     * Keep visibility IDs as the stored authority while returning the current
     * display names needed by the compact administrator summary.
     *
     * @param array<string,mixed> $space
     * @return array<string,mixed>
     */
    private function spaceWithPrincipalNames(array $space): array {
        try {
            $visibility = SpaceVisibilityPolicy::normalize($space['visibility'] ?? null);
        } catch (\Throwable) {
            return $space;
        }
        $groups = [];
        foreach ($visibility['groups'] as $id) {
            $group = $this->groups->get($id);
            $groups[] = [
                'id' => $id,
                'name' => $group === null ? $id : (string)$group->getDisplayName(),
            ];
        }
        $users = [];
        foreach ($visibility['users'] as $id) {
            $user = $this->users->get($id);
            $users[] = [
                'id' => $id,
                'name' => $user === null ? $id : (string)$user->getDisplayName(),
            ];
        }
        $space['visibility'] = $visibility + [
            'principal_names' => ['groups' => $groups, 'users' => $users],
        ];
        return $space;
    }

    /**
     * Discover workspaces visible to the administrator token. The token is
     * used once for this request and is never written to Nextcloud storage.
     *
     * @return list<array<string, mixed>>
     */
    public function discover(string $origin, string $token): array {
        $origin = $this->canonicalOrigin($origin);
        $response = $this->request($origin, $token, 'GET',
            '/api/v1/integrations/nextcloud/connection/workspaces');
        $spaces = $response['spaces'] ?? null;
        if (!is_array($spaces) || !array_is_list($spaces)) {
            throw new \UnexpectedValueException('WeKnora returned an invalid workspace list');
        }
        $out = [];
        foreach ($spaces as $space) {
            if (!is_array($space) || !$this->validTenantId($space['tenant_id'] ?? null)) {
                continue;
            }
            $out[] = [
                'tenant_id' => (string)$space['tenant_id'],
                'tenant_name' => is_string($space['tenant_name'] ?? null)
                    ? $space['tenant_name'] : '',
                'processing_template_id' => is_string($space['processing_template_id'] ?? null)
                    ? $space['processing_template_id'] : '',
                'processing_template_name' => is_string($space['processing_template_name'] ?? null)
                    ? $space['processing_template_name'] : '',
                'eligible' => ($space['eligible'] ?? false) === true,
                'reason' => is_string($space['reason'] ?? null) ? $space['reason'] : '',
            ];
        }
        return $out;
    }

    /**
     * Authorize the selected workspaces and replace the previous connection
     * credentials. The administrator token is discarded after this call.
     *
     * @param list<string|int|array<string,mixed>> $tenantIds
     * @return array<string, mixed>
     */
    public function connect(string $origin, string $token, array $tenantIds, string $uid): array {
        $origin = $this->canonicalOrigin($origin);
        $selections = $this->normalizeSelections($tenantIds);
        $tenantIds = array_keys($selections);
        if ($tenantIds === [] || count($tenantIds) > 32) {
            throw new \InvalidArgumentException('Select between one and 32 workspaces');
        }
        foreach ($tenantIds as $tenantId) {
            if (!$this->validTenantId($tenantId)) {
                throw new \InvalidArgumentException('Invalid workspace selection');
            }
            $this->validateVisiblePrincipals($selections[$tenantId]);
        }

        $directoryId = (string)$this->config->getAppValue(self::APP, 'ad_directory_id', '');
        $actorDirectoryId = '';
        $actorGuid = '';
        $identity = null;
        try {
            $identity = $this->identities->forUser($uid);
        } catch (\Throwable $ignored) {
            // The connection can still be established, but this administrator
            // will need a WeKnora membership or an explicit delegation before
            // binding a directory.
        }
        if (is_array($identity)) {
            $actorDirectoryId = (string)$identity['directory_id'];
            $actorGuid = (string)$identity['object_guid'];
            if ($directoryId !== '' && $directoryId !== $actorDirectoryId) {
                throw new \DomainException('The configured directory identity does not match the administrator');
            }
            $directoryId = $actorDirectoryId;
        }
        if ($directoryId === '') {
            throw new \DomainException('The Nextcloud directory identifier is not configured');
        }

        $instanceId = $this->config->getSystemValueString('instanceid');
        $baseUrl = $this->originFromUrl($this->urls->getAbsoluteURL('/'));
        if ($instanceId === '' || $baseUrl === '') {
            throw new \UnexpectedValueException('The Nextcloud instance identity is unavailable');
        }
        $response = $this->request($origin, $token, 'POST',
            '/api/v1/integrations/nextcloud/connection/authorize', [
                'instance_id' => $instanceId,
                'base_url' => $baseUrl,
                'directory_id' => $directoryId,
                'actor_directory_id' => $actorDirectoryId,
                'actor_object_guid' => $actorGuid,
                'tenant_ids' => array_map('intval', $tenantIds),
            ]);
        $credentials = $response['credentials'] ?? null;
        if (!is_array($credentials) || !array_is_list($credentials) || $credentials === []) {
            throw new \UnexpectedValueException('WeKnora returned no connection credentials');
        }

        $stored = $this->config->getSystemValue(self::CREDENTIALS_KEY, []);
        if (!is_array($stored)) {
            $stored = [];
        }
        $spaces = [];
        $returnedTenants = [];
        $returnedCredentials = [];
        foreach ($credentials as $credential) {
            if (!is_array($credential) || !$this->validTenantId($credential['tenant_id'] ?? null) ||
                !is_string($credential['credential_id'] ?? null) ||
                !str_starts_with($credential['credential_id'], 'nc-conn-') ||
                !is_string($credential['token'] ?? null) ||
                !$this->validToken($credential['token']) ||
                !is_string($credential['processing_template_id'] ?? null) ||
                $credential['processing_template_id'] === '') {
                throw new \UnexpectedValueException('WeKnora returned an invalid connection credential');
            }
            $tenantId = (string)$credential['tenant_id'];
            $credentialId = $credential['credential_id'];
            if (isset($returnedTenants[$tenantId]) || isset($returnedCredentials[$credentialId])) {
                throw new \UnexpectedValueException('WeKnora returned duplicate connection credentials');
            }
            $returnedTenants[$tenantId] = true;
            $returnedCredentials[$credentialId] = true;
            $stored[$credentialId] = [
                'tenant_id' => $tenantId,
                'weknora_origin' => $origin,
                'token' => $credential['token'],
            ];
            $spaces[] = [
                'tenant_id' => $tenantId,
                'name' => is_string($credential['tenant_name'] ?? null)
                    ? $credential['tenant_name'] : '',
                'processing_template_id' => $credential['processing_template_id'],
                'processing_template_name' => is_string($credential['processing_template_name'] ?? null)
                    ? $credential['processing_template_name'] : '',
                'credential_id' => $credentialId,
                'visibility' => $selections[$tenantId],
            ];
        }
        if (count($spaces) !== count($tenantIds)) {
            throw new \UnexpectedValueException('WeKnora did not authorize every selected workspace');
        }
        foreach ($tenantIds as $tenantId) {
            if (!isset($returnedTenants[$tenantId])) {
                throw new \UnexpectedValueException('WeKnora did not authorize every selected workspace');
            }
        }
        $this->config->setSystemValue(self::CREDENTIALS_KEY, $stored);
        $profile = [
            'origin' => $origin,
            'base_url' => $baseUrl,
            'connection_id' => is_string($response['connection_id'] ?? null)
                ? $response['connection_id'] : '',
            'spaces' => $spaces,
            'connected_at' => time(),
        ];
        $this->config->setAppValue(self::APP, self::PROFILE_KEY,
            json_encode($profile, JSON_THROW_ON_ERROR));
        $this->rememberOrigin($origin);
        return $this->status();
    }

    /**
     * Update only workspace visibility. This keeps the existing scoped
     * credentials and their processing templates unchanged.
     *
     * @param list<array<string,mixed>> $selections
     * @return array<string,mixed>
     */
    public function updateVisibility(array $selections): array {
        $profile = $this->profile();
        if ($profile === null) {
            throw new \DomainException('WeKnora is not connected');
        }
        $normalized = $this->normalizeSelections($selections);
        $existing = [];
        foreach ($profile['spaces'] as $space) {
            $existing[(string)$space['tenant_id']] = true;
        }
        if (count($normalized) !== count($existing) ||
            array_diff_key($existing, $normalized) !== [] ||
            array_diff_key($normalized, $existing) !== []) {
            throw new \InvalidArgumentException('Visibility must include every connected workspace');
        }
        foreach ($normalized as $visibility) {
            $this->validateVisiblePrincipals($visibility);
        }
        foreach ($profile['spaces'] as &$space) {
            $space['visibility'] = $normalized[(string)$space['tenant_id']];
        }
        unset($space);
        $this->config->setAppValue(self::APP, self::PROFILE_KEY,
            json_encode($profile, JSON_THROW_ON_ERROR));
        return $this->status();
    }

    /** @return array<string, mixed> */
    public function disconnect(): array {
        $profile = $this->profile();
        if ($profile === null) {
            return $this->status();
        }
        $credentialIds = [];
        foreach ($profile['spaces'] as $space) {
            if (is_array($space) && is_string($space['credential_id'] ?? null)) {
                $credentialIds[] = $space['credential_id'];
            }
        }
        if ($credentialIds !== []) {
            $revoked = false;
            $denied = false;
            foreach ($credentialIds as $credentialId) {
                try {
                    $this->request((string)$profile['origin'], $this->connectionToken($credentialId),
                        'POST', '/api/v1/integrations/nextcloud/onboarding/connection/revoke', [
                            'credential_ids' => $credentialIds,
                        ]);
                    $revoked = true;
                    break;
                } catch (\DomainException $exception) {
                    // A previously rotated credential is already unusable;
                    // try the remaining credentials before reporting failure.
                    $denied = true;
                }
            }
            if (!$revoked && !$denied) {
                throw new \RuntimeException('WeKnora connection revocation is unverified');
            }
        }
        $this->config->deleteAppValue(self::APP, self::PROFILE_KEY);
        return $this->status();
    }

    /**
     * Generate the internal rule for a directory on first use. This keeps the
     * existing binding/pairing/ACL protocol while removing rule IDs, file IDs,
     * tenant IDs and template IDs from the administrator and employee forms.
     *
     * @return array<string, mixed>
     */
    public function ensureRule(string $uid, int $folderId, bool $creating): array {
        $profile = $this->profile();
        if ($profile === null) {
            throw new \DomainException('WeKnora is not connected');
        }
        if (!$this->canManage($uid, $folderId, $creating)) {
            throw new \DomainException('Directory management is forbidden');
        }
        $folder = $this->folder($uid, $folderId);
        // One explicit rule per parent covers both binding an existing child
        // and creating a new child. This avoids overlapping child rules when a
        // user first creates a directory and later binds its parent.
        $container = $folder->getParent();
        $targets = $this->visibleTargets($uid);
        if ($targets === []) {
            throw new \DomainException('No authorized WeKnora workspace is available');
        }
        $rules = $this->directories->rules();
        foreach ($rules as $rule) {
            if (($rule['owner_uid'] ?? null) !== $uid ||
                ($rule['weknora_origin'] ?? null) !== $profile['origin']) {
                continue;
            }
            try {
                $candidate = $this->folder($uid, (int)($rule['container_file_id'] ?? 0));
            } catch (\Throwable $ignored) {
                continue;
            }
            if ($creating && $candidate->getId() === $folder->getId() &&
                ($rule['discovery'] ?? null) === 'direct_children') {
                return $this->refreshRuleTargets($rule, $targets);
            }
            if ($candidate->getId() !== $folder->getId() &&
                DirectoryPublicationService::contains($candidate, $folder)) {
                return $this->refreshRuleTargets($rule, $targets);
            }
        }

        $rule = [
            'id' => 'auto_' . substr(hash('sha256',
                'nc-connection-rule-v1' . "\0" . $uid . "\0" . $container->getId() . "\0" .
                'scope'), 0, 40),
            'owner_uid' => $uid,
            'container_file_id' => $container->getId(),
            'tenant_id' => $targets[0]['tenant_id'],
            'processing_template_id' => $targets[0]['processing_template_id'],
            'weknora_origin' => $profile['origin'],
            'base_url' => $profile['base_url'],
            'discovery' => 'explicit',
            'delegates' => [$uid],
            'auto_enabled' => false,
            'targets' => $targets,
        ];
        foreach ($rules as $existing) {
            if (($existing['id'] ?? null) === $rule['id']) {
                return $this->refreshRuleTargets($existing, $targets);
            }
        }
        try {
            return $this->directories->saveRule($rule, 0);
        } catch (\DomainException $exception) {
            foreach ($this->directories->rules() as $existing) {
                if (($existing['id'] ?? null) === $rule['id']) {
                    return $this->refreshRuleTargets($existing, $targets);
                }
            }
            throw $exception;
        }
    }

    private function refreshRuleTargets(array $rule, array $targets): array {
        $targetKey = static function (array $target): string {
            return implode("\0", [
                (string)($target['tenant_id'] ?? ''),
                (string)($target['processing_template_id'] ?? ''),
                (string)($target['credential_id'] ?? ''),
            ]);
        };
        $existing = array_values(array_filter($rule['targets'] ?? [], 'is_array'));
        $current = array_map(
            static fn (array $target): string => $targetKey($target),
            array_values(array_filter($targets, 'is_array')),
        );
        $old = array_map($targetKey, $existing);
        sort($current, SORT_STRING);
        sort($old, SORT_STRING);
        if ($current === $old) {
            return $rule;
        }
        $updated = $rule;
        $updated['targets'] = $targets;
        $updated['tenant_id'] = $targets[0]['tenant_id'];
        $updated['processing_template_id'] = $targets[0]['processing_template_id'];
        unset($updated['revision'], $updated['discovery_after_id']);
        try {
            return $this->directories->saveRule($updated, (int)($rule['revision'] ?? 0));
        } catch (\DomainException $exception) {
            // A rule with durable intents keeps its original target scope; its
            // old credential remains enabled on the WeKnora side.
            return $rule;
        }
    }

    /** @return list<string> */
    public function visibleTenantIds(string $uid): array {
        $tenants = [];
        foreach ($this->visibleTargets($uid) as $target) {
            $tenants[] = (string)$target['tenant_id'];
        }
        return $tenants;
    }

    /**
     * Filter targets from a rule created before the current visibility
     * restriction was applied.
     *
     * @param array<string,mixed> $result
     * @return array<string,mixed>
     */
    public function filterCandidates(string $uid, array $result): array {
        $allowed = array_fill_keys($this->visibleTenantIds($uid), true);
        $targets = [];
        foreach (is_array($result['targets'] ?? null) ? $result['targets'] : [] as $target) {
            if (is_array($target) && isset($allowed[(string)($target['tenant_id'] ?? '')])) {
                $targets[] = $target;
            }
        }
        if ($targets === []) {
            $result['targets'] = [];
            $result['tenant_id'] = null;
            $result['processing_template_id'] = null;
            $result['candidates'] = [];
            return $result;
        }
        $result['targets'] = $targets;
        $result['tenant_id'] = $targets[0]['tenant_id'];
        $result['processing_template_id'] = $targets[0]['processing_template_id'];
        $result['candidates'] = $targets[0]['candidates'] ?? [];
        return $result;
    }

    /** @param array<string,mixed> $choice */
    public function assertTargetAllowed(string $uid, array $choice): void {
        if (!$this->hasConnection()) {
            return;
        }
        $tenantId = $choice['tenant_id'] ?? null;
        if (!is_string($tenantId) && !is_int($tenantId)) {
            throw new \DomainException('A visible WeKnora workspace is required');
        }
        if (!in_array((string)$tenantId, $this->visibleTenantIds($uid), true)) {
            throw new \DomainException('The WeKnora workspace is not visible to this user');
        }
    }

    /**
     * @return list<array{tenant_id:string,processing_template_id:string,credential_id:string,space_name:string}>
     */
    private function visibleTargets(string $uid): array {
        $profile = $this->profile();
        if ($profile === null) {
            return [];
        }
        $admin = $this->groups->isAdmin($uid);
        $user = $admin ? null : $this->users->get($uid);
        if (!$admin && $user === null) {
            return [];
        }
        $userGroups = $user === null ? [] : $this->groups->getUserGroupIds($user);
        $targets = [];
        foreach ($profile['spaces'] as $space) {
            if (!is_array($space) || !$this->validTenantId($space['tenant_id'] ?? null) ||
                !is_string($space['processing_template_id'] ?? null) ||
                !is_string($space['credential_id'] ?? null)) {
                continue;
            }
            $visibility = SpaceVisibilityPolicy::normalize($space['visibility'] ?? null);
            if (!SpaceVisibilityPolicy::allows($visibility, $uid, $userGroups, $admin)) {
                continue;
            }
            $targets[] = [
                'tenant_id' => (string)$space['tenant_id'],
                'processing_template_id' => $space['processing_template_id'],
                'credential_id' => $space['credential_id'],
                'space_name' => is_string($space['name'] ?? null) ? $space['name'] : '',
            ];
        }
        return $targets;
    }

    /**
     * @param list<string|int|array<string,mixed>> $selections
     * @return array<string,array{mode:string,groups:list<string>,users:list<string>}>
     */
    private function normalizeSelections(array $selections): array {
        $out = [];
        foreach ($selections as $selection) {
            if (is_array($selection)) {
                $tenantId = $selection['tenant_id'] ?? null;
                $visibility = $selection['visibility'] ?? null;
            } else {
                $tenantId = $selection;
                $visibility = null;
            }
            if (!$this->validTenantId($tenantId)) {
                throw new \InvalidArgumentException('Invalid workspace selection');
            }
            $tenantId = (string)$tenantId;
            if (isset($out[$tenantId])) {
                throw new \InvalidArgumentException('Duplicate workspace selection');
            }
            $out[$tenantId] = SpaceVisibilityPolicy::normalize($visibility);
        }
        return $out;
    }

    /** @param array{mode:string,groups:list<string>,users:list<string>} $visibility */
    private function validateVisiblePrincipals(array $visibility): void {
        if (($visibility['mode'] ?? null) !== SpaceVisibilityPolicy::MODE_RESTRICTED) {
            return;
        }
        foreach ($visibility['groups'] as $gid) {
            if (!$this->groups->groupExists($gid)) {
                throw new \InvalidArgumentException('Unknown workspace visibility group');
            }
        }
        foreach ($visibility['users'] as $uid) {
            if ($this->users->get($uid) === null) {
                throw new \InvalidArgumentException('Unknown workspace visibility user');
            }
        }
    }

    /** @return array<string, mixed>|null */
    private function profile(): ?array {
        $raw = $this->config->getAppValue(self::APP, self::PROFILE_KEY, '');
        if ($raw === '') {
            return null;
        }
        try {
            $profile = json_decode($raw, true, 32, JSON_THROW_ON_ERROR);
            if (!is_array($profile) || !is_string($profile['origin'] ?? null) ||
                !is_string($profile['base_url'] ?? null) ||
                !is_array($profile['spaces'] ?? null) || $profile['spaces'] === []) {
                return null;
            }
            foreach ($profile['spaces'] as &$space) {
                if (!is_array($space)) {
                    return null;
                }
                $space['visibility'] = SpaceVisibilityPolicy::normalize($space['visibility'] ?? null);
            }
            unset($space);
            return $profile;
        } catch (\Throwable $exception) {
            return null;
        }
    }

    private function rememberOrigin(string $origin): void {
        $raw = $this->config->getAppValue(self::APP, self::ORIGINS_KEY, '[]');
        $origins = json_decode($raw, true);
        if (!is_array($origins)) {
            $origins = [];
        }
        $origins[] = $origin;
        $origins = array_values(array_unique(array_filter($origins, 'is_string')));
        $this->config->setAppValue(self::APP, self::ORIGINS_KEY,
            json_encode($origins, JSON_THROW_ON_ERROR));
    }

    private function connectionToken(string $credentialId): string {
        $stored = $this->config->getSystemValue(self::CREDENTIALS_KEY, []);
        $credential = is_array($stored) ? ($stored[$credentialId] ?? null) : null;
        if (!is_array($credential) || !is_string($credential['token'] ?? null)) {
            throw new \UnexpectedValueException('Connection credential is unavailable');
        }
        return $credential['token'];
    }

    /** @return array<string, mixed> */
    private function request(string $origin, string $token, string $method, string $path, array $body = []): array {
        if (!$this->validToken($token)) {
            throw new \InvalidArgumentException('Invalid WeKnora connection token');
        }
        $url = $origin . $path;
        $options = [
            'headers' => [
                'Accept' => 'application/json',
                // A browser JWT is accepted as Bearer; a platform API key is
                // accepted through X-API-Key. Only one path can authenticate.
                'Authorization' => 'Bearer ' . $token,
                'X-API-Key' => $token,
            ],
            'allow_redirects' => false,
            'http_errors' => false,
            'verify' => true,
            'stream' => true,
            'timeout' => 15,
            'connect_timeout' => 3,
            'nextcloud' => ['allow_local_address' => true],
        ];
        if ($method === 'GET' && $body !== []) {
            $url .= '?' . http_build_query($body, '', '&', PHP_QUERY_RFC3986);
        } elseif ($method !== 'GET') {
            $options['headers']['Content-Type'] = 'application/json';
            $options['body'] = json_encode($body, JSON_THROW_ON_ERROR);
        }
        $client = $this->clients->newClient();
        $response = $method === 'GET'
            ? $client->get($url, $options)
            : $client->post($url, $options);
        $raw = $response->getBody();
        if (is_resource($raw)) {
            $stream = $raw;
            try {
                $raw = stream_get_contents($stream, 1048577);
            } finally {
                fclose($stream);
            }
        }
        if (!is_string($raw) || strlen($raw) > 1048576) {
            throw new \RuntimeException('WeKnora connection response is unavailable');
        }
        $status = $response->getStatusCode();
        if ($status === 401 || $status === 403) {
            throw new \DomainException('WeKnora connection was denied');
        }
        if ($status === 409 || $status === 422) {
            throw new \DomainException('WeKnora connection requires attention');
        }
        if ($status < 200 || $status >= 300) {
            throw new \RuntimeException('WeKnora connection is unavailable');
        }
        try {
            $data = json_decode($raw, true, 64, JSON_THROW_ON_ERROR);
        } catch (\Throwable $exception) {
            throw new \UnexpectedValueException('WeKnora returned invalid JSON');
        }
        if (!is_array($data)) {
            throw new \UnexpectedValueException('WeKnora returned an invalid response');
        }
        return $data;
    }

    private function canonicalOrigin(string $raw): string {
        $raw = trim($raw);
        if ($raw === '' || strlen($raw) > 512 || preg_match('/[\x00-\x20\x7f]/', $raw)) {
            throw new \InvalidArgumentException('Invalid WeKnora origin');
        }
        $parts = parse_url($raw);
        if (!is_array($parts) || isset($parts['user']) || isset($parts['pass']) ||
            isset($parts['query']) || isset($parts['fragment']) ||
            !isset($parts['scheme'], $parts['host']) ||
            (isset($parts['path']) && $parts['path'] !== '' && $parts['path'] !== '/')) {
            throw new \InvalidArgumentException('Invalid WeKnora origin');
        }
        $devHttp = getenv('WEKNORA_EVENT_DEV_HTTP') === '1';
        if (($parts['scheme'] !== 'https' && !($devHttp && $parts['scheme'] === 'http')) ||
            !is_string($parts['host']) || strlen($parts['host']) > 253 ||
            !preg_match('/\A[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\z/D', strtolower($parts['host'])) ||
            str_contains($parts['host'], '..')) {
            throw new \InvalidArgumentException('Invalid WeKnora origin');
        }
        $origin = strtolower($parts['scheme']) . '://' . strtolower($parts['host']);
        if (isset($parts['port'])) {
            if ($parts['port'] < 1 || $parts['port'] > 65535) {
                throw new \InvalidArgumentException('Invalid WeKnora origin');
            }
            $origin .= ':' . $parts['port'];
        }
        return $origin;
    }

    private function originFromUrl(string $url): string {
        $parts = parse_url($url);
        if (!is_array($parts) || !isset($parts['scheme'], $parts['host'])) {
            return '';
        }
        $origin = strtolower($parts['scheme']) . '://' . strtolower($parts['host']);
        if (isset($parts['port'])) {
            $origin .= ':' . $parts['port'];
        }
        return $origin;
    }

    private function validTenantId(mixed $id): bool {
        return (is_string($id) || is_int($id)) &&
            preg_match('/\A[1-9][0-9]{0,19}\z/D', (string)$id) === 1 &&
            filter_var((string)$id, FILTER_VALIDATE_INT,
                ['options' => ['min_range' => 1]]) !== false;
    }

    private function validToken(string $token): bool {
        return strlen($token) >= 16 && strlen($token) <= 4096 &&
            preg_match('/[\x00-\x20\x7f]/', $token) !== 1;
    }

    private function folder(string $uid, int $folderId): Folder {
        if ($folderId < 1) {
            throw new \OutOfBoundsException('Directory is unavailable');
        }
        foreach ($this->files->getUserFolder($uid)->getById($folderId) as $node) {
            if ($node instanceof Folder && $node->getId() === $folderId && $node->isReadable()) {
                return $node;
            }
        }
        throw new \OutOfBoundsException('Directory is unavailable');
    }
}
