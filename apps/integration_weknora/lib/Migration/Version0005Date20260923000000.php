<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** A short-lived, immutable manifest for efficient multi-page reads. */
final class Version0005Date20260923000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_manifest_snap')) {
            $table = $schema->createTable('weknora_manifest_snap');
            $table->addColumn('snapshot_id', Types::STRING, ['length' => 48, 'notnull' => true]);
            $table->addColumn('binding_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('generation', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('root_etag', Types::STRING, ['length' => 255, 'notnull' => true]);
            $table->addColumn('publication_revision', Types::BIGINT, ['notnull' => true]);
            $table->addColumn('items_json', Types::TEXT, ['notnull' => true]);
            $table->addColumn('created_at', Types::BIGINT, ['notnull' => true]);
            $table->setPrimaryKey(['snapshot_id']);
            $table->addIndex(['binding_id', 'created_at'], 'weknora_snap_binding_age');
        }
        return $schema;
    }
}
