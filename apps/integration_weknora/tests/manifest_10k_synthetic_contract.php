<?php

declare(strict_types=1);

// Exercise the real manifest controller and snapshot serializer with 10,000
// synthetic file metadata rows. This is a PHP/algorithm scale check, not a
// Nextcloud filecache, PostgreSQL, network, AD, content-transfer, or 100 GB run.

namespace OCP {
    interface IRequest { public function getParam(string $key, mixed $default = null): mixed; }
    interface IConfig { public function getSystemValueString(string $key): string; }
    interface IURLGenerator {
        public function linkToRoute(string $name, array $parameters = []): string;
        public function linkToRouteAbsolute(string $name, array $parameters = []): string;
    }
    interface IDBConnection { public function getQueryBuilder(): object; }
}

namespace OCP\AppFramework {
    class Controller {
        protected \OCP\IRequest $request;
        public function __construct(string $appName, \OCP\IRequest $request) {
            $this->request = $request;
        }
    }
}

namespace OCP\AppFramework\Http {
    class Response {
        public function __construct(private int $status = 200) {}
        public function getStatus(): int { return $this->status; }
    }
    class JSONResponse extends Response {
        public function __construct(private array $data, int $status = 200, array $headers = []) {
            parent::__construct($status);
        }
        public function getData(): array { return $this->data; }
    }
}

namespace OCP\Files {
    class File {
        public function __construct(private int $id, private string $path,
            private \ManifestScale\State $state) {}
        public function getId(): int { return $this->id; }
        public function getPath(): string { return $this->path; }
        public function getName(): string { return basename($this->path); }
        public function getEtag(): string { return 'etag-' . $this->id; }
        public function getMimeType(): string { return 'text/plain'; }
        public function getSize(): int { return 16; }
        public function getMTime(): int { return 1700000000; }
        public function isReadable(): bool { return $this->state->unreadableId !== $this->id; }
    }
    class Folder {
        /** @param \Closure(): iterable<object> $listing */
        public function __construct(private int $id, private string $path,
            private \Closure $listing, private string $etag = 'root-a',
            private ?\ManifestScale\State $state = null) {}
        public function getId(): int { return $this->id; }
        public function getPath(): string { return $this->path; }
        public function getEtag(): string { return $this->etag; }
        public function setEtag(string $etag): void { $this->etag = $etag; }
        public function getDirectoryListing(): iterable {
            if ($this->state !== null) { $this->state->treeListings++; }
            return ($this->listing)();
        }
        public function isReadable(): bool { return true; }
        public function isSubNode(object $node): bool {
            return str_starts_with($node->getPath(), $this->path . '/');
        }
        public function getRelativePath(string $path): string {
            return str_starts_with($path, $this->path . '/')
                ? substr($path, strlen($this->path)) : '/';
        }
        public function getById(int $id): array {
            foreach ($this->getDirectoryListing() as $node) {
                if ($node->getId() === $id) { return [$node]; }
            }
            return [];
        }
    }
    interface IRootFolder { public function getUserFolder(string $uid): Folder; }
}

namespace OCA\IntegrationWeknora\Service {
    class BindingPublicationStoppedException extends \RuntimeException {}
    class BindingRegistryService {
        public bool $active = true;
        public int $epoch = 1;
        public function __construct(private \OCP\Files\Folder $root) {}
        public function listBindings(): array {
            return [['id' => 'scale', 'name' => 'Synthetic', 'owner_uid' => 'admin',
                'root_file_id' => $this->root->getId(), 'publication_state' => $this->active ? 'active' : 'stopped',
                'publication_epoch' => $this->epoch]];
        }
        public function requirePublicationActive(string $id): int {
            if (!$this->active) { throw new BindingPublicationStoppedException('stopped'); }
            return $this->epoch;
        }
        public function requireActiveRoot(string $id): \OCP\Files\Folder { return $this->root; }
        public function assertNodeInSupportedMount(string $id, \OCP\Files\Folder $root, object $node): void {}
    }
    class FilePublicationStateService {
        public ?int $excludedId = null;
        public function isExcluded(string $bindingId, int $fileId): bool { return $this->excludedId === $fileId; }
    }
    class PairedServiceToken {
        public function verify(\OCP\IRequest $request, ?string $bindingId = null): bool { return true; }
    }
}

