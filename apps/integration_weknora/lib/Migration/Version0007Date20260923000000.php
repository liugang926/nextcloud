<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Old snapshots must not survive the commit-ordered revision protocol. */
final class Version0007Date20260923000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if ($schema->hasTable('weknora_manifest_snap')) {
            $table = $schema->getTable('weknora_manifest_snap');
            if (!$table->hasColumn('format_version')) {
                $table->addColumn('format_version', Types::INTEGER, ['notnull' => true, 'default' => 1]);
            }
        }
        return $schema;
    }
}
