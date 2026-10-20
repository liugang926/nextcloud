<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;
use OCP\IDBConnection;

/** No remote call, GC, token deletion, or retirement ACK is inferred here. */
final class DirectoryTargetClosureService {
    public static function lookup(IDBConnection $db, string $column, string $value): ?array {
        if (!in_array($column, ['operation_id', 'binding_id', 'root_key'], true)) { throw new \LogicException('Invalid closure lookup'); }
        $q = $db->getQueryBuilder();
        $r = $q->select('*')->from('weknora_target_close')->where($q->expr()->eq($column, $q->createNamedParameter($value)))->executeQuery();
        try { $row = $r->fetchAssociative(); } finally { $r->closeCursor(); }
        if ($row === false) { return null; }
        $fact = TargetDeletionProtocol::fact(['target' => json_decode((string)$row['target_json'], true, 16, JSON_THROW_ON_ERROR),
            'root_file_id' => (string)$row['root_file_id'], 'root_key' => $row['root_key']]);
        if ($fact['target']['operation_id'] !== $row['operation_id'] || $fact['target']['binding_id'] !== $row['binding_id'] ||
            $fact['target']['closure_id'] !== $row['closure_id']) { throw new \UnexpectedValueException('Retained target closure mismatch'); }
        return $fact;
    }

    public static function requireOpen(IDBConnection $db, string $binding): void {
        if (self::lookup($db, 'binding_id', $binding) !== null) { throw new BindingPublicationStoppedException('Publication target was deleted'); }
        DirectoryTenantDeletionService::requireBindingOpen($db, $binding);
    }

    /** Caller owns the registry transaction and transport/operation validation. */
    public static function retainInTransaction(IDBConnection $db, array $fact): bool {
        if (!$db->inTransaction()) { throw new \LogicException('Target closure requires a transaction'); }
        $fact = TargetDeletionProtocol::fact($fact);
        $target = $fact['target'];
        $old = self::lookup($db, 'operation_id', $target['operation_id']);
        if ($old !== null) {
            if ($old !== $fact) { throw new \DomainException('Target closure is immutable'); }
            return false;
        }
        if ($db->insertIgnoreConflict('weknora_target_close', ['operation_id' => $target['operation_id'], 'binding_id' => $target['binding_id'],
            'root_file_id' => (int)$fact['root_file_id'], 'root_key' => $fact['root_key'], 'closure_id' => $target['closure_id'],
            'target_json' => json_encode($target, JSON_THROW_ON_ERROR), 'recorded_at' => time()]) !== 1) {
            throw new \DomainException('Target closure identity is occupied');
        }
        return true;
    }

    /** Reapply independently authenticated facts; it cannot reopen controls. */
    public static function closeRestoredIntentInTransaction(IDBConnection $db, array $fact): void {
        if (!$db->inTransaction()) { throw new \LogicException('Target closure requires a transaction'); }
        $fact = TargetDeletionProtocol::fact($fact);
        $target = $fact['target'];
        $q = $db->getQueryBuilder();
        $r = $q->select('*')->from('weknora_dir_intent')->where($q->expr()->eq('operation_id', $q->createNamedParameter($target['operation_id'])))->executeQuery();
        try { $intent = $r->fetchAssociative(); } finally { $r->closeCursor(); }
        if ($intent !== false) {
            $request = json_decode((string)$intent['request_json'], true, 16, JSON_THROW_ON_ERROR);
            foreach (['operation_id', 'binding_id', 'instance_id', 'tenant_id'] as $field) {
                if ((string)($request[$field] ?? '') !== $target[$field]) { throw new \DomainException('Restored intent closure scope mismatch'); }
            }
            if ($intent['binding_id'] !== $target['binding_id'] || (string)$intent['root_file_id'] !== $fact['root_file_id'] ||
                $intent['root_key'] !== $fact['root_key'] || ($request['knowledge_base_id'] !== '' && $request['knowledge_base_id'] !== $target['knowledge_base_id'])) {
                throw new \DomainException('Restored intent target/root mismatch');
            }
            if ($intent['state'] !== 'retired' && $intent['state'] !== 'target_deleted') {
                $revision = (int)$intent['revision'];
                if ($revision === PHP_INT_MAX) { throw new \UnexpectedValueException('Intent revision exhausted'); }
                $q = $db->getQueryBuilder();
                if ($q->update('weknora_dir_intent')->set('state', $q->createNamedParameter('target_deleted'))
                    ->set('error_code', $q->createNamedParameter('target_deleted'))->set('revision', $q->createNamedParameter($revision + 1))
                    ->set('lease_until', $q->createNamedParameter(0))->set('next_attempt_at', $q->createNamedParameter(PHP_INT_MAX))
                    ->set('updated_at', $q->createNamedParameter(time()))
                    ->where($q->expr()->eq('operation_id', $q->createNamedParameter($target['operation_id'])))
                    ->andWhere($q->expr()->eq('revision', $q->createNamedParameter($revision)))->executeStatement() !== 1) {
                    throw new \DomainException('Target closure intent CAS failed');
                }
            }
        }
        $q = $db->getQueryBuilder();
        $q->update('weknora_binding_id')->set('sync_enabled', $q->createNamedParameter(0))
            ->where($q->expr()->eq('binding_id', $q->createNamedParameter($target['binding_id'])))->executeStatement();
    }
}
