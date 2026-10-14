<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** A binding and its signing keys survive until an exact remote withdrawal ACK. */
final class Version0019Date20260924000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_src_decom')) {
            $table = $schema->createTable('weknora_src_decom');
            $table->addColumn('operation_id', Types::STRING, ['length' => 36, 'notnull' => true]);
            $table->addColumn('pair_operation_id', Types::STRING, ['length' => 36, 'notnull' => true]);
            $table->addColumn('binding_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('instance_id', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('tenant_id', Types::STRING, ['length' => 20, 'notnull' => true]);
            $table->addColumn('knowledge_base_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('data_source_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('key_id', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('publication_epoch', Types::BIGINT, ['notnull' => true]);
            $table->addColumn('state', Types::STRING, ['length' => 16, 'notnull' => true]);
            $table->addColumn('inventory_sha256', Types::STRING,
                ['length' => 64, 'notnull' => true, 'default' => '']);
            $table->addColumn('created_by_uid', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('created_at', Types::BIGINT, ['notnull' => true]);
            $table->addColumn('updated_at', Types::BIGINT, ['notnull' => true]);
            $table->setPrimaryKey(['operation_id']);
            $table->addUniqueIndex(['binding_id'], 'weknora_decom_binding');
            $table->addUniqueIndex(['pair_operation_id'], 'weknora_decom_pair');
        }
        return $schema;
    }
}
