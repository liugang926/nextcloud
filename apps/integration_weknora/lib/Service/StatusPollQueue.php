<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;

use OCP\IDBConnection;

/** New files are queued without requiring a first status push or an open browser. */
final class StatusPollQueue {
    public function __construct(private IDBConnection $db, private BindingRegistryService $bindings,
        private SourcePairingRegistryService $pairings) {}

    public static function enqueueInTransaction(IDBConnection $db, string $bindingId, int $fileId): void {
        if (!$db->inTransaction() || $fileId < 1) { throw new \InvalidArgumentException('Queue requires a source transaction'); }
        $token = bin2hex(random_bytes(16));
        $db->insertIgnoreConflict('weknora_status_pending', ['binding_id' => $bindingId, 'file_id' => $fileId,
            'last_attempt_at' => 0, 'revision_token' => $token]);
        $q = $db->getQueryBuilder();
        $q->update('weknora_status_pending')->set('revision_token', $q->createNamedParameter($token))
            ->set('last_attempt_at', $q->createNamedParameter(0))
            ->where($q->expr()->eq('binding_id', $q->createNamedParameter($bindingId)))
            ->andWhere($q->expr()->eq('file_id', $q->createNamedParameter($fileId)))->executeStatement();
    }

    /** Atomic leases allow several bounded CLI workers without duplicate claim storms. */
    public function claim(int $now): ?array {
        $this->db->beginTransaction();
        try {
            $q = $this->db->getQueryBuilder();
            $r = $q->select('*')->from('weknora_status_pending')
                ->where($q->expr()->lt('last_attempt_at', $q->createNamedParameter($now - 60)))
                ->orderBy('last_attempt_at')->addOrderBy('file_id')->setMaxResults(1)->forUpdate()->executeQuery();
            try { $row = $r->fetchAssociative(); } finally { $r->closeCursor(); }
            if ($row !== false) {
                $u = $this->db->getQueryBuilder();
                $u->update('weknora_status_pending')->set('last_attempt_at', $u->createNamedParameter($now))
                    ->where($u->expr()->eq('binding_id', $u->createNamedParameter($row['binding_id'])))
                    ->andWhere($u->expr()->eq('file_id', $u->createNamedParameter((int)$row['file_id'])))
                    ->andWhere($u->expr()->eq('revision_token', $u->createNamedParameter($row['revision_token'])))->executeStatement();
            }
            $this->db->commit(); return $row === false ? null : $row;
        } catch (\Throwable $e) { $this->db->rollBack(); throw $e; }
    }

    public function complete(array $claim): void {
        $q = $this->db->getQueryBuilder();
        $q->delete('weknora_status_pending')->where($q->expr()->eq('binding_id', $q->createNamedParameter($claim['binding_id'])))
            ->andWhere($q->expr()->eq('file_id', $q->createNamedParameter((int)$claim['file_id'])))
            ->andWhere($q->expr()->eq('revision_token', $q->createNamedParameter($claim['revision_token'])))->executeStatement();
    }

    /** File-cache pages cover pre-existing files and missing write events, without scanning source bytes. */
    public function discover(int $now): int {
        $count = 0;
        foreach ($this->bindings->listBindings() as $binding) {
            if ($binding['publication_state'] !== 'active') { continue; }
            try {
                $pair = $this->pairings->status($binding['id']);
                if ($pair === null || $pair['state'] !== 'active') { continue; }
                $root = $this->bindings->requireActiveRoot($binding['id']);
                $storage = $root->getStorage()->getCache()->getNumericStorageId();
                $path = rtrim($root->getInternalPath(), '/') . '/';
                $this->scanBinding($binding['id'], (int)$storage, $path, $now, $count,
                    (string)$binding['publication_epoch'], $pair);
            } catch (\Throwable $e) { /* Unknown roots stay closed and are retried. */ }
        }
        return $count;
    }

    private function scanBinding(string $id, int $storage, string $path, int $now, int &$count, string $epoch, array $pair): void {
        $this->db->beginTransaction();
        try {
            $this->db->insertIgnoreConflict('weknora_status_scan', ['binding_id' => $id, 'after_file_id' => 0, 'last_scan_at' => 0]);
            $s = $this->db->getQueryBuilder();
            $r = $s->select('*')->from('weknora_status_scan')->where($s->expr()->eq('binding_id', $s->createNamedParameter($id)))->forUpdate()->executeQuery();
            try { $scan = $r->fetchAssociative(); } finally { $r->closeCursor(); }
            if ($scan === false) { throw new \RuntimeException('Scan cursor missing'); }
            if ((int)$scan['after_file_id'] === 0 && (int)$scan['last_scan_at'] > $now - 60) { $this->db->commit(); return; }
            $q = $this->db->getQueryBuilder();
            $r = $q->select('f.fileid', 'f.etag', 'p.source_etag', 'p.fresh_until', 'p.body_json', 'v.revision', 'v.writing')
                ->from('filecache', 'f')->innerJoin('f', 'mimetypes', 'm', $q->expr()->eq('f.mimetype', 'm.id'))
                ->leftJoin('f', 'weknora_file_status', 'p', $q->expr()->andX($q->expr()->eq('f.fileid', 'p.file_id'),
                    $q->expr()->eq('p.binding_id', $q->createNamedParameter($id))))
                ->leftJoin('f', 'weknora_source_file_rev', 'v', $q->expr()->eq('f.fileid', 'v.file_id'))
                ->where($q->expr()->eq('f.storage', $q->createNamedParameter($storage)))
                ->andWhere($q->expr()->like('f.path', $q->createNamedParameter($this->db->escapeLikeParameter($path) . '%')))
                ->andWhere($q->expr()->neq('m.mimetype', $q->createNamedParameter('httpd/unix-directory')))
                ->andWhere($q->expr()->gt('f.fileid', $q->createNamedParameter((int)$scan['after_file_id'])))
                ->orderBy('f.fileid')->setMaxResults(200)->executeQuery();
            try { $rows = $r->fetchAllAssociative(); } finally { $r->closeCursor(); }
            $after = 0;
            foreach ($rows as $row) {
                $after = (int)$row['fileid'];
                $stored = json_decode((string)($row['body_json'] ?? ''), true);
                if (!(bool)$row['writing'] && $row['source_etag'] === $row['etag'] &&
                    ($stored['source_content_revision'] ?? '0') === (string)($row['revision'] ?? '0') &&
                    ($stored['publication_epoch'] ?? null) === $epoch && ($stored['pair_operation_id'] ?? null) === $pair['operation_id'] &&
                    ($stored['knowledge_base_id'] ?? null) === $pair['knowledge_base_id'] && ($stored['data_source_id'] ?? null) === $pair['data_source_id'] &&
                    $row['fresh_until'] !== null && (int)$row['fresh_until'] > $now + 60) { continue; }
                // Do not reset an in-flight lease just because a periodic scan sees the same file.
                $this->db->insertIgnoreConflict('weknora_status_pending', ['binding_id' => $id, 'file_id' => $after,
                    'last_attempt_at' => 0, 'revision_token' => bin2hex(random_bytes(16))]);
                $count++;
            }
            $u = $this->db->getQueryBuilder();
            $u->update('weknora_status_scan')->set('after_file_id', $u->createNamedParameter(count($rows) < 200 ? 0 : $after))
                ->set('last_scan_at', $u->createNamedParameter($now))->where($u->expr()->eq('binding_id', $u->createNamedParameter($id)))->executeStatement();
            $this->db->commit();
        } catch (\Throwable $e) { $this->db->rollBack(); throw $e; }
    }
}
