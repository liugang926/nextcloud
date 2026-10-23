<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\IDBConnection;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Privacy-safe audit for candidate, binding and sync control actions. */
final class Version0030Date20261023000000 extends SimpleMigrationStep {
    public function __construct(private IDBConnection $db, private IRootFolder $rootFolder) {
    }

    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_directory_audit')) {
            $table = $schema->createTable('weknora_directory_audit');
            $table->addColumn('id', Types::BIGINT, ['autoincrement' => true]);
            $table->addColumn('actor_uid', Types::STRING, ['length' => 64]);
            $table->addColumn('folder_id', Types::BIGINT);
            $table->addColumn('binding_id', Types::STRING, ['length' => 128, 'notnull' => false]);
            $table->addColumn('operation_id', Types::STRING, ['length' => 36, 'notnull' => false]);
            $table->addColumn('action', Types::STRING, ['length' => 32]);
            $table->addColumn('reason_code', Types::STRING, ['length' => 64, 'notnull' => false]);
            $table->addColumn('created_at', Types::BIGINT);
            $table->setPrimaryKey(['id']);
            $table->addIndex(['folder_id', 'created_at'], 'weknora_audit_folder');
            $table->addIndex(['binding_id', 'created_at'], 'weknora_audit_binding');
        }
        return $schema;
    }

    /**
     * Add physical roots to legacy rules without touching files. Duplicate
     * automatic UID rules are merged only when no durable operation references
     * any duplicate; ambiguous rules are retained for explicit diagnosis.
     */
    public function postSchemaChange(IOutput $output, Closure $schemaClosure, array $options): void {
        $query = $this->db->getQueryBuilder();
        $result = $query->select('rule_id', 'config_json', 'revision')
            ->from('weknora_dir_rules')->orderBy('rule_id')->executeQuery();
        try {
            $rows = $result->fetchAllAssociative();
        } finally {
            $result->closeCursor();
        }
        $groups = [];
        foreach ($rows as $row) {
            try {
                $rule = json_decode((string)$row['config_json'], true, 32, JSON_THROW_ON_ERROR);
                if (!is_array($rule) || !is_string($rule['owner_uid'] ?? null) ||
                    !is_int($rule['container_file_id'] ?? null) ||
                    !is_string($rule['weknora_origin'] ?? null)) {
                    continue;
                }
                $container = $this->folder($rule['owner_uid'], $rule['container_file_id']);
                if ($container === null) {
                    continue;
                }
                $key = hash('sha256', 'nextcloud-filecache-root-v1' . "\0" . $container->getId());
                $rule['physical_root_key'] = $key;
                $groupKey = hash('sha256', $rule['weknora_origin'] . "\0" . $key);
                $groups[$groupKey][] = [
                    'id' => (string)$row['rule_id'],
                    'revision' => (int)$row['revision'],
                    'rule' => $rule,
                ];
            } catch (\Throwable $exception) {
                // Invalid legacy configuration must not acquire a guessed root.
                continue;
            }
        }
        foreach ($groups as $group) {
            usort($group, static fn (array $a, array $b): int => strcmp($a['id'], $b['id']));
            $canonical = $group[0];
            $merge = count($group) > 1;
            if ($merge) {
                foreach ($group as $member) {
                    if (!str_starts_with($member['id'], 'auto_') ||
                        $this->ruleReferences($member['id'])) {
                        $merge = false;
                        break;
                    }
                }
            }
            if ($merge) {
                foreach (array_slice($group, 1) as $member) {
                    $canonical['rule']['delegates'] = array_values(array_unique(array_merge(
                        is_array($canonical['rule']['delegates'] ?? null) ? $canonical['rule']['delegates'] : [],
                        is_array($member['rule']['delegates'] ?? null) ? $member['rule']['delegates'] : [],
                    )));
                    $targets = is_array($canonical['rule']['targets'] ?? null)
                        ? $canonical['rule']['targets'] : [];
                    foreach ($member['rule']['targets'] ?? [] as $target) {
                        if (is_array($target) && !in_array($target, $targets, true)) {
                            $targets[] = $target;
                        }
                    }
                    $canonical['rule']['targets'] = $targets;
                }
            }
            $this->writeRule($canonical, $merge);
            if ($merge) {
                foreach (array_slice($group, 1) as $member) {
                    $delete = $this->db->getQueryBuilder();
                    $delete->delete('weknora_dir_rules')
                        ->where($delete->expr()->eq('rule_id',
                            $delete->createNamedParameter($member['id'])))
                        ->executeStatement();
                }
            }
        }
    }

    private function writeRule(array $entry, bool $revisionChanged): void {
        $query = $this->db->getQueryBuilder();
        $query->update('weknora_dir_rules')
            ->set('config_json', $query->createNamedParameter(
                json_encode($entry['rule'], JSON_THROW_ON_ERROR)))
            ->set('revision', $query->createNamedParameter(
                $entry['revision'] + ($revisionChanged ? 1 : 0)))
            ->set('updated_at', $query->createNamedParameter(time()))
            ->where($query->expr()->eq('rule_id',
                $query->createNamedParameter($entry['id'])))
            ->executeStatement();
    }

    private function ruleReferences(string $ruleId): bool {
        $query = $this->db->getQueryBuilder();
        $query->select($query->func()->count('*'))->from('weknora_dir_intent')
            ->where($query->expr()->eq('rule_id', $query->createNamedParameter($ruleId)));
        $result = $query->executeQuery();
        try {
            if ((int)$result->fetchOne() > 0) {
                return true;
            }
        } finally {
            $result->closeCursor();
        }
        $query = $this->db->getQueryBuilder();
        $query->select($query->func()->count('*'))->from('weknora_target_move')
            ->where($query->expr()->eq('rule_id', $query->createNamedParameter($ruleId)));
        $result = $query->executeQuery();
        try {
            if ((int)$result->fetchOne() > 0) {
                return true;
            }
        } finally {
            $result->closeCursor();
        }
        $query = $this->db->getQueryBuilder();
        $query->select($query->func()->count('*'))->from('weknora_root_move')
            ->where($query->expr()->eq('rule_id', $query->createNamedParameter($ruleId)));
        $result = $query->executeQuery();
        try {
            return (int)$result->fetchOne() > 0;
        } finally {
            $result->closeCursor();
        }
    }

    private function folder(string $uid, int $id): ?Folder {
        try {
            foreach ($this->rootFolder->getUserFolder($uid)->getById($id) as $node) {
                if ($node instanceof Folder && $node->getId() === $id && $node->isReadable()) {
                    return $node;
                }
            }
        } catch (\Throwable $exception) {
            return null;
        }
        return null;
    }
}
