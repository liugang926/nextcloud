<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;

/** Independent Tenant control metadata; never an indexed-source retirement. */
final class TenantDeletionProtocol {
    public const FIELDS = ['schema', 'state', 'operation_id', 'instance_id', 'binding_id', 'tenant_id', 'deletion_id', 'deletion_time_ms', 'closed_at'];
    public const MAX_RECOVERY_FACTS = 10000;
    public static function originHash(string $origin): string {
        // The rule producer and current client validate this canonical approved
        // origin. Hashing is only namespace selection, never authorization.
        if ($origin === '' || strlen($origin) > 512 || preg_match('/[\x00-\x20\x7f]/', $origin)) { throw new \UnexpectedValueException('Invalid Tenant deletion origin'); }
        return hash('sha256', $origin);
    }
    public static function tenant(array $value): array {
        if (count($value) !== count(self::FIELDS)) { throw new \UnexpectedValueException('Incomplete Tenant deletion fact'); }
        $out = [];
        foreach (self::FIELDS as $name) { if (!is_string($value[$name] ?? null)) { throw new \UnexpectedValueException('Invalid Tenant deletion field'); } $out[$name] = $value[$name]; }
        if ($out['schema'] !== 'weknora-nextcloud-tenant-deletion-v1' || $out['state'] !== 'tenant_deleted' ||
            !SourcePairingRegistryService::validOperationId($out['operation_id']) || strtolower($out['operation_id']) !== $out['operation_id'] ||
            !preg_match('/\A[A-Za-z0-9_-]{1,64}\z/D', $out['instance_id']) || !preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $out['binding_id']) ||
            !preg_match('/\A[a-f0-9]{64}\z/D', $out['deletion_id'])) { throw new \UnexpectedValueException('Invalid Tenant deletion scope'); }
        TargetDeletionProtocol::decimal($out['tenant_id']);
        $deleted = TargetDeletionProtocol::decimal($out['deletion_time_ms']); $closed = TargetDeletionProtocol::decimal($out['closed_at']);
        $now = time();
        if ($closed < intdiv($deleted, 1000) || $closed > $now + TargetDeletionProtocol::CLOCK_SKEW_SECONDS || intdiv($deleted, 1000) > $now + TargetDeletionProtocol::CLOCK_SKEW_SECONDS ||
            !hash_equals(hash('sha256', implode("\n", ['weknora-tenant-deleted-v1', $out['tenant_id'], $out['deletion_time_ms']])), $out['deletion_id'])) {
            throw new \UnexpectedValueException('Invalid immutable Tenant deletion witness');
        }
        return $out;
    }
    public static function fact(array $value): array {
        if (count($value) !== 2 || !is_array($value['tenant'] ?? null) || !is_string($value['origin_sha256'] ?? null) || !preg_match('/\A[a-f0-9]{64}\z/D', $value['origin_sha256'])) {
            throw new \UnexpectedValueException('Invalid Tenant deletion namespace');
        }
        return ['tenant' => self::tenant($value['tenant']), 'origin_sha256' => $value['origin_sha256']];
    }
    /** A missing optional field grants no absence/presence/reopening proof. */
    public static function observation(array $response, array $intent, array $request, string $instance, string $origin): ?array {
        if (!array_key_exists('tenant_deletion', $response)) { return null; }
        if (!is_array($response['tenant_deletion']) || !is_array($response['operation'] ?? null) || !is_array($response['target'] ?? null)) { throw new \UnexpectedValueException('Tenant deletion observation unverified'); }
        $tenant = self::tenant($response['tenant_deletion']); $operation = $response['operation']; $target = TargetDeletionProtocol::target($response['target']);
        foreach (['operation_id', 'binding_id', 'instance_id'] as $field) {
            $wanted = $field === 'instance_id' ? $instance : ($intent[$field] ?? null);
            if (($request[$field] ?? null) !== $wanted || ($operation[$field] ?? null) !== $wanted || $tenant[$field] !== $wanted || $target[$field] !== $wanted) { throw new \UnexpectedValueException('Tenant deletion operation identity mismatch'); }
        }
        if (!is_int($operation['tenant_id'] ?? null) || (string)$operation['tenant_id'] !== (string)($request['tenant_id'] ?? '') ||
            $tenant['tenant_id'] !== (string)$request['tenant_id'] || $target['tenant_id'] !== $tenant['tenant_id'] ||
            !is_int($operation['revision'] ?? null) || $operation['revision'] < TargetDeletionProtocol::decimal($target['operation_revision']) ||
            ($operation['knowledge_base_id'] ?? null) !== $target['knowledge_base_id'] ||
            (($request['knowledge_base_id'] ?? '') !== '' && $request['knowledge_base_id'] !== $target['knowledge_base_id'])) { throw new \UnexpectedValueException('Tenant deletion exact parent scope mismatch'); }
        // Retired/migrated original operations may have target=unverified.
        // This independent fact closes only its Tenant namespace, never that
        // old physical root or a new target which now owns the binding.
        return self::fact(['tenant' => $tenant, 'origin_sha256' => self::originHash($origin)]);
    }
    public static function sameNamespaceFact(array $a, array $b): bool {
        $a = self::fact($a); $b = self::fact($b);
        if ($a['origin_sha256'] !== $b['origin_sha256']) { return false; }
        foreach (['schema', 'state', 'instance_id', 'tenant_id', 'deletion_id', 'deletion_time_ms', 'closed_at'] as $name) { if ($a['tenant'][$name] !== $b['tenant'][$name]) { return false; } }
        return true;
    }
    public static function journalFields(array $value): array { $value = self::fact($value); return [$value['origin_sha256'], ...array_values($value['tenant'])]; }
    public static function requireTargetCoverage(array $targets, array $tenants): void {
        if (count($targets) > TargetDeletionProtocol::MAX_RECOVERY_CLOSURES || count($tenants) > self::MAX_RECOVERY_FACTS) { throw new \UnexpectedValueException('Permanent deletion history exceeds protocol limit'); }
        $coverage = [];
        foreach ($tenants as $fact) { $fact = self::fact($fact); $tenant = $fact['tenant']; $coverage[$tenant['instance_id'] . "\0" . $tenant['tenant_id'] . "\0" . $tenant['deletion_time_ms']] = true; }
        foreach ($targets as $fact) {
            $target = TargetDeletionProtocol::fact($fact)['target'];
            if ($target['reason'] === 'tenant_deleted' && !isset($coverage[$target['instance_id'] . "\0" . $target['tenant_id'] . "\0" . $target['deletion_time_ms']])) { throw new \UnexpectedValueException('Target Tenant deletion lacks independent permanent Tenant history'); }
        }
    }
    public static function compareFacts(array $a, array $b): int { $cmp = strcmp($a['origin_sha256'], $b['origin_sha256']); return $cmp !== 0 ? $cmp : TargetDeletionProtocol::decimal($a['tenant']['tenant_id']) <=> TargetDeletionProtocol::decimal($b['tenant']['tenant_id']); }
}
