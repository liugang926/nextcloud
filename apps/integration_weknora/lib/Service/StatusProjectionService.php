<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\Files\File;
use OCP\IConfig;
use OCP\IDBConnection;

/** Durable, ordered inbox and current-version projection in plugin tables only. */
final class StatusProjectionService {
    public function __construct(private IDBConnection $db, private IConfig $config,
        private BindingRegistryService $bindings, private SourcePairingRegistryService $pairings,
        private EventConnectionService $connections, private FilePublicationStateService $publication,
        private SourceContentRevisionService $contentRevisions) {
    }

    /** Caller verifies purpose-specific signature first. Snapshot time is authenticated round-trip time. */
    public function accept(string $raw, ?int $snapshotVerifiedAt = null, ?array $authenticatedConnection = null): array {
        $now = time();
        $s = StatusProtocol::decode($raw, $now);
        if ($snapshotVerifiedAt !== null && ($snapshotVerifiedAt > $now + 5 || $snapshotVerifiedAt < $now - 10)) {
            throw new \InvalidArgumentException('Snapshot verification expired');
        }
        $this->db->beginTransaction();
        try {
            $this->lock();
            $this->requireScope($s, $authenticatedConnection);
            $existingEvent = $this->event($s['connection_id'], $s['event_id']);
            $rawHash = hash('sha256', $raw);
            if ($existingEvent !== null && !hash_equals($existingEvent['body_sha256'], $rawHash)) {
                throw new \DomainException('Status event identity reused');
            }
            $old = $this->row($s['binding_id'], (int)$s['file_id']);
            $hash = StatusProtocol::businessHash($s);
            $decision = self::decision($old, $s, $hash);
            if ($decision === 'conflict') {
                throw new \DomainException('Status head conflicts with persisted revision');
            }
            if ($existingEvent === null) {
                $this->db->insertIgnoreConflict('weknora_status_inbox', [
                    'connection_id' => $s['connection_id'], 'event_id' => $s['event_id'],
                    'body_sha256' => $rawHash, 'received_at' => $now]);
            }
            if ($decision !== 'stale') {
                // A delayed push's receipt time never renews freshness. Only
                // its original verification or a fresh signed current-head round trip does.
                $verified = $snapshotVerifiedAt ?? $s['last_verified_at'];
                if ($decision === 'same' && $old !== null) {
                    $verified = max((int)($old['last_verified_at'] ?? 0), (int)($verified ?? 0)) ?: null;
                }
                $values = ['binding_id' => $s['binding_id'], 'file_id' => (int)$s['file_id'],
                    'publication_epoch' => (int)$s['publication_epoch'], 'pair_operation_id' => $s['pair_operation_id'],
                    'source_etag' => $s['source_etag'], 'head_revision' => (int)$s['head_revision'],
                    'status_revision' => (int)$s['status_revision'], 'state_sha256' => $hash,
                    'body_json' => json_encode($s, JSON_THROW_ON_ERROR), 'received_at' => $now,
                    'last_verified_at' => $verified,
                    'fresh_until' => $verified === null ? null : $verified + StatusProtocol::MAX_AGE];
                if ($old === null) {
                    $this->db->insertIgnoreConflict('weknora_file_status', $values);
                } else {
                    $q = $this->db->getQueryBuilder();
                    $q->update('weknora_file_status');
                    foreach ($values as $name => $value) {
                        if (!in_array($name, ['binding_id', 'file_id'], true)) {
                            $q->set($name, $q->createNamedParameter($value));
                        }
                    }
                    $q->where($q->expr()->eq('binding_id', $q->createNamedParameter($s['binding_id'])))
                        ->andWhere($q->expr()->eq('file_id', $q->createNamedParameter((int)$s['file_id'])))
                        ->executeStatement();
                }
            }
            $this->requireScope($s, $authenticatedConnection);
            $this->db->commit();
            return ['event_id' => $s['event_id'], 'status' => 'accepted', 'persisted' => true,
                'applied' => $decision === 'new', 'duplicate' => $existingEvent !== null,
                'status_revision' => $s['status_revision']];
        } catch (\Throwable $e) {
            $this->db->rollBack();
            throw $e;
        }
    }

