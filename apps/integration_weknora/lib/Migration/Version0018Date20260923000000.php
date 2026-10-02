<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Keep a consumer-applied watermark separate from the durable receipt. */
final class Version0018Date20260923000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if ($schema->hasTable('weknora_event_conn')) {
            $table = $schema->getTable('weknora_event_conn');
            if (!$table->hasColumn('applied_id')) {
                $table->addColumn('applied_id', Types::BIGINT, ['notnull' => true, 'default' => 0]);
            }
            if (!$table->hasColumn('applied_checked_at')) {
                $table->addColumn('applied_checked_at', Types::BIGINT, ['notnull' => true, 'default' => 0]);
            }
            if (!$table->hasColumn('applied_error_code')) {
                $table->addColumn('applied_error_code', Types::STRING,
                    ['length' => 64, 'notnull' => true, 'default' => 'status_unverified']);
            }
        }
        return $schema;
    }
}
