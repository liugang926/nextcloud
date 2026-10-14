<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;

use OCP\IDBConnection;

/** ETags may collide for equal-size writes in one second. This ledger never changes source bytes. */
final class SourceContentRevisionService {
    public function __construct(private IDBConnection $db, private ControlRecoveryService $recovery) {}

    public function current(int $fileId): string {
        $s = $this->snapshotForFiles([$fileId])[$fileId];
        if ($s['writing']) { throw new \DomainException('Source write is incomplete'); }
        return $s['revision'];
    }

    /** @return array<int,array{revision:string,writing:bool}> */
    public function snapshotForFiles(array $ids): array {
        return $this->snapshots($ids, true);
    }
    private function snapshots(array $ids, bool $protect): array {
        $out = [];
        foreach ($ids as $id) {
            if (!is_int($id) || $id < 1) { throw new \InvalidArgumentException('Invalid file identity'); }
            $out[$id] = ['revision' => '0', 'writing' => false];
        }
        foreach (array_chunk(array_keys($out), 1000) as $batch) {
            $q = $this->db->getQueryBuilder();
            $r = $q->select('file_id', 'revision', 'writing')->from('weknora_source_file_rev')
                ->where($q->expr()->in('file_id', $q->createNamedParameter($batch, \OCP\DB\QueryBuilder\IQueryBuilder::PARAM_INT_ARRAY)))->executeQuery();
            try {
                foreach ($r->fetchAllAssociative() as $row) {
                    $revision = (string)$row['revision'];
                    if (!preg_match('/\A(?:0|[1-9][0-9]*)\z/D', $revision) || strlen($revision) > strlen((string)PHP_INT_MAX) ||
                        (strlen($revision) === strlen((string)PHP_INT_MAX) && strcmp($revision, (string)PHP_INT_MAX) > 0) ||
                        !in_array($row['writing'], [true, false, 0, 1, '0', '1'], true)) {
                        throw new \UnexpectedValueException('Invalid source revision ledger');
                    }
                    $out[(int)$row['file_id']] = ['revision' => $revision, 'writing' => (bool)$row['writing']];
                }
            } finally { $r->closeCursor(); }
        }

        if ($protect) {
            $closed = $this->recovery->closed();
            $floors=$this->recovery->floors(array_keys($out));
            foreach ($out as $id=>&$state) { $floor=$floors[$id];
                if ($closed || (int)$state['revision'] < $floor['revision'] || ((int)$state['revision'] === $floor['revision'] && $floor['writing'])) { $state['writing']=true; }
            }
            unset($state);
        }
        return $out;
    }

    /** Private maintenance verifier keeps content floors while avoiding the global probe deadlock. */
    public function verifyRecoveryVersions(array $ids): void {
        $raw=$this->snapshots($ids,false);$floors=$this->recovery->floors($ids);
        foreach($raw as $id=>$state){$floor=$floors[$id];
            if($state['writing']||(int)$state['revision']<$floor['revision']||((int)$state['revision']===$floor['revision']&&$floor['writing'])){throw new \DomainException('Current source bytes have no complete version proof');}
        }
    }

    /** Before-write failure must propagate: a storage write cannot bypass the durable fence. */
    public function begin(int $fileId, array $bindingIds): void { $this->record($fileId, $bindingIds, true); }
    public function complete(int $fileId, array $bindingIds): void { $this->record($fileId, $bindingIds, false); }

    private function record(int $fileId, array $bindingIds, bool $before): void {
        if ($fileId < 1) { throw new \InvalidArgumentException('Invalid source file ID'); }
        $this->db->beginTransaction();
        try {
            // Same lock as state ingestion: an old push cannot commit across the write fence.
            $this->db->insertIgnoreConflict('weknora_bind_lock', ['id' => 1]);
            $q = $this->db->getQueryBuilder();
            $r = $q->select('id')->from('weknora_bind_lock')->where($q->expr()->eq('id', $q->createNamedParameter(1)))->forUpdate()->executeQuery();
            try { if ($r->fetchOne() === false) { throw new \RuntimeException('Revision lock unavailable'); } }
            finally { $r->closeCursor(); }
            $old = $this->snapshots([$fileId], false)[$fileId];
            $floor=$this->recovery->floor($fileId);
            if (!$before && ((int)$old['revision'] < $floor['revision'] ||
                ((int)$old['revision'] === $floor['revision'] && $floor['writing']))) {
                throw new \DomainException('Restored content cannot complete an external write');
            }
            $revision = max((int)$old['revision'], $floor['revision']);
            if ($before || !$old['writing']) {
                if ($revision === PHP_INT_MAX) { throw new \OverflowException('Source revision exhausted'); }
                $revision++;
            }
            $values = ['file_id' => $fileId, 'revision' => $revision, 'writing' => $before ? 1 : 0, 'updated_at' => time()];
            $this->db->insertIgnoreConflict('weknora_source_file_rev', $values);
            $u = $this->db->getQueryBuilder();
            $u->update('weknora_source_file_rev')->set('revision', $u->createNamedParameter($revision))
                ->set('writing', $u->createNamedParameter($before ? 1 : 0))->set('updated_at', $u->createNamedParameter(time()))
                ->where($u->expr()->eq('file_id', $u->createNamedParameter($fileId)))->executeStatement();
            foreach (array_unique($bindingIds) as $bindingId) {
                $d = $this->db->getQueryBuilder();
                $d->delete('weknora_manifest_snap')->where($d->expr()->eq('binding_id', $d->createNamedParameter($bindingId)))->executeStatement();
                // Metadata journal entries close recovery; they never grant visibility.
                PublicationRecoveryLedger::appendInTransaction($this->db, $bindingId, $fileId, $before ? 'content_write_begin' : 'content_write_complete', $revision);
                StatusPollQueue::enqueueInTransaction($this->db, $bindingId, $fileId);
            }
            $this->db->commit();
        } catch (\Throwable $e) { $this->db->rollBack(); throw $e; }
    }
}