    /** Pure ordering rule shared by push/snapshot; head changes never reset status revision. */
    public static function decision(?array $old, array $state, string $hash): string {
        if ($old === null) {
            return 'new';
        }
        $epoch = (int)$state['publication_epoch'];
        $previousEpoch = (int)$old['publication_epoch'];
        if ($epoch < $previousEpoch) {
            return 'stale';
        }
        if ($epoch > $previousEpoch) {
            return 'new';
        }
        if ($old['pair_operation_id'] !== $state['pair_operation_id']) {
            return 'conflict';
        }
        $revision = (int)$state['status_revision'];
        $head = (int)$state['head_revision'];
        if ($revision < (int)$old['status_revision'] || $head < (int)$old['head_revision']) {
            return 'stale';
        }
        if ($revision === (int)$old['status_revision']) {
            $sameHash = hash_equals($old['state_sha256'], $hash);
            // A pre-content-revision schema-2 row used the same contract with
            // the default-zero field absent. Normalize only an intact legacy
            // row; this never admits a changed content generation or outcome.
            if (!$sameHash && ($state['source_content_revision'] ?? null) === '0' && isset($old['body_json'])) {
                try {
                    $legacy = json_decode($old['body_json'], true, 16, JSON_THROW_ON_ERROR);
                    if (!array_key_exists('source_content_revision', $legacy) &&
                        hash_equals($old['state_sha256'], StatusProtocol::businessHash($legacy))) {
                        $normalized = StatusProtocol::decode($old['body_json'], time());
                        $sameHash = hash_equals(StatusProtocol::businessHash($normalized), $hash);
                    }
                } catch (\Throwable $e) { $sameHash = false; }
            }
            return $head === (int)$old['head_revision'] && $sameHash
                ? 'same' : 'conflict';
        }
        if ($head === (int)$old['head_revision'] && $old['source_etag'] !== $state['source_etag']) {
            return 'conflict';
        }
        if ($head === (int)$old['head_revision'] && isset($old['body_json'])) {
            $previous = json_decode($old['body_json'], true, 16, JSON_THROW_ON_ERROR);
            foreach (['knowledge_id', 'build_attempt', 'processing_config_revision', 'source_content_revision'] as $field) {
                if (($previous[$field] ?? ($field === 'source_content_revision' ? '0' : null)) !== $state[$field]) { return 'conflict'; }
            }
        }
        return 'new';
    }

    /** Caller must check the actual user's source READ both before and after this lookup. */
    public function forFile(string $bindingId, int $fileId, string $sourceEtag, int $publicationEpoch): ?array {
        $row = $this->row($bindingId, $fileId);
        if ($row === null || $row['source_etag'] !== $sourceEtag ||
            (int)$row['publication_epoch'] !== $publicationEpoch) {
            return null;
        }
        $s = StatusProtocol::decode((string)$row['body_json'], time());
        $this->requireScope($s);
        $result = StatusProtocol::projection($s,
            $row['last_verified_at'] === null ? null : (int)$row['last_verified_at'], time());
        $result['received_at'] = (int)$row['received_at'];
        return $result;
    }

    /** Bounded compensation batch; failed requests are attempted again after one minute. */
    public function due(int $now, int $limit = 100): array {
        $q = $this->db->getQueryBuilder();
        $r = $q->select('binding_id', 'file_id', 'source_etag')->from('weknora_file_status')
            ->where($q->expr()->lt('last_poll_at', $q->createNamedParameter($now - 60)))
            ->orderBy('last_poll_at')->setMaxResults($limit)->executeQuery();
        try {
            return $r->fetchAllAssociative();
        } finally {
            $r->closeCursor();
        }
    }

    public function markPolled(string $bindingId, int $fileId, int $now): void {
        $q = $this->db->getQueryBuilder();
        $q->update('weknora_file_status')->set('last_poll_at', $q->createNamedParameter($now))
            ->where($q->expr()->eq('binding_id', $q->createNamedParameter($bindingId)))
            ->andWhere($q->expr()->eq('file_id', $q->createNamedParameter($fileId)))->executeStatement();
    }

