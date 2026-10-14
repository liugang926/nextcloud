<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** New journal starts empty; old audits do not prove a continuous history. */
final class Version0022Date20261009000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_recovery_head')) {
            $head = $schema->createTable('weknora_recovery_head');
            $head->addColumn('id', Types::INTEGER, ['notnull' => true]);
            $head->addColumn('stream_id', Types::STRING, ['length' => 36, 'notnull' => true]);
            $head->addColumn('sequence', Types::BIGINT, ['notnull' => true, 'default' => 0]);
            $head->addColumn('chain_sha256', Types::STRING, ['length' => 64, 'notnull' => true]);
            $head->setPrimaryKey(['id']);
        }
        if (!$schema->hasTable('weknora_recovery_log')) {
            $log = $schema->createTable('weknora_recovery_log');
            $log->addColumn('sequence', Types::BIGINT, ['notnull' => true]);
            $log->addColumn('binding_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $log->addColumn('file_id', Types::BIGINT, ['notnull' => false]);
            $log->addColumn('kind', Types::STRING, ['length' => 32, 'notnull' => true]);
            $log->addColumn('source_revision', Types::BIGINT, ['notnull' => true]);
            $log->addColumn('created_at', Types::BIGINT, ['notnull' => true]);
            $log->addColumn('chain_sha256', Types::STRING, ['length' => 64, 'notnull' => true]);
            $log->setPrimaryKey(['sequence']);
            $log->addIndex(['binding_id', 'sequence'], 'weknora_recovery_scope');
        }
        if (!$schema->hasTable('weknora_recovery_apply')) {
            $apply = $schema->createTable('weknora_recovery_apply');
            $apply->addColumn('recovery_sha256', Types::STRING, ['length' => 64, 'notnull' => true]);
            $apply->addColumn('checkpoint_sha256', Types::STRING, ['length' => 64, 'notnull' => true]);
            $apply->addColumn('plan_sha256', Types::STRING, ['length' => 64, 'notnull' => true]);
            $apply->addColumn('stream_id', Types::STRING, ['length' => 36, 'notnull' => true]);
            $apply->addColumn('through_sequence', Types::BIGINT, ['notnull' => true]);
            $apply->addColumn('completed', Types::INTEGER, ['notnull' => true, 'default' => 0]);
            $apply->setPrimaryKey(['recovery_sha256']);
        }
        return $schema;
    }
}
