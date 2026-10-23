<?php

declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;

use OCP\DB\QueryBuilder\IQueryBuilder;
use OCP\Files\File;
use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\IConfig;
use OCP\IDBConnection;

/** Bulk current-version summary with fresh source guards and signed pagination. */
final class FolderSummaryService {
    private const PAGE_SIZE = 200;
    private const CURSOR_AGE = 300;
    public function __construct(private IRootFolder $rootFolder, private EmployeeFileStatusService $files,
        private IDBConnection $db, private IConfig $config, private BindingRegistryService $bindings,
        private SourcePairingRegistryService $pairs, private EventConnectionService $connections,
        private SourceContentRevisionService $contentRevisions) {}

    public function forUser(string $uid, int $folderId, ?string $cursor = null): ?array {
        if ($this->config->getSystemValueBool('maintenance', false)) { return null; }
        if (!$this->files->verifyUser($uid, true)) { return null; }
        $root = $this->folder($uid, $folderId);
        if ($root === null) { return null; }
        [$visible, $generation, $sources] = $this->observation($uid, $root);
        $offset = 0;
        if ($cursor !== null && $cursor !== '') {
            $page = $this->decodeCursor($cursor, $uid, $folderId);
            if (!hash_equals($generation, $page['generation'])) { throw new StaleFolderSummaryException('Summary view changed'); }
            $offset = $page['offset'];
            if ($offset > count($visible)) { throw new \InvalidArgumentException('Invalid summary page'); }
        }
        $slice = array_slice($visible, $offset, self::PAGE_SIZE, true);
        $rows = $this->bulkRows('weknora_file_status', ['file_id', 'binding_id', 'body_json', 'last_verified_at'], array_keys($slice));
        $statusRows = [];
        foreach ($rows as $row) { $statusRows[$row['binding_id'] . ':' . $row['file_id']] = $row; }
        $counts = ['ready' => 0, 'updating' => 0, 'failed' => 0, 'unverified' => 0, 'skipped' => 0];
        $pipeline = ['imported' => 0, 'skipped' => 0, 'parse_failed' => 0, 'index_completed' => 0];
        $graphs = array_fill_keys(array_merge(StatusProtocol::GRAPH_STATES, ['not_provided']), 0);
        $failures = []; $total = 0; $verifiedAt = null;
        foreach ($slice as $id => $node) {
            $source = $sources[$id] ?? null;
            if ($source === null || $source['publication_state'] !== 'eligible') { continue; }
            $total++;
            if (!isset($statusRows[$source['binding']['id'] . ':' . $id]) &&
                !$this->supportedName($node->getName())) {
                $counts['skipped']++;
                $pipeline['skipped']++;
                continue;
            }
            $status = $this->project($statusRows[$source['binding']['id'] . ':' . $id] ?? null, $node, $source);
            $state = $status['knowledge_state']; $graph = $status['graph']['state'];
            $counts[$state]++; $graphs[$graph]++;
            if (($status['sync_state'] ?? null) === 'ingested') { $pipeline['imported']++; }
            $stages = is_array($status['stages'] ?? null) ? $status['stages'] : [];
            if (in_array($stages['parse']['state'] ?? null, ['failed'], true) ||
                ($status['failure_code'] ?? null) === 'parse_failed') { $pipeline['parse_failed']++; }
            if (($stages['index']['state'] ?? null) === 'succeeded') { $pipeline['index_completed']++; }
            foreach ($stages as $stage) {
                if (($stage['state'] ?? null) === 'skipped') { $pipeline['skipped']++; break; }
            }
            if (isset($status['last_verified_at'])) {
                $verifiedAt = $verifiedAt === null ? $status['last_verified_at'] : min($verifiedAt, $status['last_verified_at']);
            }
            if (($state === 'failed' || in_array($graph, ['failed', 'partial_failed'], true)) && count($failures) < 20) {
                $failures[] = ['file_id' => $id, 'name' => $node->getName(), 'knowledge_state' => $state,
                    'graph_state' => $graph, 'error_code' => $status['failure_code'] ?? $status['graph']['error_code'] ?? null];
            }
        }
        // A second fresh identity/root audience, owner and reader scan covers
        // every counted file without repeating the entire source query per file.
        if (!$this->files->verifyUser($uid, true) || ($fresh = $this->folder($uid, $folderId)) === null) { return null; }
        [, $after] = $this->observation($uid, $fresh);
        if (!hash_equals($generation, $after)) { throw new StaleFolderSummaryException('Summary view changed'); }
        $next = $offset + count($slice); $partial = $next < count($visible);
        return ['summary' => ['generation' => $generation, 'page_offset' => $offset, 'next_offset' => $next,
            'total_visible' => $total, 'current_completed' => $counts['ready'], 'knowledge_counts' => $counts,
            'pipeline_counts' => $pipeline,
            'graph_counts' => $graphs, 'failures' => $failures, 'last_verified_at' => $verifiedAt,
            'status_stale' => $counts['unverified'] > 0, 'partial' => $partial, 'page_limit' => self::PAGE_SIZE,
            'next_cursor' => $partial ? $this->encodeCursor($uid, $folderId, $generation, $next) : null]];
    }

