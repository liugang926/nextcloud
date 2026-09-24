<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Migration;

use Closure;
use Doctrine\DBAL\Types\Types;
use OCA\IntegrationWeknora\Service\MachineKeyRegistryService;
use OCP\DB\ISchemaWrapper;
use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\IDBConnection;
use OCP\IUserManager;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/** Move a safe single-binding installation to scoped machine credentials. */
final class Version0010Date20260923000000 extends SimpleMigrationStep {
    public function __construct(
        private IDBConnection $db,
        private IRootFolder $rootFolder,
        private IUserManager $userManager,
    ) {
    }

    public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
        /** @var ISchemaWrapper $schema */
        $schema = $schemaClosure();
        if (!$schema->hasTable('weknora_machine_key')) {
            $table = $schema->createTable('weknora_machine_key');
            $table->addColumn('key_id', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('binding_id', Types::STRING, ['length' => 128, 'notnull' => true]);
            $table->addColumn('token_sha256', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->addColumn('created_at', Types::BIGINT, ['notnull' => true]);
            $table->addColumn('created_by_uid', Types::STRING, ['length' => 64, 'notnull' => true]);
            $table->setPrimaryKey(['key_id']);
            $table->addIndex(['binding_id'], 'weknora_machine_binding');
        }
        return $schema;
    }

    public function postSchemaChange(IOutput $output, Closure $schemaClosure, array $options): void {
        try {
            $config = $this->readLegacyConfig();
            $bindingId = $this->soleActiveBinding($config['bindings'] ?? '[]');
            if ($bindingId === null) {
                return;
            }
        } catch (\Throwable $exception) {
            // Unknown or unavailable roots must not receive an implicit key.
            return;
        }

        $currentId = $config['service_key_id'] ?? 'default';
        $currentHash = $config['service_token_sha256'] ?? '';
        if (!MachineKeyRegistryService::validKeyId($currentId) ||
            !MachineKeyRegistryService::validHash($currentHash)) {
            return;
        }
        $this->insertLegacy($bindingId, $currentId, $currentHash);
        $previousId = $config['service_previous_key_id'] ?? '';
        $previousHash = $config['service_previous_token_sha256'] ?? '';
        if ($previousId !== $currentId && MachineKeyRegistryService::validKeyId($previousId) &&
            MachineKeyRegistryService::validHash($previousHash)) {
            $this->insertLegacy($bindingId, $previousId, $previousHash);
        }
    }

    /** @return array<string, string> */
    private function readLegacyConfig(): array {
        $query = $this->db->getQueryBuilder();
        $query->select('configkey', 'configvalue')->from('appconfig')
            ->where($query->expr()->eq('appid', $query->createNamedParameter('integration_weknora')));
        $result = $query->executeQuery();
        try {
            $keys = [];
            while (($row = $result->fetchAssociative()) !== false) {
                if (in_array($row['configkey'], [
                    'bindings',
                    'service_key_id', 'service_token_sha256',
                    'service_previous_key_id', 'service_previous_token_sha256',
                ], true)) {
                    $keys[$row['configkey']] = (string)$row['configvalue'];
                }
            }
            return $keys;
        } finally {
            $result->closeCursor();
        }
    }

    private function soleActiveBinding(string $raw): ?string {
        try {
            $bindings = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
        } catch (\JsonException $exception) {
            return null;
        }
        if (!is_array($bindings) || !array_is_list($bindings) || count($bindings) !== 1) {
            return null;
        }
        $binding = $bindings[0];
        if (!is_array($binding) ||
            !isset($binding['id'], $binding['name'], $binding['owner_uid'], $binding['root_file_id']) ||
            !is_string($binding['id']) ||
            !preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $binding['id']) ||
            !is_string($binding['name']) || $binding['name'] === '' ||
            !is_string($binding['owner_uid']) || $binding['owner_uid'] === '' ||
            !is_int($binding['root_file_id']) || $binding['root_file_id'] < 1) {
            return null;
        }
        $owner = $this->userManager->get($binding['owner_uid']);
        if ($owner === null || !$owner->isEnabled()) {
            return null;
        }
        $userFolder = $this->rootFolder->getUserFolder($binding['owner_uid']);
        if ($userFolder->getId() === $binding['root_file_id']) {
            return null;
        }
        foreach ($userFolder->getById($binding['root_file_id']) as $node) {
            if ($node instanceof Folder && $node->getId() === $binding['root_file_id'] &&
                $userFolder->isSubNode($node) && $node->isReadable()) {
                return $binding['id'];
            }
        }
        return null;
    }

    private function insertLegacy(string $bindingId, string $keyId, string $hash): void {
        $this->db->insertIgnoreConflict('weknora_machine_key', [
            'key_id' => $keyId,
            'binding_id' => $bindingId,
            'token_sha256' => strtolower($hash),
            'created_at' => time(),
            'created_by_uid' => 'migration',
        ]);
    }
}
