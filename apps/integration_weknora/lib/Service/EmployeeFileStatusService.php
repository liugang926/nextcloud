<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\Files\File;
use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\IConfig;

/** Resolves session-visible source state before trusting a paired machine feed. */
final class EmployeeFileStatusService {
    public function __construct(
        private BindingRegistryService $bindings,
        private FilePublicationStateService $publication,
        private IRootFolder $rootFolder,
        private IConfig $config,
        private RemoteFileStatusService $remote,
    ) {
    }

    /**
     * @return array<string, mixed>|null Null when this user cannot read the file.
     */
    public function forUser(string $uid, int $fileId): ?array {
        $before = $this->sourceForUser($uid, $fileId);
        if ($before === null) {
            return null;
        }
        if ($before['source_state'] !== 'in_scope') {
            unset($before['_binding_id']);
            return $before;
        }
        $remote = $this->remote->status((string)$before['_binding_id'], $fileId,
            (string)$before['source_etag']);
        // A remote lookup can take several seconds. Recheck the session's
        // read access, publication state, binding and ETag before returning it.
        $after = $this->sourceForUser($uid, $fileId);
        if ($after === null) {
            return null;
        }
        if ($remote !== null && $after['source_state'] === 'in_scope' &&
            $after['_binding_id'] === $before['_binding_id'] &&
            $after['source_etag'] === $before['source_etag']) {
            $after = array_merge($after, $remote);
        }
        unset($after['_binding_id']);
        return $after;
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
            'qa_available' => false,
            'weknora_login_url' => null,
        ];

        // A user may read a file through a different share while not being
        // able to read its publication root. That does not reveal a binding.
        foreach ($this->bindings->listBindings() as $binding) {
            $ownerRoot = $this->bindings->requireActiveRoot($binding['id']);
            $ownerFileReadable = false;
            foreach ($ownerRoot->getById($fileId) as $node) {
                if ($node instanceof File && $node->getId() === $fileId &&
                    $this->readableInside($node, $ownerRoot)) {
                    $ownerFileReadable = true;
                    break;
                }
            }
            if (!$ownerFileReadable) {
                continue;
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
                    $state = $this->publication->getState($binding['id'], $fileId);
                    $stopped = $binding['publication_state'] === 'stopped';
                    try {
                        $freshEpoch = $this->bindings->requirePublicationActive($binding['id']);
                        if ($freshEpoch !== $binding['publication_epoch']) {
                            throw new \UnexpectedValueException('Publication state changed during status read');
                        }
                    } catch (BindingPublicationStoppedException $exception) {
                        $stopped = true;
                    }
                    return array_merge($base, [
                        'source_state' => $stopped ? 'publication_stopped' :
                            ($state === 'withdrawn' ? 'withdrawn' : 'in_scope'),
                        '_binding_id' => $binding['id'],
                        'file_withdrawn' => $state === 'withdrawn',
                        'binding_name' => $binding['name'],
                        'weknora_login_url' => $stopped || $state === 'withdrawn' ? null : $this->loginUrl(),
                    ]);
                }
            }
        }

        return $base + ['source_state' => 'outside_scope'];
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
}