    private function observation(string $uid, Folder $root): array {
        [$visible, $dirs, $tree] = $this->scan($root);
        $contentRevisions = $this->contentRevisions->snapshotForFiles(array_keys($visible));
        $ancestors = []; $node = $root;
        for ($depth = 0; $node instanceof Folder && $depth < 256; $depth++) {
            if (isset($ancestors[$node->getId()])) { break; }
            $ancestors[$node->getId()] = true;
            try { $node = $node->getParent(); } catch (\Throwable $e) { break; }
        }
        $sources = []; $authority = [];
        foreach ($this->bindings->listBindings() as $binding) {
            if (!isset($dirs[$binding['root_file_id']]) && !isset($ancestors[$binding['root_file_id']])) { continue; }
            $allowed = $this->files->summaryAuthority($uid, $binding['id']);
            $entry = [$binding['id'], $binding['root_file_id'], $binding['publication_state'], $binding['publication_epoch'], $allowed];
            if (!$allowed) { $authority[] = $entry; continue; }
            $owner = $this->bindings->requireActiveRoot($binding['id']);
            [$published] = $this->scan($owner, $binding['id']);
            $reader = $this->folder($uid, $binding['root_file_id']);
            if ($reader === null) { throw new StaleFolderSummaryException('Source root access changed'); }
            $pair = $this->pairs->status($binding['id']); $connection = $this->connections->row($binding['id']);
            $entry[] = $pair; $entry[] = $connection === null ? null : [$connection['connection_id'], $connection['key_id'], $connection['status']];
            $authority[] = $entry;
            foreach ($visible as $id => $file) {
                if (!isset($published[$id]) || !$reader->isSubNode($file) || $published[$id]->getEtag() !== $file->getEtag()) { continue; }
                $this->bindings->assertNodeInSupportedMount($binding['id'], $reader, $file);
                if (isset($sources[$id])) { throw new \UnexpectedValueException('Summary sources overlap'); }
                $sources[$id] = ['binding' => $binding, 'pair' => $pair, 'connection' => $connection,
                    'publication_state' => 'eligible', 'content_revision' => $contentRevisions[$id]['revision'],
                    'writing' => $contentRevisions[$id]['writing']];
                $tree[] = ['owner-file', $id, $published[$id]->getEtag(), $published[$id]->getPermissions()];
            }
        }
        $decisions = $this->bulkRows('weknora_pub_state', ['file_id', 'binding_id', 'state', 'actor_uid', 'updated_at', 'decision_audit_id'], array_keys($visible));
        $auditIds = array_values(array_filter(array_map(static fn (array $r): int => (int)$r['decision_audit_id'], $decisions)));
        $audits = [];
        foreach ($this->bulkRows('weknora_pub_audit', ['id', 'binding_id', 'file_id', 'action', 'actor_uid', 'created_at'], $auditIds, 'id') as $audit) { $audits[(int)$audit['id']] = $audit; }
        foreach ($decisions as $decision) {
            $id = (int)$decision['file_id'];
            if (!isset($sources[$id]) || $sources[$id]['binding']['id'] !== $decision['binding_id']) { continue; }
            if (!in_array($decision['state'], ['eligible', 'withdrawn'], true)) { throw new \UnexpectedValueException('Unknown publication decision'); }
            $revision = (int)$decision['decision_audit_id'];
            if ($revision < 0) { throw new \UnexpectedValueException('Invalid publication decision'); }
            if ($revision > 0) {
                $audit = $audits[$revision] ?? null;
                if ($audit === null || $audit['binding_id'] !== $decision['binding_id'] || (int)$audit['file_id'] !== $id ||
                    $audit['action'] !== $decision['state'] || $audit['actor_uid'] !== $decision['actor_uid'] ||
                    (int)$audit['created_at'] !== (int)$decision['updated_at']) { throw new \UnexpectedValueException('Publication audit is unverified'); }
            }
            $sources[$id]['publication_state'] = $decision['state'];
        }
        $statuses = $this->bulkRows('weknora_file_status', ['file_id', 'binding_id', 'publication_epoch', 'head_revision',
            'status_revision', 'state_sha256', 'last_verified_at'], array_keys($visible));
        foreach ($statuses as &$status) { $status['fresh'] = $status['last_verified_at'] !== null && (int)$status['last_verified_at'] >= time() - StatusProtocol::MAX_AGE; }
        unset($status);
        usort($tree, static fn (array $a, array $b): int => ($a[1] <=> $b[1]) ?: strcmp($a[0], $b[0]));
        usort($authority, static fn (array $a, array $b): int => strcmp($a[0], $b[0]));
        $generation = hash('sha256', json_encode([$uid, $root->getId(), $tree, $authority, $decisions, $statuses, $contentRevisions], JSON_THROW_ON_ERROR));
        return [$visible, $generation, $sources];
    }

