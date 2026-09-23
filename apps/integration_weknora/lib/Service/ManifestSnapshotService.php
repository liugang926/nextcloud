<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IDBConnection;

/** Short-lived immutable lists; cursors never rewalk a large source tree. */
final class ManifestSnapshotService {
    private const TTL_SECONDS = 600;
    private const MAX_JSON_BYTES = 33554432;

    public function __construct(private IDBConnection $db) {
    }

    public function publicationRevision(string $bindingId): int {
        $query = $this->db->getQueryBuilder();
        $query->select('id')->from('weknora_pub_audit')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->orderBy('id', 'DESC')->setMaxResults(1);
        $result = $query->executeQuery();
        try {
            $value = $result->fetchOne();
            return $value === false ? 0 : (int)$value;
        } finally {
            $result->closeCursor();
        }
    }

    /** @param list<array<string, mixed>> $items */
    public function save(string $bindingId, string $generation, string $rootEtag, int $publicationRevision, array $items): string {
        $json = json_encode($items, JSON_THROW_ON_ERROR);
        if (strlen($json) > self::MAX_JSON_BYTES) {
            throw new \LengthException('Manifest exceeds snapshot limit');
        }
        $this->deleteExpired();
        $id = bin2hex(random_bytes(24));
        $query = $this->db->getQueryBuilder();
        $query->insert('weknora_manifest_snap')->values([
            'snapshot_id' => $query->createNamedParameter($id),
            'binding_id' => $query->createNamedParameter($bindingId),
            'generation' => $query->createNamedParameter($generation),
            'root_etag' => $query->createNamedParameter($rootEtag),
            'publication_revision' => $query->createNamedParameter($publicationRevision),
            'items_json' => $query->createNamedParameter($json),
            'created_at' => $query->createNamedParameter(time()),
        ]);
        $query->executeStatement();
        return $id;
    }

    /** @return array{generation: string, root_etag: string, publication_revision: int, items: list<array<string, mixed>>}|null */
    public function load(string $bindingId, string $id): ?array {
        if (!preg_match('/\A[a-f0-9]{48}\z/D', $id)) {
            return null;
        }
        $query = $this->db->getQueryBuilder();
        $query->select('generation', 'root_etag', 'publication_revision', 'items_json', 'created_at')
            ->from('weknora_manifest_snap')
            ->where($query->expr()->eq('snapshot_id', $query->createNamedParameter($id)))
            ->andWhere($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)));
        $result = $query->executeQuery();
        try {
            $row = $result->fetchAssociative();
        } finally {
            $result->closeCursor();
        }
        if ($row === false || (int)$row['created_at'] < time() - self::TTL_SECONDS) {
            return null;
        }
        $items = json_decode((string)$row['items_json'], true, 512, JSON_THROW_ON_ERROR);
        if (!is_array($items) || !array_is_list($items)) {
            throw new \UnexpectedValueException('Invalid stored manifest');
        }
        return [
            'generation' => (string)$row['generation'],
            'root_etag' => (string)$row['root_etag'],
            'publication_revision' => (int)$row['publication_revision'],
            'items' => $items,
        ];
    }

    private function deleteExpired(): void {
        $query = $this->db->getQueryBuilder();
        $query->delete('weknora_manifest_snap')
            ->where($query->expr()->lt('created_at', $query->createNamedParameter(time() - self::TTL_SECONDS)));
        $query->executeStatement();
    }
}
