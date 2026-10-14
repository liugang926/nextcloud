<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Durable directory intents and encrypted pairing handoffs. No source files are written. */
final class Version0023Date20261010000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_dir_rules')) {
            $table = $schema->createTable('weknora_dir_rules');
            $table->addColumn('rule_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('config_json', Types::TEXT, ['notnull' => true]);
            $table->addColumn('revision', Types::BIGINT, ['notnull' => true, 'default' => 1]);
            $table->addColumn('updated_at', Types::BIGINT, ['notnull' => true]);
            $table->addColumn('discovery_after_id', Types::BIGINT, ['notnull' => true, 'default' => 0]);
            $table->setPrimaryKey(['rule_id']);
        }
        if (!$schema->hasTable('weknora_dir_intent')) {
            $table = $schema->createTable('weknora_dir_intent');
            foreach (['operation_id' => 36, 'binding_id' => 128, 'root_key' => 64,
                'rule_id' => 128, 'owner_uid' => 64, 'actor_uid' => 64,
                'folder_name' => 255, 'state' => 32, 'error_code' => 64] as $name => $length) {
                $table->addColumn($name, Types::STRING, ['length' => $length, 'notnull' => true]);
            }
            $table->addColumn('request_json', Types::TEXT, ['notnull' => true]);
            foreach (['root_file_id', 'parent_file_id', 'permission_version', 'revision',
                'attempt_count', 'next_attempt_at', 'lease_until', 'created_at', 'updated_at'] as $name) {
                $table->addColumn($name, Types::BIGINT, ['notnull' => true, 'default' => 0]);
            }
            $table->setPrimaryKey(['operation_id']);
            $table->addUniqueIndex(['root_key'], 'weknora_dir_root');
            $table->addUniqueIndex(['binding_id'], 'weknora_dir_binding');
            $table->addIndex(['state', 'next_attempt_at'], 'weknora_dir_due');
        }
        if (!$schema->hasTable('weknora_pair_handoff')) {
            $table = $schema->createTable('weknora_pair_handoff');
            $table->addColumn('operation_id', Types::STRING, ['length' => 36, 'notnull' => true]);
            $table->addColumn('token_ciphertext', Types::TEXT, ['notnull' => true]);
            $table->addColumn('created_at', Types::BIGINT, ['notnull' => true]);
            $table->setPrimaryKey(['operation_id']);
        }
        if (!$schema->hasTable('weknora_group_identity')) {
            $table = $schema->createTable('weknora_group_identity');
            $table->addColumn('directory_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('object_guid', Types::STRING, ['length' => 36, 'notnull' => true]);
            $table->addColumn('nextcloud_gid', Types::STRING, ['length' => 255, 'notnull' => true]);
            $table->addColumn('verified_at', Types::BIGINT, ['notnull' => true]);
            $table->setPrimaryKey(['directory_id', 'object_guid']);
            $table->addUniqueIndex(['nextcloud_gid'], 'weknora_group_gid');
        }
        $bindings = $schema->getTable('weknora_binding_id');
        if (!$bindings->hasColumn('sync_enabled')) {
            $bindings->addColumn('sync_enabled', Types::INTEGER, ['notnull' => true, 'default' => 1]);
            $bindings->addColumn('sync_revision', Types::BIGINT, ['notnull' => true, 'default' => 0]);
        }
        return $schema;
    }
}
