<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;
use OCP\IDBConnection;

/** Retains independently observed/signed facts; local inventory is not evidence. */
final class DirectoryTenantDeletionService {
    private static function namespaceKey(string $originSHA, string $tenant): string { TargetDeletionProtocol::decimal($tenant); return hash('sha256', $originSHA . "\0" . $tenant); }
    public static function namespaceKeyFromFact(array $fact): string { $fact = TenantDeletionProtocol::fact($fact); return self::namespaceKey($fact['origin_sha256'], $fact['tenant']['tenant_id']); }
    public static function lookup(IDBConnection $db, string $originSHA, string $tenant): ?array { return self::lookupByNamespaceKey($db, self::namespaceKey($originSHA, $tenant)); }
    public static function lookupByNamespaceKey(IDBConnection $db, string $key): ?array {
        if (!preg_match('/\A[a-f0-9]{64}\z/D', $key)) { throw new \UnexpectedValueException('Invalid Tenant namespace key'); }
        $q = $db->getQueryBuilder(); $r = $q->select('*')->from('weknora_tenant_close')->where($q->expr()->eq('namespace_key', $q->createNamedParameter($key)))->executeQuery();
        try { $row = $r->fetchAssociative(); } finally { $r->closeCursor(); }
        if ($row === false) { return null; }
        $fact = TenantDeletionProtocol::fact(['tenant' => json_decode((string)$row['tenant_json'], true, 16, JSON_THROW_ON_ERROR), 'origin_sha256' => $row['origin_sha256']]);
        if (self::namespaceKeyFromFact($fact) !== $key || $fact['tenant']['deletion_id'] !== $row['deletion_id'] || (string)$row['tenant_id'] !== $fact['tenant']['tenant_id']) { throw new \UnexpectedValueException('Retained Tenant deletion namespace mismatch'); }
        return $fact;
    }
    public static function requireTenantOpen(IDBConnection $db, string $origin, string $tenant): void {
        if (self::lookup($db, TenantDeletionProtocol::originHash($origin), $tenant) !== null) { throw new BindingPublicationStoppedException('Publication Tenant was deleted'); }
    }
    public static function requireBindingOpen(IDBConnection $db, string $binding): void {
        $q = $db->getQueryBuilder(); $r = $q->select('i.request_json', 'r.config_json')->from('weknora_dir_intent', 'i')
            ->leftJoin('i', 'weknora_dir_rules', 'r', $q->expr()->eq('i.rule_id', 'r.rule_id'))
            ->where($q->expr()->eq('i.binding_id', $q->createNamedParameter($binding)))
            ->andWhere($q->expr()->neq('i.state', $q->createNamedParameter('retired')))->executeQuery();
        try { $rows = $r->fetchAllAssociative(); } finally { $r->closeCursor(); }
        foreach ($rows as $row) { $request = json_decode($row['request_json'], true, 16, JSON_THROW_ON_ERROR); $rule = json_decode($row['config_json'], true, 16, JSON_THROW_ON_ERROR); self::requireTenantOpen($db, $rule['weknora_origin'], (string)$request['tenant_id']); }
    }
    public static function validateLocalAnchor(IDBConnection $db, array $fact): void {
        $fact = TenantDeletionProtocol::fact($fact); $tenant = $fact['tenant'];
        $q = $db->getQueryBuilder(); $r = $q->select('i.binding_id', 'i.request_json', 'r.config_json')->from('weknora_dir_intent', 'i')
            ->leftJoin('i', 'weknora_dir_rules', 'r', $q->expr()->eq('i.rule_id', 'r.rule_id'))
            ->where($q->expr()->eq('i.operation_id', $q->createNamedParameter($tenant['operation_id'])))->executeQuery();
        try { $row = $r->fetchAssociative(); } finally { $r->closeCursor(); }
        // A signed permanent fact predating the checkpoint remains a gate even
        // if its old NC intent no longer exists. It never creates that intent.
        if ($row === false) { return; }
        $request = json_decode($row['request_json'], true, 16, JSON_THROW_ON_ERROR); $rule = json_decode((string)$row['config_json'], true, 16, JSON_THROW_ON_ERROR);
        if ($row['binding_id'] !== $tenant['binding_id'] || TenantDeletionProtocol::originHash($rule['weknora_origin']) !== $fact['origin_sha256']) { throw new \DomainException('Tenant deletion anchor namespace changed'); }
        foreach (['operation_id', 'binding_id', 'instance_id', 'tenant_id'] as $name) { if ((string)($request[$name] ?? '') !== $tenant[$name]) { throw new \DomainException('Tenant deletion anchor identity changed'); } }
    }
    public static function retainInTransaction(IDBConnection $db, array $fact): bool {
        if (!$db->inTransaction()) { throw new \LogicException('Tenant deletion requires registry transaction'); }
        $fact = TenantDeletionProtocol::fact($fact); $tenant = $fact['tenant']; $old = self::lookup($db, $fact['origin_sha256'], $tenant['tenant_id']);
        if ($old !== null) { if (!TenantDeletionProtocol::sameNamespaceFact($old, $fact)) { throw new \DomainException('Tenant deletion is immutable'); } return false; }
        if ($db->insertIgnoreConflict('weknora_tenant_close', ['namespace_key' => self::namespaceKey($fact['origin_sha256'], $tenant['tenant_id']),
            'origin_sha256' => $fact['origin_sha256'], 'tenant_id' => (int)$tenant['tenant_id'], 'deletion_id' => $tenant['deletion_id'],
            'tenant_json' => json_encode($tenant, JSON_THROW_ON_ERROR), 'recorded_at' => time()]) !== 1) { throw new \DomainException('Tenant deletion namespace occupied'); }
        return true;
    }
    /** A freshly pinned signed tail cannot clear a gate after losing facts. */
    public static function requireHeldPermanentFacts(IDBConnection $db, array $plan): void {
        $targets = $plan['target_closures'] ?? []; $tenants = $plan['tenant_deletions'] ?? [];
        if (!is_array($targets) || !array_is_list($targets) || !is_array($tenants) || !array_is_list($tenants)) { throw new \DomainException('Permanent recovery history is incomplete'); }
        TenantDeletionProtocol::requireTargetCoverage($targets, $tenants);
        $bindings = []; $roots = []; $scopes = []; $operations = []; $targetFacts = []; $previousBinding = '';
        foreach ($targets as $fact) {
            $fact = TargetDeletionProtocol::fact($fact); $target = $fact['target']; $scope = $target['tenant_id'] . "\0" . $target['knowledge_base_id'];
            if ($target['instance_id'] !== ($plan['instance_id'] ?? '') || strcmp($target['binding_id'], $previousBinding) <= 0 || isset($bindings[$target['binding_id']]) || isset($roots[$fact['root_file_id']]) || isset($scopes[$scope]) || isset($operations[$target['operation_id']]) ||
                DirectoryTargetClosureService::lookup($db, 'operation_id', $target['operation_id']) !== $fact) { throw new \DomainException('Permanent root closure has not been retained exactly'); }
            $previousBinding = $target['binding_id']; $bindings[$target['binding_id']] = true; $roots[$fact['root_file_id']] = true; $scopes[$scope] = true; $operations[$target['operation_id']] = true; $targetFacts[$target['operation_id']] = $fact;
        }
        $previous = null; $namespaces = [];
        foreach ($tenants as $fact) {
            $fact = TenantDeletionProtocol::fact($fact); $key = self::namespaceKeyFromFact($fact);
            if ($fact['tenant']['instance_id'] !== ($plan['instance_id'] ?? '') || isset($namespaces[$key]) || ($previous !== null && TenantDeletionProtocol::compareFacts($previous, $fact) >= 0) || self::lookupByNamespaceKey($db, $key) !== $fact) { throw new \DomainException('Permanent Tenant history has not been retained exactly'); }
            $namespaces[$key] = $fact; $previous = $fact;
        }
        foreach ($plan['journal_events'] ?? [] as $event) {
            if (($event['kind'] ?? null) === 'target_deleted') {
                $fact = TargetDeletionProtocol::fact($event['target_closure'] ?? []);
                if (($targetFacts[$fact['target']['operation_id']] ?? null) !== $fact) { throw new \DomainException('Latest root closure missing from full recovery history'); }
            } elseif (($event['kind'] ?? null) === 'tenant_deleted') {
                $fact = TenantDeletionProtocol::fact($event['tenant_deletion'] ?? []);
                if (($namespaces[self::namespaceKeyFromFact($fact)] ?? null) !== $fact) { throw new \DomainException('Latest Tenant deletion missing from full recovery history'); }
            }
        }
    }

