<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;

/** Fresh authority for an existing scoped purpose; never a source READ proof. */
final class ManagerAuthorizationProtocol {
    public const MAX_RAW_REQUEST_BYTES = 4096;
    public const FIELDS = ['instance_id', 'operation_id', 'tenant_id', 'knowledge_base_id',
        'data_source_id', 'processing_template_id', 'directory_id', 'object_guid', 'purpose_sha256'];

    /** Parse precisely the body authenticated by PairedServiceToken. */
    public static function rawRequest(string $bindingId, mixed $method, mixed $contentType, mixed $uri, string $raw): array {
        if (!is_string($method) || strtoupper($method) !== 'POST' ||
            !is_string($contentType) || strlen($contentType) > 128 ||
            !preg_match('/\Aapplication\/json(?:[ \t]*;[ \t]*charset[ \t]*=[ \t]*(?:UTF-8|"UTF-8"))?[ \t]*\z/iD', $contentType) ||
            !is_string($uri) || str_contains($uri, '?') || strlen($raw) > self::MAX_RAW_REQUEST_BYTES) {
            throw new \InvalidArgumentException('Invalid manager transport');
        }
        $body = json_decode($raw, true, 16, JSON_THROW_ON_ERROR);
        if (!is_array($body) || array_is_list($body)) {
            throw new \InvalidArgumentException('Manager scope must be a JSON object');
        }
        return self::request($bindingId, $body);
    }

    public static function request(string $bindingId, array $request): array {
        $keys = array_keys($request); sort($keys, SORT_STRING);
        $expected = self::FIELDS; sort($expected, SORT_STRING);
        if ($keys !== $expected || !preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $bindingId)) {
            throw new \InvalidArgumentException('Invalid manager scope');
        }
        foreach (self::FIELDS as $field) {
            if (!is_string($request[$field]) || $request[$field] === '' || strlen($request[$field]) > 128) {
                throw new \InvalidArgumentException('Invalid manager scope');
            }
        }
        if (!preg_match('/\A[A-Za-z0-9_-]{1,64}\z/D', $request['instance_id']) ||
            !SourcePairingRegistryService::validOperationId($request['operation_id']) ||
            strtolower($request['operation_id']) !== $request['operation_id'] ||
            !SourcePairingRegistryService::validTenantId($request['tenant_id']) ||
            !SourcePairingRegistryService::validRemoteId($request['knowledge_base_id']) ||
            !SourcePairingRegistryService::validRemoteId($request['data_source_id']) ||
            !SourcePairingRegistryService::validRemoteId($request['processing_template_id']) ||
            !preg_match('/\A[a-f0-9]{64}\z/D', $request['purpose_sha256'])) {
            throw new \InvalidArgumentException('Invalid manager scope');
        }
        [$directory, $guid] = IdentityMappingService::normalizeIdentity($request['directory_id'], $request['object_guid']);
        if ($directory !== $request['directory_id'] || $guid !== $request['object_guid']) {
            throw new \InvalidArgumentException('Invalid manager identity');
        }
        return $request;
    }

    public static function decision(string $bindingId, array $scope, bool $allow, string $reason): array {
        self::request($bindingId, $scope);
        if (($allow && $reason !== 'authorized') || (!$allow && !in_array($reason, ['manager_denied', 'manager_unavailable'], true))) {
            throw new \InvalidArgumentException('Invalid manager decision');
        }
        return $scope + ['binding_id' => $bindingId, 'allow' => $allow, 'reason' => $reason, 'checked_at' => time()];
    }

    public static function requireRecoveryOpen(mixed $row): void {
        // fetchAssociative distinguishes a missing row from a SQL false value.
        if (!is_array($row) || !in_array($row['closed'] ?? null, [false, 0, '0'], true)) {
            throw new \UnexpectedValueException('Manager recovery control unavailable');
        }
    }
}
