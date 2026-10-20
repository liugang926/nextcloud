<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;
use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\IDBConnection;

/** Persistent post-operation discovery converges with explicit UI intents. */
final class DirectoryDiscoveryService {
    public function __construct(private IDBConnection $db, private IRootFolder $files,
        private DirectoryPublicationService $directories, private BindingRegistryService $bindings, private ControlRecoveryService $recovery, private \OCP\IConfig $config) {}
    public function enqueue(int $fileId): void {
        if ($fileId < 1) { return; }
        // Conflict leaves the original operation UUID intact across repeated events.
        $this->db->insertIgnoreConflict('weknora_dir_discover', ['file_id' => $fileId,
            'operation_id' => self::uuid(), 'queued_at' => time(), 'next_attempt_at' => 0,
            'lease_until' => 0, 'attempt_count' => 0]);
    }
    public function deliverDue(int $limit = 20): int {
        $this->recovery->requireAutomationOpen($this->config);
        $q = $this->db->getQueryBuilder();
        $r = $q->select('*')->from('weknora_dir_discover')
            ->where($q->expr()->lte('next_attempt_at', $q->createNamedParameter(time())))
            ->andWhere($q->expr()->lte('lease_until', $q->createNamedParameter(time())))
            ->orderBy('queued_at')->setMaxResults($limit)->executeQuery();
        try { $rows = $r->fetchAllAssociative(); } finally { $r->closeCursor(); }
        $done = 0;
        foreach ($rows as $row) {
            $q = $this->db->getQueryBuilder();
            if ($q->update('weknora_dir_discover')->set('lease_until', $q->createNamedParameter(time() + 600))
                ->where($q->expr()->eq('file_id', $q->createNamedParameter($row['file_id'])))
                ->andWhere($q->expr()->lte('lease_until', $q->createNamedParameter(time())))->executeStatement() !== 1) { continue; }
            try {
                foreach ($this->directories->rules() as $rule) {
                    if (!$rule['auto_enabled'] || $rule['discovery'] !== 'direct_children') { continue; }
                    $folder = null;
                    foreach ($this->files->getUserFolder($rule['owner_uid'])->getById((int)$row['file_id']) as $node) {
                        if ($node instanceof Folder && $node->getId() === (int)$row['file_id'] && $node->isReadable()) { $folder = $node; break; }
                    }
                    if ($folder === null || $folder->getParent()->getId() !== $rule['container_file_id']) { continue; }
                    if ($this->hasIntent($folder)) { break; }
                    $this->directories->associate($rule['automation_uid'], $folder->getId(), [
                        'operation_id' => $row['operation_id'], 'mode' => 'create', 'name' => $folder->getName()], false);
                    break;
                }
                $q = $this->db->getQueryBuilder();
                $q->delete('weknora_dir_discover')->where($q->expr()->eq('file_id', $q->createNamedParameter($row['file_id'])))->executeStatement();
                $done++;
            } catch (\Throwable $e) {
                $q = $this->db->getQueryBuilder();
                $q->update('weknora_dir_discover')->set('lease_until', $q->createNamedParameter(0))
                    ->set('next_attempt_at', $q->createNamedParameter(time() + 30))
                    ->set('attempt_count', $q->createNamedParameter((int)$row['attempt_count'] + 1))
                    ->where($q->expr()->eq('file_id', $q->createNamedParameter($row['file_id'])))->executeStatement();
            }
        }
        return $done;
    }
    private function hasIntent(Folder $folder): bool {
        if (DirectoryTargetClosureService::lookup($this->db, 'root_key', DirectoryPublicationService::physicalKey($folder)) !== null) { return true; }
        foreach ($this->bindings->listBindings() as $binding) {
            if ($binding['root_file_id'] === $folder->getId()) { return true; }
        }
        $q = $this->db->getQueryBuilder();
        $r = $q->select('operation_id')->from('weknora_dir_intent')
            ->where($q->expr()->orX(
                $q->expr()->eq('root_file_id', $q->createNamedParameter($folder->getId())),
                $q->expr()->andX($q->expr()->eq('parent_file_id', $q->createNamedParameter($folder->getParent()->getId())),
                    $q->expr()->eq('folder_name', $q->createNamedParameter($folder->getName())),
                    $q->expr()->eq('root_file_id', $q->createNamedParameter(0)))))
            ->setMaxResults(1)->executeQuery();
        try { return $r->fetchOne() !== false; } finally { $r->closeCursor(); }
    }
    private static function uuid(): string {
        $b = random_bytes(16); $b[6] = chr((ord($b[6]) & 15) | 64); $b[8] = chr((ord($b[8]) & 63) | 128);
        $h = bin2hex($b);
        return substr($h, 0, 8) . '-' . substr($h, 8, 4) . '-' . substr($h, 12, 4) . '-' . substr($h, 16, 4) . '-' . substr($h, 20);
    }
}
