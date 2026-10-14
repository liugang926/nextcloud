<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;

use OCP\IDBConnection;

/** Administrator counters only; no filenames, bodies, addresses or credentials. */
final class StageOperationalStatusService {
    public function __construct(private IDBConnection $db) {}

    public function snapshot(?int $now = null): array {
        $now ??= time();
        // These are replica diagnostics, not the per-user current-file summary.
        $out = ['stored_status_count' => 0, 'stale_status_count' => 0,
            'permission_unverified_count' => 0, 'graph_failure_count' => 0,
            'last_verified_stage_states' => [], 'last_verified_graph_states' => [], 'last_verified_publication_states' => [],
            'oldest_status_validation_age_seconds' => null];
        $q = $this->db->getQueryBuilder();
        $q->select('s.body_json', 's.last_verified_at', 's.fresh_until')
            ->from('weknora_file_status', 's')->innerJoin('s', 'weknora_binding_id', 'b',
                $q->expr()->andX($q->expr()->eq('s.binding_id', 'b.binding_id'),
                    $q->expr()->eq('s.publication_epoch', 'b.publication_epoch')))
            ->where($q->expr()->eq('b.publication_state', $q->createNamedParameter('active')));
        $r = $q->executeQuery();
        try {
            while (($row = $r->fetchAssociative()) !== false) {
                $state = StatusProtocol::decode((string)$row['body_json'], $now);
                $out['stored_status_count']++;
                $fresh = $row['fresh_until'] !== null && (int)$row['fresh_until'] > $now;
                if (!$fresh) { $out['stale_status_count']++; }
                if ($row['last_verified_at'] !== null) {
                    $age = max(0, $now - (int)$row['last_verified_at']);
                    $out['oldest_status_validation_age_seconds'] = max($out['oldest_status_validation_age_seconds'] ?? 0, $age);
                }
                if ($state['last_permission_sync_at'] === null || $now - $state['last_permission_sync_at'] >= 300) {
                    $out['permission_unverified_count']++;
                }
                // Stale successes remain historical and do not count as current success.
                foreach ($state['stages'] as $stage => $value) {
                    $key = $fresh ? $value['state'] : 'unverified';
                    $out['last_verified_stage_states'][$stage][$key] = ($out['last_verified_stage_states'][$stage][$key] ?? 0) + 1;
                }
                $graph = $fresh ? $state['graph']['state'] : 'unverified';
                $out['last_verified_graph_states'][$graph] = ($out['last_verified_graph_states'][$graph] ?? 0) + 1;
                if ($fresh && in_array($graph, ['failed', 'partial_failed', 'unsupported'], true)) { $out['graph_failure_count']++; }
                $publication = $fresh ? $state['publication_state'] : 'unverified';
                $out['last_verified_publication_states'][$publication] = ($out['last_verified_publication_states'][$publication] ?? 0) + 1;
            }
        } finally { $r->closeCursor(); }
        $q = $this->db->getQueryBuilder();
        $r = $q->select('state')->selectAlias($q->func()->count('*'), 'count')
            ->selectAlias($q->func()->min('created_at'), 'oldest_at')
            ->from('weknora_dir_intent')->groupBy('state')->executeQuery();
        $out['directory_intents'] = []; $out['oldest_pending_directory_age_seconds'] = null;
        try {
            while (($row = $r->fetchAssociative()) !== false) {
                $out['directory_intents'][(string)$row['state']] = (int)$row['count'];
                if (in_array($row['state'], ['pending', 'connecting'], true)) {
                    $out['oldest_pending_directory_age_seconds'] = max($out['oldest_pending_directory_age_seconds'] ?? 0,
                        max(0, $now - (int)$row['oldest_at']));
                }
            }
        } finally { $r->closeCursor(); }
        $q = $this->db->getQueryBuilder();
        $r = $q->selectAlias($q->func()->count('*'), 'count')->selectAlias($q->func()->min('queued_at'), 'oldest_at')
            ->from('weknora_dir_discover')->executeQuery();
        try { $row = $r->fetchAssociative(); } finally { $r->closeCursor(); }
        if ($row === false) { throw new \RuntimeException('Discovery queue unavailable'); }
        $out['directory_discovery_pending_count'] = (int)$row['count'];
        $out['oldest_directory_discovery_age_seconds'] = $row['oldest_at'] === null ? null : max(0, $now - (int)$row['oldest_at']);
        return $out;
    }
}
