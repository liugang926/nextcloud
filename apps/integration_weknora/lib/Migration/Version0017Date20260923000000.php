<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** One recoverable rotation at a time for an established source pair. */
final class Version0017Date20260923000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if ($schema->hasTable('weknora_machine_key')) {
            $keys = $schema->getTable('weknora_machine_key');
            if (!$keys->hasColumn('expires_at')) {
                $keys->addColumn('expires_at', Types::BIGINT, ['notnull' => true, 'default' => 0]);
            }
        }
        if (!$schema->hasTable('weknora_src_pair_rot')) {
            $rotations = $schema->createTable('weknora_src_pair_rot');
            $rotations->addColumn('id', Types::BIGINT, ['autoincrement' => true, 'notnull' => true]);
            $rotations->addColumn('operation_id', Types::STRING, ['length' => 36, 'notnull' => true]);
            $rotations->addColumn('pair_operation_id', Types::STRING, ['length' => 36, 'notnull' => true]);
            $rotations->addColumn('binding_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $rotations->addColumn('instance_id', Types::STRING, ['length' => 64, 'notnull' => true]);
            $rotations->addColumn('tenant_id', Types::STRING, ['length' => 20, 'notnull' => true]);
            $rotations->addColumn('knowledge_base_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $rotations->addColumn('data_source_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $rotations->addColumn('old_key_id', Types::STRING, ['length' => 64, 'notnull' => true]);
            $rotations->addColumn('new_key_id', Types::STRING, ['length' => 64, 'notnull' => true]);
            $rotations->addColumn('publication_epoch', Types::BIGINT, ['notnull' => true]);
            $rotations->addColumn('state', Types::STRING, ['length' => 16, 'notnull' => true]);
            $rotations->addColumn('old_key_expires_at', Types::BIGINT, ['notnull' => true, 'default' => 0]);
            $rotations->addColumn('created_at', Types::BIGINT, ['notnull' => true]);
            $rotations->addColumn('updated_at', Types::BIGINT, ['notnull' => true]);
            $rotations->setPrimaryKey(['id']);
            $rotations->addUniqueIndex(['operation_id'], 'weknora_pair_rot_operation');
            $rotations->addUniqueIndex(['new_key_id'], 'weknora_pair_rot_new_key');
            $rotations->addIndex(['pair_operation_id', 'id'], 'weknora_pair_rot_pair');
        }
        return $schema;
    }
}
