<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCA\IntegrationWeknora\Service\MachineKeyRegistryService;
use OCP\DB\ISchemaWrapper;
use OCP\IDBConnection;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Bind existing key rows to their original owner and root file ID. */
final class Version0011Date20260923000000 extends SimpleMigrationStep {
    public function __construct(private IDBConnection $db) {
    }

    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if ($schema->hasTable('weknora_machine_key')) {
            $table = $schema->getTable('weknora_machine_key');
            if (!$table->hasColumn('source_hash')) {
                $table->addColumn('source_hash', Types::STRING, [
                    'length' => 64, 'notnull' => true, 'default' => str_repeat('0', 64),
                ]);
            }
        }
        return $schema;
    }

    public function postSchemaChange(IOutput $output, Closure $schemaClosure, array $options): void {
        try {
            $configured = $this->readLegacyBindings();
            $bindings = [];
            foreach ($configured as $binding) {
                $bindings[$binding['id']] = MachineKeyRegistryService::sourceHash($binding);
            }
        } catch (\Throwable $exception) {
            // Invalid configuration leaves keys unbound and unusable.
            return;
        }

        $query = $this->db->getQueryBuilder();
        $query->select('key_id', 'binding_id')->from('weknora_machine_key');
        $result = $query->executeQuery();
        try {
            $rows = [];
            while (($row = $result->fetchAssociative()) !== false) {
                $rows[] = $row;
            }
        } finally {
            $result->closeCursor();
        }
        foreach ($rows as $row) {
            $hash = $bindings[(string)$row['binding_id']] ?? null;
            if ($hash !== null) {
                $update = $this->db->getQueryBuilder();
                $update->update('weknora_machine_key')
                    ->set('source_hash', $update->createNamedParameter($hash))
                    ->where($update->expr()->eq('key_id',
                        $update->createNamedParameter((string)$row['key_id'])))
                    ->executeStatement();
            }
        }
    }

    /** @return list<array{id: string, owner_uid: string, root_file_id: int}> */
    private function readLegacyBindings(): array {
        $query = $this->db->getQueryBuilder();
        $query->select('configvalue')->from('appconfig')
            ->where($query->expr()->eq('appid', $query->createNamedParameter('integration_weknora')))
            ->andWhere($query->expr()->eq('configkey', $query->createNamedParameter('bindings')));
        $result = $query->executeQuery();
        try {
            $raw = $result->fetchOne();
        } finally {
            $result->closeCursor();
        }
        $bindings = json_decode($raw === false ? '[]' : (string)$raw, true, 512, JSON_THROW_ON_ERROR);
        if (!is_array($bindings) || !array_is_list($bindings)) {
            throw new \UnexpectedValueException('Invalid binding registry');
        }
        $ids = [];
        foreach ($bindings as $binding) {
            if (!is_array($binding) ||
                !isset($binding['id'], $binding['owner_uid'], $binding['root_file_id']) ||
                !is_string($binding['id']) ||
                !preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $binding['id']) ||
                !is_string($binding['owner_uid']) || $binding['owner_uid'] === '' ||
                !is_int($binding['root_file_id']) || $binding['root_file_id'] < 1 ||
                isset($ids[$binding['id']])) {
                throw new \UnexpectedValueException('Invalid binding registry');
            }
            $ids[$binding['id']] = true;
        }
        return $bindings;
    }
}
