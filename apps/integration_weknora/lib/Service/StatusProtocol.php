<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

/** Versioned metadata only. Neither a signature nor a successful stage grants access. */
final class StatusProtocol {
    public const MAX_BYTES = 32768;
    public const PATH = '/api/v1/integrations/nextcloud/files/status/v2';
    public const MAX_AGE = 300;
    public const GRAPH_STATES = ['not_enabled', 'unsupported', 'skipped', 'blocked',
        'pending', 'running', 'succeeded', 'succeeded_empty', 'partial_failed', 'failed',
        'cancelled', 'superseded', 'unverified'];
    public const ERROR_CODES = ['no_retrievable_content', 'source_changed', 'source_unavailable',
        'publication_stopped', 'publication_recovery_closed', 'identity_unmapped', 'permission_unverified', 'template_unavailable',
        'unsupported_backend', 'graph_backend_unsupported', 'graph_enqueue_failed',
        'graph_extract_failed', 'graph_persist_failed', 'graph_write_unverified',
        'graph_fence_closed', 'parse_failed', 'chunk_failed', 'embedding_failed', 'index_failed',
        'required_graph_failed', 'document_processing_failed', 'coverage_missing', 'lease_expired', 'cancelled', 'superseded',
        'graph_task_manifest_pending', 'graph_persistence_unverified', 'content_generation_closed',
        'graph_input_unverified', 'graph_extraction_failed', 'graph_persistence_failed',
        'graph_no_applicable_input', 'graph_task_manifest_unverified', 'graph_configuration_changed'];

    public static function key(string $secret): string {
        return hash_hmac('sha256', 'weknora-nextcloud-state-key-v2', $secret, true);
    }

    public static function request(string $query, string $timestamp, string $nonce,
        string $connectionId, string $keyId): string {
        return implode("\n", ['nextcloud-file-status-hmac-sha256-v2', 'GET', self::PATH,
            $query, hash('sha256', ''), $timestamp, $nonce, $connectionId, $keyId]);
    }

    public static function response(string $requestSignature, string $body,
        string $connectionId, string $keyId, string $nonce): string {
        return implode("\n", ['weknora-file-status-hmac-sha256-v2', $requestSignature,
            hash('sha256', $body), $connectionId, $keyId, $nonce]);
    }

    public static function push(string $path, string $body, string $timestamp,
        string $nonce, string $connectionId, string $keyId): string {
        return implode("\n", ['weknora-state-push-hmac-sha256-v2', 'POST', $path, '',
            hash('sha256', $body), $timestamp, $nonce, $connectionId, $keyId]);
    }

    public static function decimal(mixed $value, bool $zero = false): bool {
        if (!is_string($value) || !preg_match($zero ? '/\A(?:0|[1-9][0-9]*)\z/D' : '/\A[1-9][0-9]*\z/D', $value)) {
            return false;
        }
        $max = (string)PHP_INT_MAX;
        return strlen($value) < strlen($max) ||
            (strlen($value) === strlen($max) && strcmp($value, $max) <= 0);
    }

    private static function id(mixed $value, int $max = 128): bool {
        return is_string($value) && strlen($value) <= $max &&
            (bool)preg_match('/\A[A-Za-z0-9._:-]+\z/D', $value);
    }

    private static function timestamp(mixed $value, int $now): bool {
        return $value === null || (is_int($value) && $value > 0 && $value <= $now + 5);
    }

    public static function error(mixed $code): ?string {
        return is_string($code) && in_array($code, self::ERROR_CODES, true) ? $code : null;
    }