namespace ManifestScale {
    final class State {
        public ?int $unreadableId = null;
        public int $treeListings = 0;
    }
    final class Request implements \OCP\IRequest {
        public string $cursor = '';
        public function getParam(string $key, mixed $default = null): mixed {
            return $key === 'cursor' ? $this->cursor : $default;
        }
    }
    final class Config implements \OCP\IConfig {
        public function getSystemValueString(string $key): string { return 'http://nextcloud.test'; }
    }
    final class URLGenerator implements \OCP\IURLGenerator {
        public function linkToRoute(string $name, array $parameters = []): string {
            return '/index.php/f/' . (int)($parameters['fileid'] ?? 0);
        }
        public function linkToRouteAbsolute(string $name, array $parameters = []): string {
            return 'http://nextcloud.test/content/' . (int)($parameters['fileId'] ?? $parameters['fileid'] ?? 0);
        }
    }
    final class RootFolder implements \OCP\Files\IRootFolder {
        public function __construct(private \OCP\Files\Folder $userFolder) {}
        public function getUserFolder(string $uid): \OCP\Files\Folder { return $this->userFolder; }
    }
    final class Result {
        private bool $read = false;
        public function __construct(private mixed $value) {}
        public function fetchOne(): mixed { return $this->value; }
        public function fetchAssociative(): array|false {
            if ($this->read || !is_array($this->value)) { return false; }
            $this->read = true;
            return $this->value;
        }
        public function closeCursor(): void {}
    }
    final class Expr {
        public function eq(string $column, mixed $value): array { return ['eq', $column, $value]; }
        public function lt(string $column, mixed $value): array { return ['lt', $column, $value]; }
    }
    final class DB implements \OCP\IDBConnection {
        /** @var array<string, array<string, mixed>> */
        public array $snapshots = [];
        public int $publicationRevision = 0;
        public function getQueryBuilder(): object { return new Query($this); }
        public function expireSnapshots(): void {
            foreach ($this->snapshots as &$row) { $row['created_at'] = time() - 601; }
            unset($row);
        }
        public function snapshotJsonBytes(): int {
            $latest = end($this->snapshots);
            return $latest === false ? 0 : strlen((string)$latest['items_json']);
        }
    }
    final class Query {
        private string $operation = '';
        private string $table = '';
        private array $conditions = [];
        private array $values = [];
        public function __construct(private DB $db) {}
        public function expr(): Expr { return new Expr(); }
        public function createNamedParameter(mixed $value): mixed { return $value; }
        public function select(string ...$columns): self { $this->operation = 'select'; return $this; }
        public function from(string $table): self { $this->table = $table; return $this; }
        public function where(array $condition): self { $this->conditions = [$condition]; return $this; }
        public function andWhere(array $condition): self { $this->conditions[] = $condition; return $this; }
        public function orderBy(string $column, string $direction): self { return $this; }
        public function setMaxResults(int $count): self { return $this; }
        public function insert(string $table): self { $this->operation = 'insert'; $this->table = $table; return $this; }
        public function values(array $values): self { $this->values = $values; return $this; }
        public function delete(string $table): self { $this->operation = 'delete'; $this->table = $table; return $this; }
        private function matches(array $row): bool {
            foreach ($this->conditions as [$operator, $column, $value]) {
                if ($operator === 'eq' && ($row[$column] ?? null) !== $value) { return false; }
                if ($operator === 'lt' && !($row[$column] < $value)) { return false; }
            }
            return true;
        }
        public function executeQuery(): Result {
            if ($this->table === 'weknora_pub_audit') {
                return new Result($this->db->publicationRevision === 0 ? false : $this->db->publicationRevision);
            }
            if ($this->table === 'weknora_manifest_snap') {
                foreach ($this->db->snapshots as $row) {
                    if ($this->matches($row)) { return new Result($row); }
                }
                return new Result(false);
            }
            throw new \LogicException('Unexpected query table: ' . $this->table);
        }
        public function executeStatement(): int {
            if ($this->table !== 'weknora_manifest_snap') { throw new \LogicException('Unexpected write'); }
            if ($this->operation === 'insert') {
                $this->db->snapshots[$this->values['snapshot_id']] = $this->values;
                return 1;
            }
            if ($this->operation === 'delete') {
                $before = count($this->db->snapshots);
                $this->db->snapshots = array_filter($this->db->snapshots,
                    fn (array $row): bool => !$this->matches($row));
                return $before - count($this->db->snapshots);
            }
            throw new \LogicException('Unexpected write operation');
        }
    }
}

