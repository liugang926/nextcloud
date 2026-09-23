<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use OCA\IntegrationWeknora\Service\MachineKeyRegistryService;
use OCP\IDBConnection;

/** Reserve every binding ID with retained source state before it can be reused. */
final class BindingIdHistoryBackfill {
    private const HISTORY_TABLES = [
        'weknora_outbox',
        'weknora_pub_state',
        'weknora_pub_audit',
        'weknora_change_floor',
        'weknora_manifest_snap',
    ];

    public static function run(IDBConnection $db): void {
        // An invalid current registry cannot authorize any active identity.
        // Historical IDs are still retired so repairing appconfig later does
        // not allow a fresh binding to inherit old publication state.
        $current = self::currentBindings($db);
        $historical = [];
        foreach (self::HISTORY_TABLES as $table) {
            $query = $db->getQueryBuilder();
            $query->select('binding_id')->from($table)->groupBy('binding_id');
            $result = $query->executeQuery();
            try {
                while (($row = $result->fetchAssociative()) !== false) {
                    $historical[(string)$row['binding_id']] = true;
                }
            } finally {
                $result->closeCursor();
            }
        }

        $retiredAt = time();
        foreach (array_keys($historical) as $bindingId) {
            if (isset($current[$bindingId])) {
                continue;
            }
            $db->insertIgnoreConflict('weknora_binding_id', [
                'binding_id' => $bindingId,
                'source_hash' => str_repeat('0', 64),
                'retired_at' => $retiredAt,
            ]);
            // A retry or an earlier migration may have inserted an active row
            // before the binding disappeared from appconfig. Never revive it.
            $retire = $db->getQueryBuilder();
            $retire->update('weknora_binding_id')
                ->set('retired_at', $retire->createNamedParameter($retiredAt))
                ->where($retire->expr()->eq('binding_id', $retire->createNamedParameter($bindingId)))
                ->andWhere($retire->expr()->eq('retired_at', $retire->createNamedParameter(0)))
                ->executeStatement();
        }
        foreach ($current as $binding) {
            $db->insertIgnoreConflict('weknora_binding_id', [
                'binding_id' => $binding['id'],
                'source_hash' => MachineKeyRegistryService::sourceHash($binding),
                'retired_at' => 0,
            ]);
        }
    }

    /** @return array<string, array{id: string, owner_uid: string, root_file_id: int}> */
    private static function currentBindings(IDBConnection $db): array {
        $query = $db->getQueryBuilder();
        $query->select('configvalue')->from('appconfig')
            ->where($query->expr()->eq('appid', $query->createNamedParameter('integration_weknora')))
            ->andWhere($query->expr()->eq('configkey', $query->createNamedParameter('bindings')));
        $result = $query->executeQuery();
        try {
            $raw = $result->fetchOne();
        } finally {
            $result->closeCursor();
        }
        try {
            $bindings = json_decode($raw === false ? '[]' : (string)$raw, true, 512, JSON_THROW_ON_ERROR);
        } catch (\JsonException $exception) {
            return [];
        }
        if (!is_array($bindings) || !array_is_list($bindings)) {
            return [];
        }
        $current = [];
        foreach ($bindings as $binding) {
            if (!is_array($binding) ||
                !isset($binding['id'], $binding['name'], $binding['owner_uid'], $binding['root_file_id']) ||
                !is_string($binding['id']) ||
                !preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $binding['id']) ||
                !is_string($binding['name']) || $binding['name'] === '' || strlen($binding['name']) > 255 ||
                !is_string($binding['owner_uid']) || $binding['owner_uid'] === '' ||
                !is_int($binding['root_file_id']) || $binding['root_file_id'] < 1 ||
                isset($current[$binding['id']])) {
                return [];
            }
            $current[$binding['id']] = $binding;
        }
        return $current;
    }
}