    /** Strict shape validation; arbitrary remote diagnostic strings are discarded. */
    public static function decode(string $raw, int $now): array {
        if (strlen($raw) > self::MAX_BYTES) {
            throw new \InvalidArgumentException('Status exceeds limit');
        }
        $s = json_decode($raw, true, 16, JSON_THROW_ON_ERROR);
        if (!is_array($s) || ($s['schema_version'] ?? null) !== 2 || ($s['qa_available'] ?? null) !== false) {
            throw new \InvalidArgumentException('Invalid status schema');
        }
        $out = ['schema_version' => 2, 'qa_available' => false];
        $contentRevision = $s['source_content_revision'] ?? '0';
        if (!self::decimal($contentRevision, true)) { throw new \InvalidArgumentException('Invalid content revision'); }
        $out['source_content_revision'] = $contentRevision;
        foreach (['event_id', 'connection_id', 'instance_id', 'binding_id', 'pair_operation_id',
            'tenant_id', 'knowledge_base_id', 'data_source_id', 'source_etag'] as $name) {
            if (!self::id($s[$name] ?? null, $name === 'source_etag' ? 256 : 128)) {
                throw new \InvalidArgumentException('Invalid status scope');
            }
            $out[$name] = $s[$name];
        }
        if (!SourcePairingRegistryService::validOperationId($out['pair_operation_id'])) {
            throw new \InvalidArgumentException('Invalid pairing operation');
        }
        foreach (['file_id', 'publication_epoch', 'head_revision', 'status_revision'] as $name) {
            if (!self::decimal($s[$name] ?? null, $name === 'publication_epoch')) {
                throw new \InvalidArgumentException('Invalid status revision');
            }
            $out[$name] = $s[$name];
        }
        foreach (['build_attempt', 'processing_config_revision'] as $name) {
            $value = $s[$name] ?? null;
            if ($value !== null && !self::decimal($value, true)) {
                throw new \InvalidArgumentException('Invalid build identity');
            }
            $out[$name] = $value;
        }
        $out['knowledge_id'] = $s['knowledge_id'] ?? null;
        if ($out['knowledge_id'] !== null && !self::id($out['knowledge_id'])) {
            throw new \InvalidArgumentException('Invalid candidate');
        }
        if (!in_array($s['sync_state'] ?? null, ['discovered', 'downloading', 'ingested', 'failed', 'withdrawn'], true)) {
            throw new \InvalidArgumentException('Invalid sync state');
        }
        $out['sync_state'] = $s['sync_state'];
        foreach (['parse', 'chunk', 'embedding', 'index'] as $stage) {
            $item = $s['stages'][$stage] ?? null;
            if (!is_array($item) || !in_array($item['state'] ?? null,
                ['pending', 'running', 'succeeded', 'failed', 'skipped', 'blocked', 'cancelled', 'superseded', 'unverified'], true) ||
                !self::timestamp($item['started_at'] ?? null, $now) ||
                !self::timestamp($item['completed_at'] ?? null, $now) ||
                ($item['state'] === 'succeeded' && ($item['completed_at'] ?? null) === null) ||
                (($item['started_at'] ?? null) !== null && ($item['completed_at'] ?? null) !== null &&
                    $item['started_at'] > $item['completed_at'])) {
                throw new \InvalidArgumentException('Invalid document stage');
            }
            $out['stages'][$stage] = ['state' => $item['state'],
                'started_at' => $item['started_at'] ?? null, 'completed_at' => $item['completed_at'] ?? null];
        }
        $graph = $s['graph'] ?? null;
        if (!is_array($graph) || !in_array($graph['state'] ?? null, self::GRAPH_STATES, true)) {
            throw new \InvalidArgumentException('Invalid graph result');
        }
        foreach (['enabled', 'required', 'persisted'] as $name) {
            if (!is_bool($graph[$name] ?? null)) {
                throw new \InvalidArgumentException('Invalid graph mode');
            }
            $out['graph'][$name] = $graph[$name];
        }
        $sum = 0;
        foreach (['expected', 'succeeded', 'failed', 'skipped', 'cancelled', 'pending'] as $name) {
            if (!is_int($graph[$name] ?? null) || $graph[$name] < 0 || $graph[$name] > 1000000000) {
                throw new \InvalidArgumentException('Invalid graph count');
            }
            $out['graph'][$name] = $graph[$name];
            if ($name !== 'expected') {
                $sum += $graph[$name];
            }
        }
        if ($sum !== $graph['expected'] || ($graph['required'] && !$graph['enabled']) ||
            (!$graph['enabled'] && !in_array($graph['state'], ['not_enabled', 'unverified', 'cancelled', 'superseded', 'blocked'], true)) ||
            (in_array($graph['state'], ['succeeded', 'succeeded_empty'], true) &&
                (!$graph['enabled'] || !$graph['persisted'] || $graph['failed'] > 0 || $graph['pending'] > 0 ||
                    $graph['expected'] < 1 || $graph['succeeded'] !== $graph['expected'] || ($graph['completed_at'] ?? null) === null)) ||
            !self::timestamp($graph['completed_at'] ?? null, $now)) {
            throw new \InvalidArgumentException('Graph result lacks completion proof');
        }
        $out['graph']['state'] = $graph['state'];
        $out['graph']['completed_at'] = $graph['completed_at'] ?? null;
        $out['graph']['error_code'] = self::error($graph['error_code'] ?? null);
        $pub = $s['publication_state'] ?? null;
        if (!in_array($pub, ['building', 'ready', 'published', 'failed', 'withdrawn', 'retired',
            'blocked', 'superseded', 'unverified', 'pending'], true)) {
            throw new \InvalidArgumentException('Invalid publication state');
        }
        $out['publication_state'] = $pub;
        $out['published_source_etag'] = $s['published_source_etag'] ?? null;
        if ($out['published_source_etag'] !== null && !self::id($out['published_source_etag'], 256)) {
            throw new \InvalidArgumentException('Invalid published version');
        }
        if ($pub === 'published' && ($out['published_source_etag'] !== $out['source_etag'] ||
            $out['knowledge_id'] === null || $out['build_attempt'] === null ||
            (int)$out['build_attempt'] < 1 || (int)$out['processing_config_revision'] < 1 ||
            $out['sync_state'] !== 'ingested' ||
            $out['stages']['parse']['state'] !== 'succeeded' ||
            $out['stages']['chunk']['state'] !== 'succeeded' ||
            !in_array($out['stages']['embedding']['state'], ['succeeded', 'skipped'], true) ||
            $out['stages']['index']['state'] !== 'succeeded' || $out['stages']['index']['completed_at'] === null ||
            ($graph['required'] && !in_array($graph['state'], ['succeeded', 'succeeded_empty'], true)))) {
            throw new \InvalidArgumentException('Incomplete publication proof');
        }
        foreach (['occurred_at', 'last_verified_at', 'last_permission_sync_at'] as $name) {
            if (!self::timestamp($s[$name] ?? null, $now)) {
                throw new \InvalidArgumentException('Invalid result time');
            }
            $out[$name] = $s[$name] ?? null;
        }
        if ($out['occurred_at'] === null) {
            throw new \InvalidArgumentException('Missing event time');
        }
        $out['permission_revision'] = $s['permission_revision'] ?? null;
        if ($out['permission_revision'] !== null && !self::id($out['permission_revision'], 256)) {
            throw new \InvalidArgumentException('Invalid permission revision');
        }
        foreach (['withdrawal_state', 'permission_reclaim_state', 'gc_state'] as $name) {
            $value = $s[$name] ?? 'unverified';
            if (!is_string($value) || !in_array($value, ['none', 'pending', 'blocked', 'partial', 'verified',
                'withdrawn', 'closed', 'access_closed', 'completed', 'unverified', 'cleanup_pending', 'cleanup_blocked',
                'cleanup_partial', 'cleanup_verified'], true)) {
                throw new \InvalidArgumentException('Invalid cleanup metadata');
            }
            $out[$name] = $value;
        }
        $out['error_code'] = self::error($s['error_code'] ?? null);
        return $out;
    }