namespace {
    require_once __DIR__ . '/../lib/Service/BrowserFileUrl.php';
    require_once __DIR__ . '/../lib/Service/ManifestSnapshotService.php';
    require_once __DIR__ . '/../lib/Controller/ApiController.php';

    use ManifestScale\Config;
    use ManifestScale\DB;
    use ManifestScale\Request;
    use ManifestScale\RootFolder;
    use ManifestScale\State;
    use ManifestScale\URLGenerator;
    use OCA\IntegrationWeknora\Controller\ApiController;
    use OCA\IntegrationWeknora\Service\BindingRegistryService;
    use OCA\IntegrationWeknora\Service\FilePublicationStateService;
    use OCA\IntegrationWeknora\Service\ManifestSnapshotService;
    use OCA\IntegrationWeknora\Service\PairedServiceToken;
    use OCP\Files\File;
    use OCP\Files\Folder;

    function expect(bool $condition, string $message): void {
        if (!$condition) { throw new \RuntimeException($message); }
    }
    function page(ApiController $controller, Request $request, string $cursor = ''): array {
        $request->cursor = $cursor;
        $response = $controller->manifest('scale');
        return [$response->getStatus(), $response->getData()];
    }
    /** @return array{list<int>, string, int} */
    function drain(ApiController $controller, Request $request, string $cursor = ''): array {
        $ids = [];
        $generation = '';
        $pages = 0;
        do {
            [$status, $data] = page($controller, $request, $cursor);
            expect($status === 200, 'manifest page failed');
            expect(count($data['items']) <= 200, 'page exceeded 200 items');
            if ($generation === '') { $generation = $data['generation']; }
            expect($generation === $data['generation'], 'generation changed across pages');
            foreach ($data['items'] as $item) { $ids[] = $item['file_id']; }
            $cursor = $data['next_cursor'] ?? '';
            $pages++;
            expect($pages <= 51, 'manifest pagination did not terminate');
        } while ($cursor !== '');
        return [$ids, $generation, $pages];
    }

    $state = new State();
    $request = new Request();
    $db = new DB();
    $rootPath = '/admin/files/Published';
    $root = new Folder(22, $rootPath, function () use ($rootPath, $state): iterable {
        for ($folder = 0; $folder < 10; $folder++) {
            $index = $folder;
            $path = sprintf('%s/batch-%02d', $rootPath, $index);
            yield new Folder(100 + $index, $path, function () use ($path, $index, $state): iterable {
                for ($offset = 0; $offset < 1000; $offset++) {
                    $id = 100000 + $index * 1000 + $offset;
                    yield new File($id, sprintf('%s/file-%04d.txt', $path, $offset), $state);
                }
            }, 'folder-a', $state);
        }
    }, 'root-a', $state);
    $user = new Folder(1, '/admin/files', static fn (): iterable => [$root]);
    $registry = new BindingRegistryService($root);
    $publication = new FilePublicationStateService();
    $controller = new ApiController($request, new Config(), new RootFolder($user),
        new URLGenerator(), $publication, new ManifestSnapshotService($db),
        $registry, new PairedServiceToken());

