<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Store the binding-root-relative target path captured with each file hint. */
final class Version0020Date20261001000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if ($schema->hasTable('weknora_outbox')) {
            $table = $schema->getTable('weknora_outbox');
            if (!$table->hasColumn('relative_path')) {
                $table->addColumn('relative_path', Types::TEXT, ['notnull' => false]);
            }
        }
        return $schema;
    }
}