    /** Source controls only for tuples still in the deleted namespace. */
    public static function stopNamespaceInTransaction(IDBConnection $db, array $fact, BindingRegistryService $bindings): void {
        if (!$db->inTransaction()) { throw new \LogicException('Tenant namespace controls require transaction'); }
        $fact = TenantDeletionProtocol::fact($fact); $seen = [];
        $q = $db->getQueryBuilder(); $r = $q->select('i.binding_id', 'i.request_json', 'r.config_json')->from('weknora_dir_intent', 'i')
            ->innerJoin('i', 'weknora_dir_rules', 'r', $q->expr()->eq('i.rule_id', 'r.rule_id'))
            ->where($q->expr()->neq('i.state', $q->createNamedParameter('retired')))->executeQuery();
        try { $rows = $r->fetchAllAssociative(); } finally { $r->closeCursor(); }
        foreach ($rows as $row) {
            $request = json_decode($row['request_json'], true, 16, JSON_THROW_ON_ERROR); $rule = json_decode($row['config_json'], true, 16, JSON_THROW_ON_ERROR);
            if ((string)$request['tenant_id'] !== $fact['tenant']['tenant_id'] || TenantDeletionProtocol::originHash($rule['weknora_origin']) !== $fact['origin_sha256'] || isset($seen[$row['binding_id']])) { continue; }
            $seen[$row['binding_id']] = true;
            $bindings->setPublicationState($row['binding_id'], 'stopped', 'tenant-deleted');
            $u = $db->getQueryBuilder(); $u->update('weknora_binding_id')->set('sync_enabled', $u->createNamedParameter(0))->where($u->expr()->eq('binding_id', $u->createNamedParameter($row['binding_id'])))->executeStatement();
        }
    }
}