    $baselineBytes = memory_get_usage(true);
    $started = hrtime(true);
    [$status, $first] = page($controller, $request);
    $firstMs = (hrtime(true) - $started) / 1e6;
    expect($status === 200 && count($first['items']) === 200 && $first['complete'] === false,
        'first 10,000-file page failed');
    $cursor = $first['next_cursor'];
    $snapshotBytes = $db->snapshotJsonBytes();
    expect($snapshotBytes > 0 && $snapshotBytes <= 33554432, 'snapshot exceeds the production cap');
    $firstScanListings = $state->treeListings;
    expect($firstScanListings === 11, 'first page did not scan each tree folder once');
    $all = array_column($first['items'], 'file_id');
    $pages = 1;
    $pageStarted = hrtime(true);
    do {
        [$status, $data] = page($controller, $request, $cursor);
        expect($status === 200 && $data['generation'] === $first['generation'], 'stored page failed');
        expect(count($data['items']) <= 200, 'stored page too large');
        array_push($all, ...array_column($data['items'], 'file_id'));
        $cursor = $data['next_cursor'] ?? '';
        expect(($cursor === '') === ($data['complete'] === true), 'terminal page marker disagreed with cursor');
        $pages++;
        expect($pages <= 50, 'too many pages');
    } while ($cursor !== '');
    $remainingMs = (hrtime(true) - $pageStarted) / 1e6;
    $storedPageRescans = $state->treeListings - $firstScanListings;
    expect($storedPageRescans === 0, 'stored pages rescanned the source tree');
    expect($pages === 50 && count($all) === 10000 && count(array_unique($all)) === 10000,
        '10,000 files were duplicated or omitted');
    $sorted = $all;
    sort($sorted, SORT_NUMERIC);
    expect($all === $sorted, 'file IDs were not sorted');
    $firstCursor = $first['next_cursor'];

    $root->setEtag('root-b');
    [$status, $data] = page($controller, $request, $firstCursor);
    expect($status === 409 && $data['error'] === 'manifest_changed', 'root ETag change kept old cursor');
    [$status, $fresh] = page($controller, $request);
    expect($status === 200, 'new root ETag could not start a manifest');
    $db->publicationRevision++;
    $publication->excludedId = 100000;
    [$status, $data] = page($controller, $request, $fresh['next_cursor']);
    expect($status === 409 && $data['error'] === 'manifest_changed', 'withdrawal kept old cursor');
    [$ids, $newGeneration, $newPages] = drain($controller, $request);
    expect($newGeneration !== $first['generation'] && $newPages === 50 &&
        count($ids) === 9999 && !in_array(100000, $ids, true), 'withdrawal was not reflected');

    [$status, $fresh] = page($controller, $request);
    expect($status === 200, 'fresh manifest failed before stop');
    $registry->active = false;
    [$status, $data] = page($controller, $request, $fresh['next_cursor']);
    expect($status === 423 && $data['error'] === 'publication_stopped', 'stop did not close manifest');
    $registry->active = true;
    $registry->epoch++;
    [$status, $data] = page($controller, $request, $fresh['next_cursor']);
    expect($status === 409 && $data['error'] === 'manifest_changed', 'resume reused prior epoch');

    [$status, $fresh] = page($controller, $request);
    expect($status === 200, 'fresh manifest failed before expiry');
    $db->expireSnapshots();
    [$status, $data] = page($controller, $request, $fresh['next_cursor']);
    expect($status === 409 && $data['error'] === 'manifest_changed', 'expired snapshot was accepted');
    [$status, $data] = page($controller, $request, 'tampered');
    expect($status === 400 && $data['error'] === 'invalid_cursor', 'tampered cursor was accepted');

    $state->unreadableId = 109999;
    $snapshotsBeforeFailure = count($db->snapshots);
    [$status, $data] = page($controller, $request);
    expect($status === 503 && $data['error'] === 'invalid_configuration',
        'unreadable node returned a partial manifest');
    expect(count($db->snapshots) === $snapshotsBeforeFailure,
        'failed scan persisted a partial snapshot');

    echo json_encode([
        'scope' => 'synthetic_metadata_php_controller_snapshot_only',
        'php_version' => PHP_VERSION,
        'php_memory_limit' => ini_get('memory_limit'),
        'file_count' => 10000,
        'page_count' => $pages,
        'snapshot_json_bytes' => $snapshotBytes,
        'first_scan_folder_listings' => $firstScanListings,
        'stored_page_tree_rescans' => $storedPageRescans,
        'first_page_ms' => round($firstMs, 2),
        'remaining_pages_ms' => round($remainingMs, 2),
        'memory_baseline_bytes' => $baselineBytes,
        'memory_peak_bytes' => memory_get_peak_usage(true),
        'invalidations' => ['root_etag', 'withdrawal_revision', 'stop_resume_epoch',
            'snapshot_ttl', 'tampered_cursor', 'unreadable_node'],
        'not_measured' => ['Nextcloud filecache', 'PostgreSQL I/O', 'HTTP/network transfer',
            'WeKnora indexing', '100 GB source content', 'real AD or Team Folder ACL'],
    ], JSON_THROW_ON_ERROR) . "\n";
}
