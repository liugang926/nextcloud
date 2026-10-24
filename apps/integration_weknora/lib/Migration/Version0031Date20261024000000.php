<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\IDBConnection;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Durable lifecycle metadata and privacy-safe administrator audit. */
final class Version0031Date20261024000000 extends SimpleMigrationStep {
    public function __construct(private IDBConnection $db) {
    }

    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        $schema = $schemaClosure();
        if ($schema->hasTable('weknora_src_pair')) {
            $table = $schema->getTable('weknora_src_pair');
            if (!$table->hasColumn('tenant_name')) {
                $table->addColumn('tenant_name', Types::STRING, ['length' => 255, 'notnull' => false]);
            }
            if (!$table->hasColumn('knowledge_base_name')) {
                $table->addColumn('knowledge_base_name', Types::STRING, ['length' => 255, 'notnull' => false]);
            }
        }
        if (!$schema->hasTable('weknora_delete_tomb')) {
            $table = $schema->createTable('weknora_delete_tomb');
            $table->addColumn('binding_id', Types::STRING, ['length' => 128]);
            $table->addColumn('file_id', Types::BIGINT);
            $table->addColumn('source_etag', Types::STRING, ['length' => 256, 'notnull' => false]);
            $table->addColumn('source_content_revision', Types::BIGINT, ['notnull' => false]);
            $table->addColumn('reason_code', Types::STRING, ['length' => 64]);
            $table->addColumn('generation', Types::BIGINT);
            $table->addColumn('observed_at', Types::BIGINT);
            $table->addColumn('access_closed', Types::BOOLEAN, ['default' => true]);
            $table->addColumn('cleanup_state', Types::STRING, ['length' => 32, 'default' => 'unverified']);
            $table->addColumn('cleanup_revision', Types::BIGINT, ['default' => 0]);
            $table->addColumn('updated_at', Types::BIGINT);
            $table->setPrimaryKey(['binding_id', 'file_id']);
            $table->addIndex(['binding_id', 'observed_at'], 'weknora_tomb_binding');
            $table->addIndex(['cleanup_state', 'updated_at'], 'weknora_tomb_cleanup');
        }
        if (!$schema->hasTable('weknora_connection_audit')) {
            $table = $schema->createTable('weknora_connection_audit');
            $table->addColumn('id', Types::BIGINT, ['autoincrement' => true]);
            $table->addColumn('actor_uid', Types::STRING, ['length' => 64]);
            $table->addColumn('action', Types::STRING, ['length' => 32]);
            $table->addColumn('origin_sha256', Types::STRING, ['length' => 64, 'notnull' => false]);
            $table->addColumn('space_count', Types::INTEGER, ['default' => 0]);
            $table->addColumn('affected_binding_count', Types::INTEGER, ['default' => 0]);
            $table->addColumn('reason_code', Types::STRING, ['length' => 64, 'notnull' => false]);
            $table->addColumn('created_at', Types::BIGINT);
            $table->setPrimaryKey(['id']);
            $table->addIndex(['created_at'], 'weknora_conn_audit_time');
        }
        return $schema;
    }

    /**
     * Backfill display names from already persisted onboarding metadata. IDs
     * remain the stable identity; names only improve the user projection.
     */
    public function postSchemaChange(IOutput $output, Closure $schemaClosure, array $options): void {
        $pairs = $this->db->getQueryBuilder();
        $result = $pairs->select('binding_id', 'tenant_id', 'knowledge_base_id')
            ->from('weknora_src_pair')->where($pairs->expr()->eq('state',
                $pairs->createNamedParameter('active')))->executeQuery();
        try {
            $rows = $result->fetchAllAssociative();
        } finally {
            $result->closeCursor();
        }
        foreach ($rows as $pair) {
            $tenantName = $this->ruleSpaceName((string)$pair['binding_id'], (string)$pair['tenant_id']);
            $knowledgeBaseName = $this->intentKnowledgeBaseName((string)$pair['binding_id']);
            $update = $this->db->getQueryBuilder();
            $update->update('weknora_src_pair')
                ->set('tenant_name', $update->createNamedParameter($tenantName === '' ? null : $tenantName))
                ->set('knowledge_base_name', $update->createNamedParameter(
                    $knowledgeBaseName === '' ? null : $knowledgeBaseName))
                ->where($update->expr()->eq('binding_id',
                    $update->createNamedParameter((string)$pair['binding_id'])));
            $update->executeStatement();
        }
    }

    private function ruleSpaceName(string $bindingId, string $tenantId): string {
        $intent = $this->latestIntent($bindingId);
        if ($intent === null) {
            return '';
        }
        $query = $this->db->getQueryBuilder();
        $result = $query->select('config_json')->from('weknora_dir_rules')
            ->where($query->expr()->eq('rule_id', $query->createNamedParameter((string)$intent['rule_id'])))
            ->setMaxResults(1)->executeQuery();
        try {
            $raw = $result->fetchOne();
        } finally {
            $result->closeCursor();
        }
        if (!is_string($raw)) {
            return '';
        }
        try {
            $rule = json_decode($raw, true, 32, JSON_THROW_ON_ERROR);
        } catch (\Throwable) {
            return '';
        }
        foreach (is_array($rule['targets'] ?? null) ? $rule['targets'] : [] as $target) {
            if (is_array($target) && (string)($target['tenant_id'] ?? '') === $tenantId &&
                is_string($target['space_name'] ?? null)) {
                return substr($target['space_name'], 0, 255);
            }
        }
        return '';
    }

    private function intentKnowledgeBaseName(string $bindingId): string {
        $intent = $this->latestIntent($bindingId);
        if ($intent === null || !is_string($intent['request_json'] ?? null)) {
            return '';
        }
        try {
            $request = json_decode($intent['request_json'], true, 32, JSON_THROW_ON_ERROR);
        } catch (\Throwable) {
            return '';
        }
        return is_string($request['name'] ?? null) ? substr($request['name'], 0, 255) : '';
    }

    private function latestIntent(string $bindingId): ?array {
        $query = $this->db->getQueryBuilder();
        $result = $query->select('rule_id', 'request_json')->from('weknora_dir_intent')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->andWhere($query->expr()->in('state', $query->createNamedParameter(
                ['pending', 'active'], \OCP\DB\QueryBuilder\IQueryBuilder::PARAM_STR_ARRAY)))
            ->orderBy('updated_at', 'DESC')->setMaxResults(1)->executeQuery();
        try {
            $row = $result->fetchAssociative();
            return $row === false ? null : $row;
        } finally {
            $result->closeCursor();
        }
    }
}