    private function scan(Folder $root, ?string $bindingId = null): array {
        $queue = [$root]; $dirs = []; $visible = []; $tree = [];
        while ($queue !== []) {
            $folder = array_pop($queue);
            if (!$folder->isReadable() || isset($dirs[$folder->getId()])) { continue; }
            $dirs[$folder->getId()] = true; $tree[] = ['folder', $folder->getId(), $folder->getEtag(), $folder->getPermissions()];
            foreach ($folder->getDirectoryListing() as $node) {
                if (!$root->isSubNode($node)) { throw new \UnexpectedValueException('Summary node escaped its root'); }
                if ($bindingId !== null) { $this->bindings->assertNodeInSupportedMount($bindingId, $root, $node); }
                if (!$node->isReadable()) { continue; }
                if ($node instanceof Folder) { $queue[] = $node; }
                elseif ($node instanceof File) { $visible[$node->getId()] = $node; $tree[] = ['file', $node->getId(), $node->getEtag(), $node->getPermissions()]; }
            }
        }
        ksort($visible, SORT_NUMERIC); return [$visible, $dirs, $tree];
    }

    private function project(?array $row, File $node, array $source): array {
        $unknown = ['knowledge_state' => 'unverified', 'graph' => ['state' => 'not_provided']];
        if ($this->config->getSystemValueBool('maintenance', false)) { return $unknown; }
        if ($source['writing']) { return ['knowledge_state' => 'unverified', 'graph' => ['state' => 'unverified']]; }
        if ($row === null || !is_array($source['pair']) || !is_array($source['connection'])) { return $unknown; }
        try {
            $s = StatusProtocol::decode($row['body_json'], time()); $pair = $source['pair']; $connection = $source['connection'];
            if ($s['source_content_revision'] !== $source['content_revision']) {
                return ['knowledge_state' => 'unverified', 'graph' => ['state' => 'unverified']];
            }
            if ($pair['state'] !== 'active' || $connection['status'] !== 'active' ||
                $s['instance_id'] !== $this->config->getSystemValueString('instanceid') || $s['binding_id'] !== $source['binding']['id'] ||
                $s['connection_id'] !== $connection['connection_id'] || $s['pair_operation_id'] !== $pair['operation_id'] ||
                $s['tenant_id'] !== (string)$pair['tenant_id'] || $s['knowledge_base_id'] !== $pair['knowledge_base_id'] ||
                $s['data_source_id'] !== $pair['data_source_id'] || (int)$s['file_id'] !== $node->getId() ||
                $s['source_etag'] !== $node->getEtag() || (int)$s['publication_epoch'] !== $source['binding']['publication_epoch']) { return $unknown; }
            return StatusProtocol::projection($s, $row['last_verified_at'] === null ? null : (int)$row['last_verified_at'], time());
        } catch (\Throwable $e) { return $unknown; }
    }

