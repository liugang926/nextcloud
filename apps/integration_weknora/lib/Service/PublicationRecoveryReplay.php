<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IConfig;
use OCP\IDBConnection;

/** Apply only conservative closure in maintenance; reconciliation opens later. */
final class PublicationRecoveryReplay {
    public function __construct(private IDBConnection $db, private IConfig $config,
        private BindingRegistryService $bindings, private FilePublicationStateService $files) {
    }

    public function apply(array $plan): array {
        if (!$this->config->getSystemValueBool('maintenance', false) ||
            ($plan['schema_version'] ?? null) !== 1 ||
            ($plan['instance_id'] ?? null) !== $this->config->getSystemValueString('instanceid') ||
            ($plan['authoritative_reconciliation_required'] ?? null) !== true ||
            ($plan['ingress_reopen_permitted'] ?? null) !== false) {
            throw new \DomainException('Recovery requires matching instance and closed maintenance');
        }
        foreach (['checkpoint_record_sha256', 'recovery_record_sha256'] as $field) {
            if (!is_string($plan[$field] ?? null) || !preg_match('/\A[a-f0-9]{64}\z/D', $plan[$field])) {
                throw new \InvalidArgumentException('Recovery requires external record pins');
            }
        }
        if (!is_array($plan['close_bindings'] ?? null) || !is_array($plan['withdraw_files'] ?? null) ||
            count($plan['close_bindings']) > 10000 || count($plan['withdraw_files']) > 100000) {
            throw new \InvalidArgumentException('Invalid recovery closure inventory');
        }
        $headQuery = $this->db->getQueryBuilder();
        $headResult = $headQuery->select('stream_id', 'sequence', 'chain_sha256')->from('weknora_recovery_head')
            ->where($headQuery->expr()->eq('id', $headQuery->createNamedParameter(1)))->executeQuery();
        try { $head = $headResult->fetchAssociative(); } finally { $headResult->closeCursor(); }
        $checkpoint = filter_var($plan['checkpoint_sequence'] ?? null, FILTER_VALIDATE_INT,
            ['options' => ['min_range' => 0, 'max_range' => PHP_INT_MAX]]);
        $through = filter_var($plan['through_sequence'] ?? null, FILTER_VALIDATE_INT,
            ['options' => ['min_range' => 0, 'max_range' => PHP_INT_MAX]]);
        $planDigest = hash('sha256', json_encode($plan, JSON_THROW_ON_ERROR));
        $receiptQuery = $this->db->getQueryBuilder();
        $receiptResult = $receiptQuery->select('checkpoint_sha256', 'plan_sha256', 'stream_id', 'through_sequence', 'completed')
            ->from('weknora_recovery_apply')->where($receiptQuery->expr()->eq('recovery_sha256',
                $receiptQuery->createNamedParameter($plan['recovery_record_sha256'])))->executeQuery();
        try { $receipt = $receiptResult->fetchAssociative(); } finally { $receiptResult->closeCursor(); }
        if ($receipt !== false && ($receipt['checkpoint_sha256'] !== $plan['checkpoint_record_sha256'] ||
            $receipt['plan_sha256'] !== $planDigest || $receipt['stream_id'] !== ($plan['stream_id'] ?? null) || (int)$receipt['through_sequence'] !== $through)) {
            throw new \DomainException('Recovery retry receipt mismatch');
        }
        if ($head === false || $checkpoint === false || $through === false ||
            ($plan['stream_id'] ?? null) !== $head['stream_id'] || $checkpoint > (int)$head['sequence'] ||
            $receipt === false && $through < (int)$head['sequence']) {
            throw new \DomainException('Recovery stream or conservative checkpoint boundary mismatch');
        }
        if ($receipt === false && (!is_array($plan['database_prefix_hashes'] ?? null) ||
            !hash_equals($head['chain_sha256'], (string)($plan['database_prefix_hashes'][(string)$head['sequence']] ?? '')))) {
            throw new \DomainException('Restored database prefix is not in authenticated external history');
        }
        $checkpointHash = PublicationRecoveryLedger::ZERO_HASH;
        if ($checkpoint > 0) {
            $prefix = $this->db->getQueryBuilder();
            $prefixResult = $prefix->select('chain_sha256')->from('weknora_recovery_log')
                ->where($prefix->expr()->eq('sequence', $prefix->createNamedParameter($checkpoint)))->executeQuery();
            try { $checkpointHash = $prefixResult->fetchOne(); } finally { $prefixResult->closeCursor(); }
        }
        if (!is_string($checkpointHash) || !hash_equals($checkpointHash, (string)($plan['checkpoint_database_chain_sha256'] ?? ''))) {
            throw new \DomainException('Restored checkpoint history does not match external journal');
        }
        $bindingIds = [];
        foreach ($plan['close_bindings'] as $entry) {
            if (!is_array($entry) || !preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $entry['binding_id'] ?? '') ||
                isset($bindingIds[$entry['binding_id']])) {
                throw new \InvalidArgumentException('Invalid recovery binding');
            }
            $bindingIds[$entry['binding_id']] = true;
        }
        $fileIds = [];
        foreach ($plan['withdraw_files'] as $entry) {
            $fileId = filter_var($entry['file_id'] ?? null, FILTER_VALIDATE_INT,
                ['options' => ['min_range' => 1, 'max_range' => PHP_INT_MAX]]);
            $binding = $entry['binding_id'] ?? '';
            if (!isset($bindingIds[$binding]) || $fileId === false || isset($fileIds[$binding . ':' . $fileId])) {
                throw new \InvalidArgumentException('Invalid recovery file');
            }
            $fileIds[$binding . ':' . $fileId] = [$binding, $fileId];
        }
        // Validate every identity before applying the first state transition.
        $current = [];
        foreach ($this->bindings->listBindings() as $binding) { $current[$binding['id']] = $binding; }
        foreach (array_keys($bindingIds) as $id) {
            if (!isset($current[$id])) {
                // A source created after this backup can legitimately be absent.
                // A retired restored identity needs no writable active binding.
                $q = $this->db->getQueryBuilder();
                $result = $q->select('binding_id', 'retired_at')->from('weknora_binding_id')
                    ->where($q->expr()->eq('binding_id', $q->createNamedParameter($id)))->executeQuery();
                try { $registered = $result->fetchAssociative(); } finally { $result->closeCursor(); }
                if ($registered !== false && (int)$registered['retired_at'] === 0) {
                    throw new \DomainException('Registered recovery binding is not resolvable');
                }
            }
        }
        // File storage and ACL changes are not guaranteed to emit every hint.
        // All restored active bindings stay closed until authoritative scanning.
        foreach (array_keys($current) as $id) { $bindingIds[$id] = true; }
        $this->db->insertIgnoreConflict('weknora_recovery_apply', [
            'recovery_sha256' => $plan['recovery_record_sha256'],
            'checkpoint_sha256' => $plan['checkpoint_record_sha256'], 'plan_sha256' => $planDigest,
            'stream_id' => $plan['stream_id'], 'through_sequence' => $through, 'completed' => 0,
        ]);
        $closed = 0;
        foreach (array_keys($bindingIds) as $id) {
            if (!isset($current[$id])) { continue; }
            $this->bindings->setPublicationState($id, 'stopped', 'recovery-ledger');
            $closed++;
        }
        $withdrawn = 0;
        foreach ($fileIds as [$binding, $file]) {
            if (!isset($current[$binding])) { continue; }
            if ($this->files->getState($binding, $file) !== 'withdrawn') {
                $this->files->withdraw($binding, $file, 'recovery-ledger');
            }
            $withdrawn++;
        }
        // Re-read the authority after all idempotent, separately durable steps.
        foreach ($this->bindings->listBindings() as $binding) {
            if (isset($bindingIds[$binding['id']]) && $binding['publication_state'] !== 'stopped') {
                throw new \UnexpectedValueException('Recovery closure was not retained');
            }
        }
        foreach ($fileIds as [$binding, $file]) {
            if (isset($current[$binding]) && $this->files->getState($binding, $file) !== 'withdrawn') {
                throw new \UnexpectedValueException('Recovery file withdrawal was not retained');
            }
        }
        $completed = $this->db->getQueryBuilder();
        $completed->update('weknora_recovery_apply')->set('completed', $completed->createNamedParameter(1))
            ->where($completed->expr()->eq('recovery_sha256', $completed->createNamedParameter($plan['recovery_record_sha256'])))
            ->executeStatement();
        return ['closed_bindings' => $closed, 'withdrawn_files' => $withdrawn,
            'external_replay_applied' => true, 'authoritative_reconciliation_required' => true,
            'ingress_reopen_permitted' => false];
    }
}
