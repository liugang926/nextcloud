<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** One durable sender checkpoint and encrypted credential per live binding. */
final class Version0014Date20260923000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_event_conn')) {
            $table = $schema->createTable('weknora_event_conn');
            $table->addColumn('binding_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('connection_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('key_id', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('secret_ciphertext', Types::TEXT, ['notnull' => true]);
            $table->addColumn('receiver_url', Types::STRING, ['length' => 512, 'notnull' => true]);
            $table->addColumn('received_id', Types::BIGINT, ['notnull' => true, 'default' => 0]);
            $table->addColumn('status', Types::STRING, ['length' => 16, 'notnull' => true]);
            $table->addColumn('attempt_count', Types::INTEGER, ['notnull' => true, 'default' => 0]);
            $table->addColumn('next_attempt_at', Types::BIGINT, ['notnull' => true, 'default' => 0]);
            $table->addColumn('last_error_code', Types::STRING, ['length' => 64, 'notnull' => true, 'default' => '']);
            $table->addColumn('created_at', Types::BIGINT, ['notnull' => true]);
            $table->addColumn('updated_at', Types::BIGINT, ['notnull' => true]);
            $table->setPrimaryKey(['binding_id']);
            $table->addUniqueIndex(['connection_id'], 'weknora_event_connection');
            $table->addIndex(['status', 'next_attempt_at'], 'weknora_event_due');
        }
        return $schema;
    }
}
