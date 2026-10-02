<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\IDBConnection;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Durable, ordered hints for a connector that also performs full reconciliation. */
final class Version0003Date20260923000000 extends SimpleMigrationStep {
    public function __construct(private IDBConnection $db) {
    }

    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_outbox')) {
            $table = $schema->createTable('weknora_outbox');
            $table->addColumn('id', Types::BIGINT, ['autoincrement' => true, 'notnull' => true]);
            $table->addColumn('binding_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('file_id', Types::BIGINT, ['notnull' => false]);
            $table->addColumn('event_type', Types::STRING, ['length' => 32, 'notnull' => true]);
            $table->addColumn('old_path', Types::TEXT, ['notnull' => false]);
            $table->addColumn('path', Types::TEXT, ['notnull' => false]);
            $table->addColumn('etag', Types::STRING, ['length' => 255, 'notnull' => false]);
            $table->addColumn('created_at', Types::BIGINT, ['notnull' => true]);
            $table->setPrimaryKey(['id']);
            $table->addIndex(['binding_id', 'id'], 'weknora_outbox_cursor');
        }
        if (!$schema->hasTable('weknora_outbox_lock')) {
            $table = $schema->createTable('weknora_outbox_lock');
            $table->addColumn('id', Types::INTEGER, ['notnull' => true]);
            $table->setPrimaryKey(['id']);
        }
        if (!$schema->hasTable('weknora_change_floor')) {
            $table = $schema->createTable('weknora_change_floor');
            $table->addColumn('binding_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('floor_id', Types::BIGINT, ['notnull' => true]);
            $table->setPrimaryKey(['binding_id']);
        }
        return $schema;
    }

    public function postSchemaChange(IOutput $output, Closure $schemaClosure, array $options): void {
        $this->db->insertIgnoreConflict('weknora_outbox_lock', ['id' => 1]);
    }
}