    public function currentEtag(string $bindingId, int $fileId): ?string {
        try {
            $this->contentRevisions->current($fileId);
            $this->bindings->requirePublicationActive($bindingId);
            if ($this->publication->getState($bindingId, $fileId) !== 'eligible') { return null; }
            $root = $this->bindings->requireActiveRoot($bindingId);
            foreach ($root->getById($fileId) as $file) {
                if ($file instanceof File && $root->isSubNode($file) && $file->isReadable()) {
                    $this->bindings->assertNodeInSupportedMount($bindingId, $root, $file);
                    return $file->getEtag();
                }
            }
        } catch (\Throwable $e) { return null; }
        return null;
    }

    private function requireScope(array $s, ?array $authenticatedConnection = null): void {
        if ($this->config->getSystemValueBool('maintenance', false)) {
            throw new \DomainException('Recovery maintenance is closed');
        }
        $pair = $this->pairings->status($s['binding_id']);
        $connection = $this->connections->row($s['binding_id']);
        if ($authenticatedConnection !== null) {
            // Key rotation takes the same registry lock. Authentication outside
            // the transaction cannot authorize a write with the retired key.
            foreach (['connection_id', 'key_id', 'secret_ciphertext'] as $field) {
                if ($connection === null || !is_string($authenticatedConnection[$field] ?? null) ||
                    !hash_equals((string)$connection[$field], $authenticatedConnection[$field])) {
                    throw new \DomainException('Status authentication generation changed');
                }
            }
        }
        if ($pair === null || $pair['state'] !== 'active' || $connection === null ||
            $connection['status'] !== 'active' || $connection['connection_id'] !== $s['connection_id'] ||
            $s['instance_id'] !== $this->config->getSystemValueString('instanceid') ||
            $pair['operation_id'] !== $s['pair_operation_id'] ||
            (string)$pair['tenant_id'] !== $s['tenant_id'] ||
            $pair['knowledge_base_id'] !== $s['knowledge_base_id'] ||
            $pair['data_source_id'] !== $s['data_source_id'] ||
            $this->bindings->requirePublicationActive($s['binding_id']) !== (int)$s['publication_epoch'] ||
            $this->publication->getState($s['binding_id'], (int)$s['file_id']) !== 'eligible') {
            throw new \DomainException('Status source unavailable');
        }
        if ($this->contentRevisions->current((int)$s['file_id']) !== $s['source_content_revision']) {
            throw new \DomainException('Status source content revision unavailable');
        }
        $root = $this->bindings->requireActiveRoot($s['binding_id']);
        foreach ($root->getById((int)$s['file_id']) as $file) {
            if ($file instanceof File && $root->isSubNode($file) &&
                $file->getId() === (int)$s['file_id'] && $file->getEtag() === $s['source_etag']) {
                $this->bindings->assertNodeInSupportedMount($s['binding_id'], $root, $file);
                return;
            }
        }
        throw new \DomainException('Status source version unavailable');
    }

    private function row(string $bindingId, int $fileId): ?array {
        $q = $this->db->getQueryBuilder();
        $r = $q->select('*')->from('weknora_file_status')
            ->where($q->expr()->eq('binding_id', $q->createNamedParameter($bindingId)))
            ->andWhere($q->expr()->eq('file_id', $q->createNamedParameter($fileId)))->executeQuery();
        try { $row = $r->fetchAssociative(); return $row === false ? null : $row; }
        finally { $r->closeCursor(); }
    }

    private function event(string $connectionId, string $eventId): ?array {
        $q = $this->db->getQueryBuilder();
        $r = $q->select('body_sha256')->from('weknora_status_inbox')
            ->where($q->expr()->eq('connection_id', $q->createNamedParameter($connectionId)))
            ->andWhere($q->expr()->eq('event_id', $q->createNamedParameter($eventId)))->executeQuery();
        try { $row = $r->fetchAssociative(); return $row === false ? null : $row; }
        finally { $r->closeCursor(); }
    }

    private function lock(): void {
        $this->db->insertIgnoreConflict('weknora_bind_lock', ['id' => 1]);
        $q = $this->db->getQueryBuilder();
        $r = $q->select('id')->from('weknora_bind_lock')
            ->where($q->expr()->eq('id', $q->createNamedParameter(1)))->forUpdate()->executeQuery();
        try { if ($r->fetchOne() === false) { throw new \RuntimeException('Registry lock missing'); } }
        finally { $r->closeCursor(); }
    }
}
