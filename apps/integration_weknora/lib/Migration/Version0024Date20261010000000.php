<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

final class Version0024Date20261010000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_status_inbox')) {
            $table = $schema->createTable('weknora_status_inbox');
            $table->addColumn('connection_id', Types::STRING, ['length' => 128]);
            $table->addColumn('event_id', Types::STRING, ['length' => 128]);
            $table->addColumn('body_sha256', Types::STRING, ['length' => 64]);
            $table->addColumn('received_at', Types::BIGINT);
            $table->setPrimaryKey(['connection_id', 'event_id']);
        }
        if (!$schema->hasTable('weknora_file_status')) {
            $table = $schema->createTable('weknora_file_status');
            $table->addColumn('binding_id', Types::STRING, ['length' => 128]);
            $table->addColumn('file_id', Types::BIGINT);
            $table->addColumn('publication_epoch', Types::BIGINT);
            $table->addColumn('pair_operation_id', Types::STRING, ['length' => 36]);
            $table->addColumn('source_etag', Types::STRING, ['length' => 256]);
            $table->addColumn('head_revision', Types::BIGINT);
            $table->addColumn('status_revision', Types::BIGINT);
            $table->addColumn('body_json', Types::TEXT);
            $table->addColumn('state_sha256', Types::STRING, ['length' => 64]);
            $table->addColumn('received_at', Types::BIGINT);
            $table->addColumn('last_verified_at', Types::BIGINT, ['notnull' => false]);
            $table->addColumn('fresh_until', Types::BIGINT, ['notnull' => false]);
            $table->addColumn('last_poll_at', Types::BIGINT, ['default' => 0]);
            $table->setPrimaryKey(['binding_id', 'file_id']);
            $table->addIndex(['last_poll_at'], 'weknora_status_poll');
        }
        return $schema;
    }
}