    public static function businessHash(array $state): string {
        unset($state['event_id'], $state['occurred_at'], $state['last_verified_at']);
        return hash('sha256', json_encode($state, JSON_THROW_ON_ERROR));
    }

    public static function projection(array $state, ?int $verifiedAt, int $now): array {
        $fresh = $verifiedAt !== null && $verifiedAt <= $now + 5 && $verifiedAt + self::MAX_AGE >= $now;
        $published = $state['publication_state'] === 'published' &&
            $state['published_source_etag'] === $state['source_etag'];
        $failed = $state['sync_state'] === 'failed' || $state['publication_state'] === 'failed';
        foreach ($state['stages'] as $stage) {
            $failed = $failed || $stage['state'] === 'failed';
        }
        if ($state['graph']['required'] && in_array($state['graph']['state'], ['failed', 'partial_failed', 'unsupported'], true)) {
            $failed = true;
        }
        $verifiedKnowledge = $failed ? 'failed' : ($published ? 'ready' :
            ($state['publication_state'] === 'unverified' ? 'unverified' : 'updating'));
        $knowledge = !$fresh ? 'unverified' : $verifiedKnowledge;
        $graph = $state['graph'];
        if (!$fresh) {
            $graph['last_verified_state'] = $graph['state'];
            $graph['state'] = 'unverified';
        }
        return ['schema_version' => 2, 'knowledge_state' => $knowledge,
            'knowledge_ready_at' => $knowledge === 'ready' ? $state['stages']['index']['completed_at'] : null,
            'published_source_etag' => $state['published_source_etag'],
            'failure_code' => $failed || $state['publication_state'] === 'unverified' ? $state['error_code'] : null,
            'qa_available' => false, 'sync_state' => $state['sync_state'],
            'stages' => $state['stages'], 'graph' => $graph,
            'publication_state' => $state['publication_state'],
            'head_revision' => $state['head_revision'], 'status_revision' => $state['status_revision'],
            'build_attempt' => $state['build_attempt'],
            'processing_config_revision' => $state['processing_config_revision'],
            'occurred_at' => $state['occurred_at'], 'last_verified_at' => $verifiedAt,
            'fresh_until' => $verifiedAt === null ? null : $verifiedAt + self::MAX_AGE,
            'status_stale' => !$fresh, 'last_verified_knowledge_state' => $verifiedKnowledge,
            'withdrawal_state' => $state['withdrawal_state'],
            'permission_reclaim_state' => $state['permission_reclaim_state'], 'gc_state' => $state['gc_state']];
    }
}
