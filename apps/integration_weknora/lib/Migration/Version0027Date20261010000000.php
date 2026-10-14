<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

final class Version0027Date20261010000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_source_file_rev')) {
            $t = $schema->createTable('weknora_source_file_rev');
            $t->addColumn('file_id', Types::BIGINT);
            $t->addColumn('revision', Types::BIGINT);
            $t->addColumn('writing', Types::BOOLEAN, ['default' => false]);
            $t->addColumn('updated_at', Types::BIGINT);
            $t->setPrimaryKey(['file_id']);
        }
        if (!$schema->hasTable('weknora_status_pending')) {
            $t = $schema->createTable('weknora_status_pending');
            $t->addColumn('binding_id', Types::STRING, ['length' => 128]);
            $t->addColumn('file_id', Types::BIGINT);
            $t->addColumn('last_attempt_at', Types::BIGINT, ['default' => 0]);
            $t->addColumn('revision_token', Types::STRING, ['length' => 32]);
            $t->setPrimaryKey(['binding_id', 'file_id']);
            $t->addIndex(['last_attempt_at'], 'weknora_status_pending_due');
        }
        if (!$schema->hasTable('weknora_status_scan')) {
            $t = $schema->createTable('weknora_status_scan');
            $t->addColumn('binding_id', Types::STRING, ['length' => 128]);
            $t->addColumn('after_file_id', Types::BIGINT, ['default' => 0]);
            $t->addColumn('last_scan_at', Types::BIGINT, ['default' => 0]);
            $t->setPrimaryKey(['binding_id']);
        }
        return $schema;
    }
}
