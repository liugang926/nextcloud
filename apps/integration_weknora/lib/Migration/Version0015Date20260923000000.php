<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Binding-wide publication barrier and its independent audit history. */
final class Version0015Date20260923000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        $bindings = $schema->getTable('weknora_binding_id');
        if (!$bindings->hasColumn('publication_state')) {
            $bindings->addColumn('publication_state', Types::STRING, [
                'length' => 16, 'notnull' => true, 'default' => 'active',
            ]);
        }
        if (!$bindings->hasColumn('publication_epoch')) {
            $bindings->addColumn('publication_epoch', Types::BIGINT, [
                'notnull' => true, 'default' => 0,
            ]);
        }

        $snapshots = $schema->getTable('weknora_manifest_snap');
        if (!$snapshots->hasColumn('binding_epoch')) {
            $snapshots->addColumn('binding_epoch', Types::BIGINT, [
                'notnull' => true, 'default' => 0,
            ]);
        }

        if (!$schema->hasTable('weknora_bind_pub_audit')) {
            $audit = $schema->createTable('weknora_bind_pub_audit');
            $audit->addColumn('id', Types::BIGINT, ['autoincrement' => true, 'notnull' => true]);
            $audit->addColumn('binding_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $audit->addColumn('action', Types::STRING, ['length' => 16, 'notnull' => true]);
            $audit->addColumn('actor_uid', Types::STRING, ['length' => 64, 'notnull' => true]);
            $audit->addColumn('publication_epoch', Types::BIGINT, ['notnull' => true]);
            $audit->addColumn('created_at', Types::BIGINT, ['notnull' => true]);
            $audit->setPrimaryKey(['id']);
            $audit->addIndex(['binding_id', 'id'], 'weknora_bind_pub_history');
        }
        return $schema;
    }
}
