<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Migration;
use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Permanent target controls; pairing credentials and source inventory are retained. */
final class Version0029Date20261014000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_target_close')) {
            $t = $schema->createTable('weknora_target_close');
            foreach (['operation_id' => 36, 'binding_id' => 128, 'root_key' => 64, 'closure_id' => 64] as $name => $length) {
                $t->addColumn($name, Types::STRING, ['length' => $length, 'notnull' => true]);
            }
            $t->addColumn('root_file_id', Types::BIGINT, ['notnull' => true]);
            $t->addColumn('target_json', Types::TEXT, ['notnull' => true]);
            $t->addColumn('recorded_at', Types::BIGINT, ['notnull' => true]);
            $t->setPrimaryKey(['operation_id']);
            $t->addUniqueIndex(['binding_id'], 'weknora_target_close_binding');
            $t->addUniqueIndex(['root_key'], 'weknora_target_close_root');
            $t->addUniqueIndex(['closure_id'], 'weknora_target_close_receipt');
        }
        if (!$schema->hasTable('weknora_tenant_close')) {
            $t = $schema->createTable('weknora_tenant_close');
            foreach (['namespace_key', 'origin_sha256', 'deletion_id'] as $name) { $t->addColumn($name, Types::STRING, ['length' => 64, 'notnull' => true]); }
            $t->addColumn('tenant_id', Types::BIGINT, ['notnull' => true]);
            $t->addColumn('tenant_json', Types::TEXT, ['notnull' => true]);
            $t->addColumn('recorded_at', Types::BIGINT, ['notnull' => true]);
            $t->setPrimaryKey(['namespace_key']); $t->addUniqueIndex(['origin_sha256', 'tenant_id'], 'weknora_tenant_close_namespace');
        }
        if (!$schema->hasTable('weknora_deletion_probe')) {
            $t = $schema->createTable('weknora_deletion_probe');
            $t->addColumn('operation_id', Types::STRING, ['length' => 36, 'notnull' => true]);
            $t->addColumn('next_check_at', Types::BIGINT, ['default' => 0, 'notnull' => true]);
            $t->addColumn('lease_until', Types::BIGINT, ['default' => 0, 'notnull' => true]);
            $t->setPrimaryKey(['operation_id']);
        }
        if ($schema->hasTable('weknora_recovery_log')) {
            $t = $schema->getTable('weknora_recovery_log');
            if (!$t->hasColumn('tenant_namespace')) { $t->addColumn('tenant_namespace', Types::STRING, ['length' => 64, 'notnull' => false]); }
        }
        return $schema;
    }
}
