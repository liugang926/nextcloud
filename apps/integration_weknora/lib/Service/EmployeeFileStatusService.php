<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\Files\File;
use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\IConfig;
use OCP\IUserManager;

/** Resolves session-visible source state before trusting a paired machine feed. */
final class EmployeeFileStatusService {
    private array $identityProofs = [];
    public function __construct(
        private BindingRegistryService $bindings,
        private FilePublicationStateService $publication,
        private ManifestSnapshotService $manifestSnapshots,
        private IRootFolder $rootFolder,
        private IConfig $config,
        private RemoteFileStatusService $remote,
        private StatusProjectionService $projection,
        private RemoteStageStatusService $stageStatuses,
        private IUserManager $users,
        private IdentityMappingService $identities,
        private SourceAudienceVerifier $audience,
        private SourceContentRevisionService $contentRevisions,
    ) {
    }

    /**
     * @return array<string, mixed>|null Null when this user cannot read the file.
     */
    public function forUser(string $uid, int $fileId, bool $refresh = true): ?array {
        if (!$this->verifyUser($uid)) { return null; }
        $before = $this->sourceForUser($uid, $fileId);
        if ($before === null) {
            return null;
        }
        if ($before['source_state'] !== 'in_scope') {
            unset($before['_binding_id'], $before['_publication_epoch'],
                $before['_publication_revision']);
            return $before;
        }
        if ($before['source_content_revision'] === null) {
            // A write fence blocks old knowledge, not visibility of the
            // reader's original file. Never poll/fall back using an unknown version.
            unset($before['_binding_id'], $before['_publication_epoch'], $before['_publication_revision']);
            return $before + ['schema_version' => 2, 'status_stale' => true, 'graph' => ['state' => 'unverified']];
        }
        $remote = null;
        try {
            $remote = $this->projection->forFile((string)$before['_binding_id'], $fileId,
                (string)$before['source_etag'], (int)$before['_publication_epoch']);
            if ($refresh && ($remote === null || $remote['status_stale'])) {
                $this->stageStatuses->refresh((string)$before['_binding_id'], $fileId, (string)$before['source_etag']);
                $remote = $this->projection->forFile((string)$before['_binding_id'], $fileId,
                    (string)$before['source_etag'], (int)$before['_publication_epoch']);
            }
        } catch (\Throwable $e) {
            // Source changes, restore gates, stale metadata, and DB failures
            // never become a ready proof through a legacy fallback.
            $remote = ['knowledge_state' => 'unverified', 'qa_available' => false,
                'schema_version' => 2, 'status_stale' => true, 'graph' => ['state' => 'unverified']];
        }
        if ($remote === null && $refresh && ($before['source_content_revision'] ?? '0') === '0') {
            $remote = $this->remote->status((string)$before['_binding_id'], $fileId, (string)$before['source_etag']);
            if ($remote !== null) {
                $remote['schema_version'] = 1;
                $remote['graph'] = ['state' => 'not_provided'];
            }
        }
        // A remote lookup can take several seconds. Recheck the session's
        // read access, publication state, binding and ETag before returning it.
        if (!$this->verifyUser($uid, $refresh)) { return null; }
        $after = $this->sourceForUser($uid, $fileId);
        if ($after === null) {
            return null;
        }
        if ($remote !== null && self::sameRemoteObservation($before, $after)) {
            $after = array_merge($after, $remote);
            if ($remote['knowledge_state'] === 'ready') {
                $after['weknora_ask_url'] = $this->askUrl(
                    (string)$after['_binding_id'], $fileId, (string)$after['source_etag']);
            }
        }
        unset($after['_binding_id'], $after['_publication_epoch'],
            $after['_publication_revision']);
        return $after;
    }

    /** A page verifies at its two boundaries; reuse inside one bounded request only. */
    public function verifyUser(string $uid, bool $force = false): bool {
        $user = $this->users->get($uid);
        if ($user === null || !$user->isEnabled()) { unset($this->identityProofs[$uid]); return false; }
        if ($user->getBackendClassName() !== 'LDAP') { return true; }
        $proof = $this->identityProofs[$uid] ?? null;
        if (!$force && $proof !== null && $proof['at'] >= time() - 4) { return true; }
        try {
            $identity = $this->identities->forUser($uid);
            $this->identityProofs[$uid] = ['at' => time(), 'identity' => $identity];
            if ($force) { $this->audience->invalidate(); }
            return true;
        } catch (\Throwable $e) { unset($this->identityProofs[$uid]); return false; }
    }