    private function supportedName(string $name): bool {
        $extension = strtolower(pathinfo($name, PATHINFO_EXTENSION));
        return in_array($extension, [
            'pdf', 'txt', 'docx', 'doc', 'epub', 'html', 'htm', 'mhtml',
            'md', 'markdown', 'xmind', 'png', 'jpg', 'jpeg', 'gif', 'csv',
            'xlsx', 'xls', 'pptx', 'ppt', 'json', 'mp3', 'wav', 'm4a',
            'flac', 'ogg',
        ], true);
    }

    private function bulkRows(string $table, array $columns, array $ids, string $key = 'file_id'): array {
        $rows = [];
        foreach (array_chunk(array_values(array_unique($ids)), 500) as $chunk) {
            $q = $this->db->getQueryBuilder();
            $r = $q->select(...$columns)->from($table)->where($q->expr()->in($key, $q->createNamedParameter($chunk, IQueryBuilder::PARAM_INT_ARRAY)))
                ->orderBy($key)->executeQuery();
            try { $rows = array_merge($rows, $r->fetchAllAssociative()); } finally { $r->closeCursor(); }
        }
        usort($rows, static fn (array $a, array $b): int => ((int)$a[$key] <=> (int)$b[$key]) ?: strcmp($a['binding_id'] ?? '', $b['binding_id'] ?? ''));
        return $rows;
    }
    private function folder(string $uid, int $id): ?Folder {
        $view = $this->rootFolder->getUserFolder($uid);
        foreach ($view->getById($id) as $node) {
            if ($node instanceof Folder && $node->getId() === $id && $view->isSubNode($node) && $node->isReadable()) { return $node; }
        }
        return null;
    }
    private function encodeCursor(string $uid, int $id, string $generation, int $offset): string {
        $body = rtrim(strtr(base64_encode(json_encode(['uid' => $uid, 'folder_id' => $id, 'generation' => $generation,
            'offset' => $offset, 'issued_at' => time()], JSON_THROW_ON_ERROR)), '+/', '-_'), '=');
        return $body . '.' . hash_hmac('sha256', $body, $this->cursorSecret());
    }
    private function decodeCursor(string $cursor, string $uid, int $id): array {
        if (strlen($cursor) > 1024 || !preg_match('/\A([A-Za-z0-9_-]+)\.([a-f0-9]{64})\z/D', $cursor, $parts) ||
            !hash_equals(hash_hmac('sha256', $parts[1], $this->cursorSecret()), $parts[2])) { throw new \InvalidArgumentException('Invalid summary cursor'); }
        $data = json_decode((string)base64_decode(strtr($parts[1], '-_', '+/'), true), true, 8, JSON_THROW_ON_ERROR);
        if (!is_array($data) || ($data['uid'] ?? null) !== $uid || ($data['folder_id'] ?? null) !== $id ||
            !is_int($data['offset'] ?? null) || $data['offset'] < 0 || !is_int($data['issued_at'] ?? null) ||
            $data['issued_at'] > time() + 5 || $data['issued_at'] < time() - self::CURSOR_AGE ||
            !is_string($data['generation'] ?? null) || !preg_match('/\A[a-f0-9]{64}\z/D', $data['generation'])) { throw new \InvalidArgumentException('Invalid summary cursor'); }
        return $data;
    }
    private function cursorSecret(): string {
        $secret = $this->config->getSystemValueString('secret');
        if (strlen($secret) < 16) { throw new \RuntimeException('Summary cursor key is unavailable'); }
        return hash_hmac('sha256', 'nextcloud-weknora-folder-summary-v1', $secret);
    }
}
