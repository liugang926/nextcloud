<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IConfig;
use OCP\IDBConnection;

/** Metadata only. An external collector must retain and authenticate pages. */
final class PublicationRecoveryLedger {
    public const PAGE_SIZE = 200;
    public const ZERO_HASH = "0000000000000000000000000000000000000000000000000000000000000000";
    public const KINDS = ['upsert', 'metadata', 'delete', 'subtree_scan', 'subtree_moved',
        'subtree_deleted', 'reconcile', 'withdrawn', 'eligible', 'binding_stop',
        'binding_resume', 'binding_retired', 'binding_created'];

    public function __construct(private IDBConnection $db, private IConfig $config) {
    }

    /** Caller holds its business transaction; sequence allocation shares that commit. */
    public static function appendInTransaction(IDBConnection $db, string $binding, ?int $file, string $kind, int $revision): void {
        if (!$db->inTransaction() ||
            !preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $binding) ||
            ($file !== null && $file < 1) || !in_array($kind, self::KINDS, true) || $revision < 0) {
            throw new \InvalidArgumentException('Invalid recovery journal transaction');
        }
        $head = self::lockHead($db);
        if ($head['sequence'] === PHP_INT_MAX) {
            throw new \UnexpectedValueException('Recovery sequence exhausted');
        }
        $next = $head['sequence'] + 1;
        $time = time();
        $event = ['sequence' => (string)$next, 'binding_id' => $binding,
            'file_id' => $file === null ? null : (string)$file, 'kind' => $kind,
            'source_revision' => (string)$revision, 'created_at' => $time];
        $chain = self::eventHash($head['chain_sha256'], $event);
        $query = $db->getQueryBuilder();
        $query->insert('weknora_recovery_log')->values([
            'sequence' => $query->createNamedParameter($next),
            'binding_id' => $query->createNamedParameter($binding),
            'file_id' => $query->createNamedParameter($file),
            'kind' => $query->createNamedParameter($kind),
            'source_revision' => $query->createNamedParameter($revision),
            'created_at' => $query->createNamedParameter($time),
            'chain_sha256' => $query->createNamedParameter($chain),
        ])->executeStatement();
        $update = $db->getQueryBuilder();
        if ($update->update('weknora_recovery_head')
            ->set('sequence', $update->createNamedParameter($next))
            ->set('chain_sha256', $update->createNamedParameter($chain))
            ->where($update->expr()->eq('id', $update->createNamedParameter(1)))
            ->andWhere($update->expr()->eq('sequence', $update->createNamedParameter($head['sequence'])))
            ->executeStatement() !== 1) {
            throw new \UnexpectedValueException('Recovery head changed');
        }
    }

    /** @return array<string, mixed> */
    public function page(int $after): array {
        if ($after < 0) {
            throw new \InvalidArgumentException('Invalid recovery cursor');
        }
        $instance = $this->config->getSystemValueString('instanceid');
        if ($instance === '' || strlen($instance) > 64) {
            throw new \UnexpectedValueException('Instance identity is unavailable');
        }
        $this->db->beginTransaction();
        try {
            $head = self::lockHead($this->db);
            if ($after > $head['sequence']) {
                throw new \DomainException('Recovery journal rolled back or wrong cursor');
            }
            $afterHash = self::ZERO_HASH;
            if ($after > 0) {
                $cursor = $this->db->getQueryBuilder();
                $cursorResult = $cursor->select('chain_sha256')->from('weknora_recovery_log')
                    ->where($cursor->expr()->eq('sequence', $cursor->createNamedParameter($after)))->executeQuery();
                try { $afterHash = $cursorResult->fetchOne(); } finally { $cursorResult->closeCursor(); }
                if (!is_string($afterHash) || !preg_match('/\A[a-f0-9]{64}\z/D', $afterHash)) {
                    throw new \UnexpectedValueException('Recovery cursor prefix is missing');
                }
            }
            $q = $this->db->getQueryBuilder();
            $result = $q->select('sequence', 'binding_id', 'file_id', 'kind', 'source_revision', 'created_at', 'chain_sha256')
                ->from('weknora_recovery_log')
                ->where($q->expr()->gt('sequence', $q->createNamedParameter($after)))
                ->orderBy('sequence', 'ASC')->setMaxResults(self::PAGE_SIZE)->executeQuery();
            try {
                $rows = $result->fetchAllAssociative();
            } finally {
                $result->closeCursor();
            }
            $next = $after;
            $nextHash = $afterHash;
            $items = [];
            foreach ($rows as $row) {
                if ((int)$row['sequence'] !== $next + 1 ||
                    !preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', (string)$row['binding_id']) ||
                    ($row['file_id'] !== null && (int)$row['file_id'] < 1) ||
                    (int)$row['source_revision'] < 0 || (int)$row['created_at'] < 0 ||
                    !in_array($row['kind'], self::KINDS, true)) {
                    throw new \UnexpectedValueException('Recovery journal continuity is broken');
                }
                $next++;
                $item = ['sequence' => (string)$next, 'binding_id' => (string)$row['binding_id'],
                    'file_id' => $row['file_id'] === null ? null : (string)$row['file_id'],
                    'kind' => (string)$row['kind'], 'source_revision' => (string)$row['source_revision'],
                    'created_at' => (int)$row['created_at']];
                $nextHash = self::eventHash($nextHash, $item);
                if (!hash_equals($nextHash, (string)$row['chain_sha256'])) {
                    throw new \UnexpectedValueException('Recovery event chain mismatch');
                }
                $items[] = $item;
            }
            if ($next < $head['sequence'] && count($items) < self::PAGE_SIZE) {
                throw new \UnexpectedValueException('Recovery journal tail is missing');
            }
            if ($next === $head['sequence'] && !hash_equals($nextHash, $head['chain_sha256'])) {
                throw new \UnexpectedValueException('Recovery head hash mismatch');
            }
            $this->db->commit();
            return ['schema_version' => 1, 'instance_id' => $instance, 'stream_id' => $head['stream_id'],
                'after_sequence' => (string)$after, 'next_sequence' => (string)$next,
                'head_sequence' => (string)$head['sequence'], 'after_chain_sha256' => $afterHash,
                'next_chain_sha256' => $nextHash, 'head_chain_sha256' => $head['chain_sha256'], 'items' => $items,
                'has_more' => $next < $head['sequence']];
        } catch (\Throwable $error) {
            $this->db->rollBack();
            throw $error;
        }
    }

    public static function eventHash(string $previous, array $item): string {
        if (!preg_match('/\A[a-f0-9]{64}\z/D', $previous)) {
            throw new \UnexpectedValueException('Invalid recovery prefix hash');
        }
        // Explicit ordered scalars avoid JSON encoder/key-order differences.
        $values = [$previous, $item['sequence'], $item['binding_id'], $item['file_id'] ?? '-',
            $item['kind'], $item['source_revision'], (string)$item['created_at']];
        return hash('sha256', implode("\n", $values));
    }

    /** @return array{stream_id:string,sequence:int} */
    private static function lockHead(IDBConnection $db): array {
        $bytes = random_bytes(16);
        $bytes[6] = chr((ord($bytes[6]) & 0x0f) | 0x40);
        $bytes[8] = chr((ord($bytes[8]) & 0x3f) | 0x80);
        $hex = bin2hex($bytes);
        $uuid = substr($hex, 0, 8) . '-' . substr($hex, 8, 4) . '-' . substr($hex, 12, 4) . '-' . substr($hex, 16, 4) . '-' . substr($hex, 20);
        $db->insertIgnoreConflict('weknora_recovery_head', ['id' => 1, 'stream_id' => $uuid, 'sequence' => 0, 'chain_sha256' => self::ZERO_HASH]);
        $q = $db->getQueryBuilder();
        $result = $q->select('stream_id', 'sequence', 'chain_sha256')->from('weknora_recovery_head')
            ->where($q->expr()->eq('id', $q->createNamedParameter(1)))->forUpdate()->executeQuery();
        try {
            $row = $result->fetchAssociative();
        } finally {
            $result->closeCursor();
        }
        if ($row === false || !preg_match('/\A[a-f0-9-]{36}\z/D', (string)$row['stream_id']) || (int)$row['sequence'] < 0) {
            throw new \UnexpectedValueException('Recovery head is missing or invalid');
        }
        return ['stream_id' => (string)$row['stream_id'], 'sequence' => (int)$row['sequence'], 'chain_sha256' => (string)$row['chain_sha256']];
    }
}
