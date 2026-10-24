<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Monotonic cleanup proof for deletion tombstones. */
final class Version0032Date20261024001000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure,
        array $options): ?ISchemaWrapper {
        $schema = $schemaClosure();
        if ($schema->hasTable('weknora_delete_tomb')) {
            $table = $schema->getTable('weknora_delete_tomb');
            if (!$table->hasColumn('cleanup_revision')) {
                $table->addColumn('cleanup_revision', Types::BIGINT, ['default' => 0]);
            }
        }
        return $schema;
    }
}
