<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;

/** Only fixed control metadata; a computed hash is not transport authorization. */
final class TargetDeletionProtocol {
    public const MAX_RECOVERY_CLOSURES = 10000;
    public const CLOCK_SKEW_SECONDS = 300;
    public const FIELDS = ['schema', 'state', 'reason', 'operation_id', 'instance_id', 'binding_id',
        'tenant_id', 'knowledge_base_id', 'operation_revision', 'closure_id', 'deletion_time_ms', 'closed_at'];

    public static function decimal(mixed $value, bool $positive = true): int {
        if (!is_string($value) || !preg_match('/\A(?:0|[1-9][0-9]{0,18})\z/D', $value) ||
            ($positive && $value === '0') || strlen($value) > strlen((string)PHP_INT_MAX) ||
            strlen($value) === strlen((string)PHP_INT_MAX) && strcmp($value, (string)PHP_INT_MAX) > 0) {
            throw new \UnexpectedValueException('Invalid target control integer');
        }
        return (int)$value;
    }

    public static function target(array $target): array {
        if (count($target) !== count(self::FIELDS)) { throw new \UnexpectedValueException('Incomplete target observation'); }
        $out = [];
        foreach (self::FIELDS as $name) {
            if (!is_string($target[$name] ?? null)) { throw new \UnexpectedValueException('Invalid target observation field'); }
            $out[$name] = $target[$name];
        }
        if ($out['schema'] !== 'weknora-nextcloud-target-v1' ||
            !SourcePairingRegistryService::validOperationId($out['operation_id']) || strtolower($out['operation_id']) !== $out['operation_id'] ||
            !preg_match('/\A[A-Za-z0-9_-]{1,64}\z/D', $out['instance_id']) ||
            !preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $out['binding_id']) ||
            !SourcePairingRegistryService::validTenantId($out['tenant_id']) ||
            !SourcePairingRegistryService::validRemoteId($out['knowledge_base_id'])) {
            throw new \UnexpectedValueException('Invalid target observation scope');
        }
        self::decimal($out['tenant_id']);
        self::decimal($out['operation_revision']);
        if ($out['state'] === 'present' || $out['state'] === 'unverified') {
            if ($out['reason'] !== ($out['state'] === 'present' ? '' : 'target_unverified') ||
                $out['closure_id'] !== '' || $out['deletion_time_ms'] !== '' || $out['closed_at'] !== '') {
                throw new \UnexpectedValueException('Invalid open target observation');
            }
            return $out;
        }
        if ($out['state'] !== 'target_deleted' || !in_array($out['reason'], ['knowledge_base_deleted', 'tenant_deleted'], true) ||
            !preg_match('/\A[a-f0-9]{64}\z/D', $out['closure_id'])) {
            throw new \UnexpectedValueException('Target deletion receipt is unavailable');
        }
        $deleted = self::decimal($out['deletion_time_ms']);
        $closed = self::decimal($out['closed_at']);
        if ($closed < intdiv($deleted, 1000) || $closed > time() + self::CLOCK_SKEW_SECONDS || intdiv($deleted, 1000) > time() + self::CLOCK_SKEW_SECONDS) {
            throw new \UnexpectedValueException('Invalid target closure time');
        }
        $receipt = hash('sha256', implode("\n", ['weknora-target-deleted-v1', $out['operation_id'], $out['tenant_id'],
            $out['knowledge_base_id'], $out['instance_id'], $out['binding_id'], $out['operation_revision'], $out['reason'], $out['deletion_time_ms']]));
        if (!hash_equals($receipt, $out['closure_id'])) { throw new \UnexpectedValueException('Target deletion receipt scope mismatch'); }
        return $out;
    }

    /** The caller obtains this response only through the approved authenticated GET 200. */
    public static function observation(array $response, array $intent, array $request, ?array $pair, string $instance, int $floor): array {
        $operation = $response['operation'] ?? null;
        if (!is_array($operation) || !is_array($response['target'] ?? null)) { throw new \UnexpectedValueException('Target state is unverified'); }
        $target = self::target($response['target']);
        foreach (['operation_id', 'binding_id', 'instance_id'] as $field) {
            $wanted = $field === 'instance_id' ? $instance : $intent[$field];
            if (($request[$field] ?? null) !== $wanted || ($operation[$field] ?? null) !== $wanted || $target[$field] !== $wanted) {
                throw new \UnexpectedValueException('Target operation identity mismatch');
            }
        }
        if (!is_int($operation['tenant_id'] ?? null) || (string)$operation['tenant_id'] !== (string)$request['tenant_id'] ||
            $target['tenant_id'] !== (string)$request['tenant_id'] ||
            ($operation['knowledge_base_id'] ?? null) !== $target['knowledge_base_id'] ||
            (($request['knowledge_base_id'] ?? '') !== '' && $request['knowledge_base_id'] !== $target['knowledge_base_id']) ||
            !is_int($operation['revision'] ?? null) || $operation['revision'] < self::decimal($target['operation_revision']) ||
            self::decimal($target['operation_revision']) < $floor) {
            throw new \UnexpectedValueException('Target operation scope or revision mismatch');
        }
        if ($pair !== null) {
            foreach (['operation_id', 'instance_id', 'binding_id', 'tenant_id', 'knowledge_base_id'] as $field) {
                if ((string)($pair[$field] ?? '') !== $target[$field]) { throw new \UnexpectedValueException('Target pairing scope mismatch'); }
            }
        }
        if ($target['state'] === 'unverified') { throw new \UnexpectedValueException('Target state is unverified'); }
        return $target;
    }

    /**
     * The callback performs only the original operation's approved GET 200,
     * with the current actor. Failed reserve/present/error JSON is not proof.
     * All non-deletion outcomes preserve the original reserve error and retry
     * path; this helper cannot resume, pair, or reconcile a present target.
     */
    public static function deletedAfterFailedReservation(\Throwable $failure, callable $readStatus,
        array $intent, array $request, ?array $pair, string $instance, int $floor): array {
        try {
            $target = self::observation($readStatus(), $intent, $request, $pair, $instance, $floor);
            if ($target['state'] === 'target_deleted') { return $target; }
        } catch (\Throwable $unverified) {
            // Preserve the reserve failure even if a second request fails.
        }
        throw $failure;
    }

    public static function fact(array $fact): array {
        if (count($fact) !== 3 || !is_array($fact['target'] ?? null)) { throw new \UnexpectedValueException('Invalid target closure fact'); }
        $target = self::target($fact['target']);
        $id = self::decimal($fact['root_file_id'] ?? null);
        $key = hash('sha256', 'nextcloud-filecache-root-v1' . "\0" . $id);
        if ($target['state'] !== 'target_deleted' || !is_string($fact['root_key'] ?? null) || !hash_equals($key, $fact['root_key'])) {
            throw new \UnexpectedValueException('Invalid target closure root');
        }
        return ['target' => $target, 'root_file_id' => (string)$id, 'root_key' => $key];
    }

    /** Stable ASCII control fields, appended only for the new journal kind. */
    public static function journalFields(array $fact): array {
        $fact = self::fact($fact);
        return [$fact['root_file_id'], $fact['root_key'], ...array_values($fact['target'])];
    }
}