    /** Current whole-root qualification for stable, user-scoped summary generations. */
    public function summaryAuthority(string $uid, string $bindingId, bool $requireActive = true): bool {
        if (!$this->verifyUser($uid)) { return false; }
        try {
            $binding = null;
            foreach ($this->bindings->listBindings() as $candidate) {
                if ($candidate['id'] === $bindingId) { $binding = $candidate; break; }
            }
            if ($binding === null) { return false; }
            $ownerRoot = null;
            if ($requireActive) {
                $this->bindings->requirePublicationActive($bindingId);
                $ownerRoot = $this->bindings->requireActiveRoot($bindingId);
            } else {
                $ownerView = $this->rootFolder->getUserFolder($binding['owner_uid']);
                foreach ($ownerView->getById($binding['root_file_id']) as $root) {
                    if ($root instanceof Folder && $ownerView->isSubNode($root) && $root->isReadable()) { $ownerRoot = $root; break; }
                }
                if ($ownerRoot === null) { return false; }
            }
            $view = $this->rootFolder->getUserFolder($uid);
            $readable = false;
            foreach ($view->getById($binding['root_file_id']) as $root) {
                if ($root instanceof Folder && $view->isSubNode($root) && $root->isReadable()) { $readable = true; break; }
            }
            if (!$readable) { return false; }
            $user = $this->users->get($uid);
            if ($user === null || !$user->isEnabled()) { return false; }
            if ($user->getBackendClassName() !== 'LDAP') { return true; }
            $identity = $this->identityProofs[$uid]['identity'];
            return $this->audience->allows($ownerRoot, $binding['owner_uid'], $user,
                $identity['directory_id'], $identity['object_guid']);
        } catch (\Throwable $e) { return false; }
    }

    /** Publication changes can invalidate a status despite an unchanged ETag. */
    private static function sameRemoteObservation(array $before, array $after): bool {
        $beforeContentRevision = array_key_exists('source_content_revision', $before) ? $before['source_content_revision'] : '0';
        $afterContentRevision = array_key_exists('source_content_revision', $after) ? $after['source_content_revision'] : '0';
        return $before['source_state'] === 'in_scope' &&
            $after['source_state'] === 'in_scope' &&
            $before['_binding_id'] === $after['_binding_id'] &&
            $before['_publication_epoch'] === $after['_publication_epoch'] &&
            $before['_publication_revision'] === $after['_publication_revision'] &&
            $beforeContentRevision !== null && $afterContentRevision !== null &&
            $beforeContentRevision === $afterContentRevision &&
            $before['source_etag'] === $after['source_etag'];
    }

    /** @return array<string, mixed>|null */
    private function sourceForUser(string $uid, int $fileId): ?array {
        $userFolder = $this->rootFolder->getUserFolder($uid);
        $visibleFile = null;
        foreach ($userFolder->getById($fileId) as $node) {
            if ($node instanceof File && $node->getId() === $fileId &&
                $userFolder->isSubNode($node) && $node->isReadable()) {
                $visibleFile = $node;
                break;
            }
        }
        if ($visibleFile === null) {
            return null;
        }

        $base = [
            'file_id' => $fileId,
            'source_modified_at' => $visibleFile->getMTime(),
            'source_etag' => $visibleFile->getEtag(),
            'knowledge_state' => 'unverified',
            'knowledge_ready_at' => null,
            'published_source_etag' => null,
            'failure_code' => null,
            'qa_available' => false,
            'weknora_login_url' => null,
            'weknora_ask_url' => null,
        ];

        // A user may read a file through a different share while not being
        // able to read its publication root. That does not reveal a binding.
        foreach ($this->bindings->listBindings() as $binding) {
            $unavailable = false;
            try { $ownerRoot = $this->bindings->requireActiveRoot($binding['id']); }
            catch (\Throwable $error) {
                // A stopped/migrating or unavailable unrelated root cannot
                // take down status for every other KB. Read-only inspection
                // still requires this exact registered root ID and today's
                // owner/reader audience before any metadata is exposed.
                $ownerRoot = $this->readOnlyRoot($binding);
                if ($ownerRoot === null) { continue; }
                $unavailable = true;
            }
            $ownerFileReadable = false;
            foreach ($ownerRoot->getById($fileId) as $node) {
                if ($node instanceof File && $node->getId() === $fileId &&
                    $this->readableInside($node, $ownerRoot)) {
                    $this->bindings->assertNodeInSupportedMount($binding['id'], $ownerRoot, $node);
                    $ownerFileReadable = true;
                    break;
                }
            }
            if (!$ownerFileReadable) {
                continue;
            }
            $user = $this->users->get($uid);
            if ($user !== null && $user->getBackendClassName() === 'LDAP') {
                $identity = $this->identityProofs[$uid]['identity'] ?? null;
                if ($identity === null || !$this->audience->allows($ownerRoot, $binding['owner_uid'], $user,
                    $identity['directory_id'], $identity['object_guid'])) { return null; }
            }
            foreach ($userFolder->getById($binding['root_file_id']) as $root) {
                if (!$root instanceof Folder || $root->getId() !== $binding['root_file_id'] ||
                    !$userFolder->isSubNode($root) || !$root->isReadable()) {
                    continue;
                }
                foreach ($root->getById($fileId) as $userFile) {
                    if (!$userFile instanceof File || $userFile->getId() !== $fileId ||
                        !$this->readableInside($userFile, $root)) {
                        continue;
                    }
                    $this->bindings->assertNodeInSupportedMount($binding['id'], $root, $userFile);
                    $revisionBefore = $this->manifestSnapshots->publicationRevision($binding['id']);
                    $state = $this->publication->getState($binding['id'], $fileId);
                    $stopped = $unavailable || $binding['publication_state'] === 'stopped';
                    try {
                        $freshEpoch = $this->bindings->requirePublicationActive($binding['id']);
                        if ($freshEpoch !== $binding['publication_epoch']) {
                            throw new \UnexpectedValueException('Publication state changed during status read');
                        }
                    } catch (BindingPublicationStoppedException $exception) {
                        $stopped = true;
                    }
                    $revisionAfter = $this->manifestSnapshots->publicationRevision($binding['id']);
                    if ($revisionBefore !== $revisionAfter) {
                        throw new \UnexpectedValueException('Publication changed during status read');
                    }
                    try { $contentRevision = $this->contentRevisions->current($fileId); }
                    catch (\DomainException $writeFence) { $contentRevision = null; }
                    return array_merge($base, [
                        'source_state' => $stopped ? 'publication_stopped' :
                            ($state === 'withdrawn' ? 'withdrawn' : 'in_scope'),
                        '_binding_id' => $binding['id'],
                        '_publication_epoch' => $binding['publication_epoch'],
                        '_publication_revision' => $revisionAfter,
                        'source_content_revision' => $contentRevision,
                        'file_withdrawn' => $state === 'withdrawn',
                        'binding_name' => $binding['name'],
                        'weknora_login_url' => $stopped || $state === 'withdrawn' ? null : $this->loginUrl(),
                    ]);
                }
            }
        }

        return $base + ['source_state' => 'outside_scope'];
    }

