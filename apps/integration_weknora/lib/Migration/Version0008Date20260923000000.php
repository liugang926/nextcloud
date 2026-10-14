<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Finds bindings with retention-eligible change hints without a table scan. */
final class Version0008Date20260923000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if ($schema->hasTable('weknora_outbox')) {
            $table = $schema->getTable('weknora_outbox');
            if (!$table->hasIndex('weknora_outbox_retention')) {
                $table->addIndex(['created_at', 'binding_id'], 'weknora_outbox_retention');
            }
        }
        return $schema;
    }
}
