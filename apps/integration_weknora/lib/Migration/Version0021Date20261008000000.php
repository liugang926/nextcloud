<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Link subsequent local file decisions to their audit row in one transaction. */
final class Version0021Date20261008000000 extends SimpleMigrationStep {
    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        $state = $schema->getTable('weknora_pub_state');
        if (!$state->hasColumn('decision_audit_id')) {
            // Existing rows have no proven current-decision link. A later
            // administrator write will replace this sentinel with its audit ID.
            $state->addColumn('decision_audit_id', Types::BIGINT, [
                'notnull' => true, 'default' => 0,
            ]);
        }
        return $schema;
    }
}
