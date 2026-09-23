<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Explicit, one-to-one directory principal to Nextcloud account mappings. */
final class Version0004Date20260923000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_identities')) {
            $table = $schema->createTable('weknora_identities');
            $table->addColumn('id', Types::BIGINT, ['autoincrement' => true, 'notnull' => true]);
            $table->addColumn('directory_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('object_guid', Types::STRING, ['length' => 36, 'notnull' => true]);
            $table->addColumn('nextcloud_uid', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('backend_class', Types::STRING, ['length' => 255, 'notnull' => true]);
            $table->addColumn('actor_uid', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('created_at', Types::BIGINT, ['notnull' => true]);
            $table->setPrimaryKey(['id']);
            $table->addUniqueIndex(['directory_id', 'object_guid'], 'weknora_identity_principal');
            $table->addUniqueIndex(['nextcloud_uid'], 'weknora_identity_uid');
        }

        if (!$schema->hasTable('weknora_identity_audit')) {
            $table = $schema->createTable('weknora_identity_audit');
            $table->addColumn('id', Types::BIGINT, ['autoincrement' => true, 'notnull' => true]);
            $table->addColumn('directory_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('object_guid', Types::STRING, ['length' => 36, 'notnull' => true]);
            $table->addColumn('nextcloud_uid', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('action', Types::STRING, ['length' => 16, 'notnull' => true]);
            $table->addColumn('actor_uid', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('created_at', Types::BIGINT, ['notnull' => true]);
            $table->setPrimaryKey(['id']);
            $table->addIndex(['directory_id', 'object_guid', 'created_at'], 'weknora_identity_history');
        }

        return $schema;
    }
}
