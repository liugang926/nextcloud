<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Durable source-pair attempts, independent of event-delivery credentials. */
final class Version0016Date20260923000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_src_pair')) {
            $table = $schema->createTable('weknora_src_pair');
            $table->addColumn('id', Types::BIGINT, ['autoincrement' => true, 'notnull' => true]);
            $table->addColumn('operation_id', Types::STRING, ['length' => 36, 'notnull' => true]);
            $table->addColumn('binding_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('source_hash', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('root_hash', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('publication_epoch', Types::BIGINT, ['notnull' => true]);
            $table->addColumn('instance_id', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('tenant_id', Types::STRING, ['length' => 20, 'notnull' => true]);
            $table->addColumn('knowledge_base_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('data_source_id', Types::STRING, ['length' => 128, 'notnull' => true, 'default' => '']);
            $table->addColumn('key_id', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('state', Types::STRING, ['length' => 16, 'notnull' => true]);
            $table->addColumn('created_by_uid', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('created_at', Types::BIGINT, ['notnull' => true]);
            $table->addColumn('updated_at', Types::BIGINT, ['notnull' => true]);
            $table->setPrimaryKey(['id']);
            $table->addUniqueIndex(['operation_id'], 'weknora_src_pair_operation');
            $table->addUniqueIndex(['key_id'], 'weknora_src_pair_key');
            $table->addIndex(['binding_id', 'id'], 'weknora_src_pair_binding');
            $table->addIndex(['tenant_id', 'knowledge_base_id', 'state'], 'weknora_src_pair_target');
        }
        return $schema;
    }
}
