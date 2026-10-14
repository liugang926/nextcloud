<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Durable, atomic replay barrier shared by all web workers. */
final class Version0009Date20260923000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_request_nonce')) {
            $table = $schema->createTable('weknora_request_nonce');
            $table->addColumn('nonce', Types::STRING, ['length' => 32, 'notnull' => true]);
            $table->addColumn('key_id', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('expires_at', Types::BIGINT, ['notnull' => true]);
            $table->setPrimaryKey(['nonce']);
            $table->addIndex(['expires_at'], 'weknora_nonce_expiry');
        }
        return $schema;
    }
}