    private function readOnlyRoot(array $binding): ?Folder {
        try {
            $owner = $this->users->get($binding['owner_uid']);
            if ($owner === null || !$owner->isEnabled()) { return null; }
            $view = $this->rootFolder->getUserFolder($binding['owner_uid']);
            foreach ($view->getById($binding['root_file_id']) as $root) {
                if ($root instanceof Folder && $root->getId() === $binding['root_file_id'] &&
                    $view->isSubNode($root) && $root->isReadable()) { return $root; }
            }
        } catch (\Throwable $error) { return null; }
        return null;
    }

    private function readableInside(File|Folder $node, Folder $root): bool {
        if (!$root->isSubNode($node) &&
            !($root->getId() === $node->getId() && $root->getPath() === $node->getPath())) {
            return false;
        }
        $cursor = $node;
        $visited = [];
        for ($depth = 0; $depth < 1024; $depth++) {
            $path = $cursor->getPath();
            if (isset($visited[$path]) || !$cursor->isReadable()) {
                return false;
            }
            if ($cursor->getId() === $root->getId() && $path === $root->getPath()) {
                return true;
            }
            $visited[$path] = true;
            $parent = $cursor->getParent();
            if (!$parent instanceof Folder) {
                return false;
            }
            $cursor = $parent;
        }
        return false;
    }

    private function loginUrl(): ?string {
        $raw = trim($this->config->getAppValue('integration_weknora', 'weknora_web_url', ''));
        if ($raw === '' || filter_var($raw, FILTER_VALIDATE_URL) === false) {
            return null;
        }
        $parts = parse_url($raw);
        if (!is_array($parts) || !isset($parts['scheme'], $parts['host']) ||
            isset($parts['user']) || isset($parts['pass']) ||
            isset($parts['query']) || isset($parts['fragment'])) {
            return null;
        }
        $scheme = strtolower($parts['scheme']);
        $host = strtolower($parts['host']);
        if ($scheme !== 'https' && !($scheme === 'http' &&
            in_array($host, ['localhost', '127.0.0.1', '::1'], true))) {
            return null;
        }
        return $raw;
    }

    /** A navigation hint only. WeKnora rechecks the user's identity and source. */
    private function askUrl(string $bindingId, int $fileId, string $sourceEtag): ?string {
        $login = $this->loginUrl();
        if ($login === null) {
            return null;
        }
        $parts = parse_url($login);
        if (!is_array($parts) || !in_array($parts['path'] ?? '', ['', '/', '/login'], true)) {
            return null;
        }
        $instanceId = $this->config->getSystemValueString('instanceid');
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $instanceId) ||
            !preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $bindingId) ||
            !preg_match('/\A[A-Za-z0-9._:-]{1,256}\z/D', $sourceEtag)) {
            return null;
        }
        $host = (string)$parts['host'];
        if (str_contains($host, ':') && $host[0] !== '[') {
            $host = '[' . $host . ']';
        }
        $origin = strtolower((string)$parts['scheme']) . '://' . $host .
            (isset($parts['port']) ? ':' . $parts['port'] : '');
        return $origin . '/platform/nextcloud-ask?' . http_build_query([
            'instance_id' => $instanceId,
            'binding_id' => $bindingId,
            'file_id' => (string)$fileId,
            'source_etag' => $sourceEtag,
        ], '', '&', PHP_QUERY_RFC3986);
    }
}
