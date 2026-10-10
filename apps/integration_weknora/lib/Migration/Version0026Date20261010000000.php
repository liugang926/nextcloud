<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Migration;
use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;
final class Version0026Date20261010000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_dir_discover')) {
            $table = $schema->createTable('weknora_dir_discover');
            $table->addColumn('file_id', Types::BIGINT, ['notnull' => true]);
            $table->addColumn('operation_id', Types::STRING, ['notnull' => true, 'length' => 36]);
            foreach (['queued_at', 'next_attempt_at', 'lease_until', 'attempt_count'] as $column) {
                $table->addColumn($column, Types::BIGINT, ['notnull' => true, 'default' => 0]);
            }
            $table->setPrimaryKey(['file_id']);
            $table->addUniqueIndex(['operation_id'], 'weknora_discovery_op');
            $table->addIndex(['next_attempt_at', 'lease_until'], 'weknora_discovery_due');
        }
        return $schema;
    }
}
