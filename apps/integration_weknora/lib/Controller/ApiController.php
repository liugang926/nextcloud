<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Controller;

use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoCSRFRequired;
use OCP\AppFramework\Http\Attribute\PublicPage;
use OCP\AppFramework\Http\JSONResponse;
use OCP\AppFramework\Http\Response;
use OCP\AppFramework\Http\StreamResponse;
use OCP\Files\File;
use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\IConfig;
use OCP\IRequest;
use OCP\IURLGenerator;
use OCA\IntegrationWeknora\Service\FilePublicationStateService;
use OCP\Lock\ILockingProvider;

final class ApiController extends Controller {
    private const APP_ID = 'integration_weknora';
    private const MANIFEST_PAGE_SIZE = 200;
    private const MAX_CONTENT_BYTES = 67108864;

    public function __construct(
        IRequest $request,
        private IConfig $config,
        private IRootFolder $rootFolder,
        private IURLGenerator $urlGenerator,
        private FilePublicationStateService $publicationState,
    ) {
        parent::__construct(self::APP_ID, $request);
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function capabilities(): Response {
        if (!$this->isAuthorized()) {
            return $this->unauthorized();
        }

        return $this->json([
            'protocol_version' => '1',
            'instance_id' => $this->config->getSystemValueString('instanceid'),
        ]);
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function bindings(): Response {
        if (!$this->isAuthorized()) {
            return $this->unauthorized();
        }

        try {
            $bindings = $this->loadBindings();
        } catch (\UnexpectedValueException $exception) {
            return $this->configurationError();
        }

        return $this->json([
            'bindings' => array_map(static fn (array $binding): array => [
                'id' => $binding['id'],
                'name' => $binding['name'],
                'root_file_id' => $binding['root_file_id'],
            ], $bindings),
        ]);
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function manifest(string $id): Response {
        if (!$this->isAuthorized()) {
            return $this->unauthorized();
        }

        try {
            $binding = $this->findBinding($id);
            if ($binding === null) {
                return $this->notFound();
            }
            [$userFolder, $bindingRoot] = $this->resolveRoot($binding);
            if ($bindingRoot === null) {
                return $this->notFound();
            }

            $items = $this->collectFiles($binding, $userFolder, $bindingRoot);
            usort($items, static fn (array $a, array $b): int =>
                ($a['file_id'] <=> $b['file_id']));
            $snapshot = array_map(static fn (array $item): array => [
                $item['file_id'],
                $item['etag'],
                $item['path'],
                $item['mime_type'],
                $item['size'],
                $item['mtime'],
            ], $items);
            $generation = hash('sha256', json_encode([
                $binding['id'],
                $binding['root_file_id'],
                $snapshot,
            ], JSON_THROW_ON_ERROR));

            $cursor = $this->request->getParam('cursor', '');
            $offset = $this->decodeManifestCursor($cursor, $generation);
            if ($offset === null) {
                return $this->json(['error' => 'manifest_changed'], 409);
            }
            if ($offset > count($items)) {
                return $this->json(['error' => 'invalid_cursor'], 400);
            }
            $page = array_slice($items, $offset, self::MANIFEST_PAGE_SIZE);
            $nextOffset = $offset + count($page);
            $complete = $nextOffset >= count($items);

            return $this->json([
                'generation' => $generation,
                'items' => $page,
                'complete' => $complete,
                'next_cursor' => $complete ? null : $this->encodeManifestCursor($nextOffset, $generation),
            ]);
        } catch (\UnexpectedValueException $exception) {
            return $this->configurationError();
        } catch (\Throwable $exception) {
            return $this->serverError();
        }
    }

    #[PublicPage]
    #[NoCSRFRequired]
    public function content(string $id, int $fileId): Response {
        if (!$this->isAuthorized()) {
            return $this->unauthorized();
        }

        if ($fileId < 1) {
            return $this->notFound();
        }

        try {
            $binding = $this->findBinding($id);
            if ($binding === null) {
                return $this->notFound();
            }
            [$userFolder, $bindingRoot] = $this->resolveRoot($binding);
            if ($bindingRoot === null) {
                return $this->notFound();
            }

            // Resolve by ID inside the binding root, then verify both folder bounds.
            // A file ID from another user's tree must never become a download URL.
            foreach ($bindingRoot->getById($fileId) as $node) {
                if (!$node instanceof File || $node->getId() !== $fileId ||
                    !$bindingRoot->isSubNode($node) || !$userFolder->isSubNode($node) ||
                    !$node->isReadable() || $this->publicationState->isExcluded($id, $fileId)) {
                    continue;
                }

                // Hold a shared Nextcloud lock until the bytes have been
                // copied. A StreamResponse reads only after this controller
                // returns, so passing the live source stream directly could
                // pair an old ETag with bytes written by a concurrent save.
                $node->lock(ILockingProvider::LOCK_SHARED);
                try {
                    $etag = $node->getEtag();
                    $size = (int)$node->getSize();
                    $ifMatch = trim($this->request->getHeader('If-Match'));
                    if ($ifMatch !== '' && trim($ifMatch, '"') !== $etag) {
                        return $this->json(['error' => 'version_changed'], 412);
                    }
                    if ($size > self::MAX_CONTENT_BYTES) {
                        return $this->json(['error' => 'content_too_large'], 413);
                    }
                    $source = $node->fopen('r');
                    if (!is_resource($source)) {
                        return $this->serverError();
                    }
                    $buffer = fopen('php://temp/maxmemory:2097152', 'w+b');
                    if (!is_resource($buffer)) {
                        fclose($source);
                        return $this->serverError();
                    }
                    try {
                        $copied = stream_copy_to_stream($source, $buffer, self::MAX_CONTENT_BYTES + 1);
                    } finally {
                        fclose($source);
                    }
                    if ($copied === false || $copied !== $size || $node->getEtag() !== $etag) {
                        fclose($buffer);
                        return $this->json(['error' => 'version_changed'], 412);
                    }
                    rewind($buffer);
                    return new StreamResponse($buffer, 200, [
                        'Content-Type' => $node->getMimeType(),
                        'Content-Length' => (string)$copied,
                        'ETag' => '"' . $etag . '"',
                        'Cache-Control' => 'no-store',
                        'X-Content-Type-Options' => 'nosniff',
                    ]);
                } finally {
                    $node->unlock(ILockingProvider::LOCK_SHARED);
                }
            }

            return $this->notFound();
        } catch (\UnexpectedValueException $exception) {
            return $this->configurationError();
        } catch (\Throwable $exception) {
            return $this->serverError();
        }
    }

    private function isAuthorized(): bool {
        $expectedHash = $this->config->getAppValue(self::APP_ID, 'service_token_sha256', '');
        if (!is_string($expectedHash) || !preg_match('/\A[a-fA-F0-9]{64}\z/D', $expectedHash)) {
            return false;
        }

        $authorization = $this->request->getHeader('Authorization');
        if (!preg_match('/\ABearer[ \t]+([^\s]+)\z/iD', $authorization, $matches)) {
            return false;
        }

        return hash_equals(strtolower($expectedHash), hash('sha256', $matches[1]));
    }

    /** @return list<array{id: string, name: string, owner_uid: string, root_file_id: int}> */
    private function loadBindings(): array {
        $raw = $this->config->getAppValue(self::APP_ID, 'bindings', '[]');
        try {
            $decoded = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
        } catch (\JsonException $exception) {
            throw new \UnexpectedValueException('Invalid bindings JSON', 0, $exception);
        }

        if (!is_array($decoded) || !array_is_list($decoded)) {
            throw new \UnexpectedValueException('Bindings must be a JSON array');
        }

        $ids = [];
        foreach ($decoded as $binding) {
            if (!is_array($binding) ||
                !isset($binding['id'], $binding['name'], $binding['owner_uid'], $binding['root_file_id']) ||
                !is_string($binding['id']) || !preg_match('/\A[A-Za-z0-9_-]+\z/D', $binding['id']) ||
                !is_string($binding['name']) || $binding['name'] === '' ||
                !is_string($binding['owner_uid']) || $binding['owner_uid'] === '' ||
                !is_int($binding['root_file_id']) || $binding['root_file_id'] < 1 ||
                isset($ids[$binding['id']])) {
                throw new \UnexpectedValueException('Invalid or duplicate binding');
            }
            $ids[$binding['id']] = true;
        }

        return $decoded;
    }

    /** @return array{id: string, name: string, owner_uid: string, root_file_id: int}|null */
    private function findBinding(string $id): ?array {
        foreach ($this->loadBindings() as $binding) {
            if ($binding['id'] === $id) {
                return $binding;
            }
        }
        return null;
    }

    /** @return array{Folder, Folder|null} */
    private function resolveRoot(array $binding): array {
        $userFolder = $this->rootFolder->getUserFolder($binding['owner_uid']);
        if ($userFolder->getId() === $binding['root_file_id']) {
            return [$userFolder, null];
        }

        foreach ($userFolder->getById($binding['root_file_id']) as $node) {
            if ($node instanceof Folder && $node->getId() === $binding['root_file_id'] &&
                $userFolder->isSubNode($node)) {
                return [$userFolder, $node];
            }
        }
        return [$userFolder, null];
    }

    /** @return list<array{file_id: int, etag: string, name: string, path: string, mime_type: string, size: int, mtime: int, url: string}> */
    private function collectFiles(array $binding, Folder $userFolder, Folder $bindingRoot): array {
        $items = [];
        $pending = [$bindingRoot];
        $visitedFolders = [];
        $visitedFiles = [];

        while ($pending !== []) {
            /** @var Folder $folder */
            $folder = array_pop($pending);
            $folderId = $folder->getId();
            if (isset($visitedFolders[$folderId])) {
                continue;
            }
            $visitedFolders[$folderId] = true;

            foreach ($folder->getDirectoryListing() as $node) {
                if (!$folder->isSubNode($node) || !$bindingRoot->isSubNode($node) ||
                    !$userFolder->isSubNode($node)) {
                    continue;
                }
                if ($node instanceof File && $this->publicationState->isExcluded($binding['id'], $node->getId())) {
                    continue;
                }
                // A partial manifest must never look like a confirmed deletion.
                // Reject the scan if a node in the publication tree cannot be read.
                if (!$node->isReadable()) {
                    throw new \UnexpectedValueException('Unreadable node in publication tree');
                }

                if ($node instanceof Folder) {
                    $pending[] = $node;
                    continue;
                }
                if (!$node instanceof File || isset($visitedFiles[$node->getId()])) {
                    continue;
                }

                $relativePath = $bindingRoot->getRelativePath($node->getPath());
                if (!is_string($relativePath) || $relativePath === '/') {
                    continue;
                }
                $visitedFiles[$node->getId()] = true;
                $items[] = [
                    'file_id' => $node->getId(),
                    'etag' => $node->getEtag(),
                    'name' => $node->getName(),
                    'path' => ltrim($relativePath, '/'),
                    'mime_type' => $node->getMimeType(),
                    'size' => (int)$node->getSize(),
                    'mtime' => (int)$node->getMTime(),
                    'url' => $this->urlGenerator->linkToRouteAbsolute(
                        self::APP_ID . '.api.content',
                        ['id' => $binding['id'], 'fileId' => $node->getId()],
                    ),
                ];
            }
        }

        return $items;
    }

    private function encodeManifestCursor(int $offset, string $generation): string {
        return rtrim(strtr(base64_encode(json_encode([$offset, $generation], JSON_THROW_ON_ERROR)), '+/', '-_'), '=');
    }

    /** A changed generation invalidates the entire traversal; the connector must restart. */
    private function decodeManifestCursor(mixed $raw, string $generation): ?int {
        if ($raw === '') {
            return 0;
        }
        if (!is_string($raw) || strlen($raw) > 256 || !preg_match('/\A[A-Za-z0-9_-]+\z/D', $raw)) {
            return null;
        }
        $decoded = base64_decode(strtr($raw, '-_', '+/'), true);
        if ($decoded === false) {
            return null;
        }
        try {
            $value = json_decode($decoded, true, 8, JSON_THROW_ON_ERROR);
        } catch (\JsonException $exception) {
            return null;
        }
        if (!is_array($value) || count($value) !== 2 || !is_int($value[0]) || $value[0] < 0 ||
            !is_string($value[1]) || !hash_equals($generation, $value[1])) {
            return null;
        }
        return $value[0];
    }

    private function json(array $data, int $status = 200, array $headers = []): JSONResponse {
        return new JSONResponse($data, $status, ['Cache-Control' => 'no-store'] + $headers);
    }

    private function unauthorized(): JSONResponse {
        return $this->json(['error' => 'unauthorized'], 401, ['WWW-Authenticate' => 'Bearer realm="WeKnora Integration"']);
    }

    private function notFound(): JSONResponse {
        return $this->json(['error' => 'not_found'], 404);
    }

    private function configurationError(): JSONResponse {
        return $this->json(['error' => 'invalid_configuration'], 503);
    }

    private function serverError(): JSONResponse {
        return $this->json(['error' => 'file_unavailable'], 500);
    }
}
