<?php

declare(strict_types=1);

// Run the real migration and service against a private in-memory SQLite DB.
// The small query-builder adapter exercises transactions and injected SQL
// failures without starting or changing a Nextcloud installation.

namespace Doctrine\DBAL\Types {
    final class Types { public const BIGINT = 'bigint'; }
}

namespace OCP\DB { interface ISchemaWrapper {} }
namespace OCP\Migration {
    interface IOutput {}
    class SimpleMigrationStep {}
}
namespace OCP { interface IDBConnection {} interface IConfig {} }

namespace PublicationDecisionContract {
    use OCP\DB\ISchemaWrapper;
    use OCP\IDBConnection;
    use PDO;

    final class Schema implements ISchemaWrapper {
        public function __construct(private PDO $pdo) {}
        public function getTable(string $name): Table { return new Table($this->pdo, $name); }
    }

    final class Table {
        public function __construct(private PDO $pdo, private string $name) {}
        public function hasColumn(string $name): bool {
            $rows = $this->pdo->query('PRAGMA table_info(' . $this->name . ')')->fetchAll(PDO::FETCH_ASSOC);
            return in_array($name, array_column($rows, 'name'), true);
        }
        public function addColumn(string $name, string $type, array $options): void {
            if ($name !== 'decision_audit_id' || $type !== 'bigint' ||
                $options !== ['notnull' => true, 'default' => 0]) {
                throw new \RuntimeException('Unexpected migration column');
            }
            $this->pdo->exec('ALTER TABLE ' . $this->name .
                ' ADD COLUMN decision_audit_id BIGINT NOT NULL DEFAULT 0');
        }
    }

    final class Result {
        public function __construct(private \PDOStatement $statement) {}
        public function fetchOne(): mixed {
            $row = $this->statement->fetch(PDO::FETCH_NUM);
            return $row === false ? false : $row[0];
        }
        public function fetchAssociative(): array|false {
            return $this->statement->fetch(PDO::FETCH_ASSOC);
        }
        public function fetchAllAssociative(): array { return $this->statement->fetchAll(PDO::FETCH_ASSOC); }
        public function closeCursor(): void { $this->statement->closeCursor(); }
    }

    final class Expr {
        public function eq(string $column, mixed $value): array { return [$column, $value, "="]; }
        public function gt(string $column, mixed $value): array { return [$column, $value, ">"]; }
    }

    final class Query {
        private string $verb = '';
        private string $table = '';
        private array $columns = [];
        private array $values = [];
        private array $conditions = [];
        private string $order = "";
        private int $limit = 0;

        public function __construct(private DB $db) {}
        public function expr(): Expr { return new Expr(); }
        public function createNamedParameter(mixed $value): mixed { return $value; }
        public function select(string ...$columns): self { $this->verb = 'select'; $this->columns = $columns; return $this; }
        public function from(string $table): self { $this->table = $table; return $this; }
        public function insert(string $table): self { $this->verb = 'insert'; $this->table = $table; return $this; }
        public function update(string $table): self { $this->verb = 'update'; $this->table = $table; return $this; }
        public function values(array $values): self { $this->values = $values; return $this; }
        public function set(string $column, mixed $value): self { $this->values[$column] = $value; return $this; }
        public function where(array $condition): self { $this->conditions = [$condition]; return $this; }
        public function andWhere(array $condition): self { $this->conditions[] = $condition; return $this; }
        public function orderBy(string $field, string $direction): self { $this->order = " ORDER BY " . $field . " " . $direction; return $this; }
        public function setMaxResults(int $limit): self { $this->limit = $limit; return $this; }
        public function forUpdate(): self { return $this; } // SQLite holds the writer transaction.

        public function executeQuery(): Result {
            $params = [];
            $where = $this->whereSql($params);
            $sql = 'SELECT ' . implode(', ', $this->columns) . ' FROM ' . $this->table . $where . $this->order . ($this->limit ? ' LIMIT ' . $this->limit : '');
            $statement = $this->db->execute($sql, $params);
            return new Result($statement);
        }

        public function executeStatement(): int {
            $params = [];
            if ($this->verb === 'insert') {
                $columns = array_keys($this->values);
                $sql = 'INSERT INTO ' . $this->table . ' (' . implode(', ', $columns) .
                    ') VALUES (' . implode(', ', array_fill(0, count($columns), '?')) . ')';
                $params = array_values($this->values);
            } elseif ($this->verb === 'update') {
                $assignments = [];
                foreach ($this->values as $column => $value) {
                    $assignments[] = $column . ' = ?';
                    $params[] = $value;
                }
                $sql = 'UPDATE ' . $this->table . ' SET ' . implode(', ', $assignments) .
                    $this->whereSql($params);
            } else {
                throw new \RuntimeException('Unexpected statement');
            }
            return $this->db->execute($sql, $params)->rowCount();
        }

        private function whereSql(array &$params): string {
            if ($this->conditions === []) { return ''; }
            $parts = [];
            foreach ($this->conditions as [$column, $value, $operator]) {
                $parts[] = $column . " " . $operator . " ?";
                $params[] = $value;
            }
            return ' WHERE ' . implode(' AND ', $parts);
        }
    }

    final class DB implements IDBConnection {
        public ?string $failSqlContaining = null;
        public function __construct(public PDO $pdo) {}
        public function getQueryBuilder(): Query { return new Query($this); }
        public function inTransaction(): bool { return $this->pdo->inTransaction(); }
        public function beginTransaction(): void { $this->pdo->beginTransaction(); }
        public function commit(): void { $this->pdo->commit(); }
        public function rollBack(): void { $this->pdo->rollBack(); }
        public function lastInsertId(string $table): string { return $this->pdo->lastInsertId(); }
        public function insertIgnoreConflict(string $table, array $values): void {
            $columns = array_keys($values);
            $this->execute('INSERT OR IGNORE INTO ' . $table . ' (' . implode(', ', $columns) .
                ') VALUES (' . implode(', ', array_fill(0, count($columns), '?')) . ')',
                array_values($values));
        }
        public function execute(string $sql, array $params = []): \PDOStatement {
            if ($this->failSqlContaining !== null && str_contains($sql, $this->failSqlContaining)) {
                $this->failSqlContaining = null;
                throw new \RuntimeException('Injected SQL failure');
            }
            $statement = $this->pdo->prepare($sql);
            $statement->execute($params);
            return $statement;
        }
    }

    function expect(bool $condition, string $message): void {
        if (!$condition) { throw new \RuntimeException($message); }
    }
    function scalar(PDO $pdo, string $sql): mixed { return $pdo->query($sql)->fetchColumn(); }
}

namespace {
    require_once __DIR__ . '/../lib/Migration/Version0021Date20261008000000.php';
    require_once __DIR__ . '/../lib/Service/PublicationRecoveryLedger.php';
    require_once __DIR__ . '/../lib/Service/FilePublicationStateService.php';

    use OCA\IntegrationWeknora\Migration\Version0021Date20261008000000;
    use OCA\IntegrationWeknora\Service\FilePublicationStateService;
    use PublicationDecisionContract\DB;
    use PublicationDecisionContract\Schema;
    use function PublicationDecisionContract\expect;
    use function PublicationDecisionContract\scalar;

    $pdo = new PDO('sqlite::memory:');
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->exec('CREATE TABLE weknora_bind_lock (id INTEGER PRIMARY KEY)');
    $pdo->exec('CREATE TABLE weknora_recovery_head(id INTEGER PRIMARY KEY, stream_id TEXT NOT NULL, sequence BIGINT NOT NULL, chain_sha256 TEXT NOT NULL)');
    $pdo->exec('CREATE TABLE weknora_recovery_log(sequence BIGINT PRIMARY KEY, binding_id TEXT NOT NULL, file_id BIGINT, kind TEXT NOT NULL, source_revision BIGINT NOT NULL, created_at BIGINT NOT NULL, chain_sha256 TEXT NOT NULL)');
    $pdo->exec('CREATE TABLE weknora_pub_state (id INTEGER PRIMARY KEY AUTOINCREMENT,
        binding_id TEXT NOT NULL, file_id BIGINT NOT NULL, state TEXT NOT NULL,
        actor_uid TEXT NOT NULL, updated_at BIGINT NOT NULL,
        UNIQUE (binding_id, file_id))');
    $pdo->exec('CREATE TABLE weknora_pub_audit (id INTEGER PRIMARY KEY AUTOINCREMENT,
        binding_id TEXT NOT NULL, file_id BIGINT NOT NULL, action TEXT NOT NULL,
        actor_uid TEXT NOT NULL, created_at BIGINT NOT NULL)');
    $pdo->exec("INSERT INTO weknora_pub_state (binding_id, file_id, state, actor_uid, updated_at)
        VALUES ('binding-a', 7, 'withdrawn', 'legacy-admin', 1)");

    $schema = new Schema($pdo);
    $migration = new Version0021Date20261008000000();
    $output = new class implements \OCP\Migration\IOutput {};
    $migration->changeSchema($output, fn () => $schema, []);
    $migration->changeSchema($output, fn () => $schema, []); // Idempotent schema check.
    expect((int)scalar($pdo, 'SELECT decision_audit_id FROM weknora_pub_state WHERE file_id = 7') === 0,
        'pre-migration state must keep the unknown-history sentinel');

    $db = new DB($pdo);
    $service = new FilePublicationStateService($db);
    expect($service->getDecision('binding-a', 7) ===
        ['state' => 'withdrawn', 'decision_audit_id' => 0],
        'legacy withdrawal was not preserved');
    expect($service->isExcluded('binding-a', 7), 'legacy withdrawal became visible');
    expect($service->getDecision('binding-a', 8) ===
        ['state' => 'eligible', 'decision_audit_id' => 0],
        'unrecorded source acquired a decision revision');

    $service->republish('binding-a', 7, 'admin');
    $first = $service->getDecision('binding-a', 7);
    expect($first['state'] === 'eligible' && $first['decision_audit_id'] > 0,
        'republish did not establish a linked current decision');
    expect(!$service->isExcluded('binding-a', 7), 'exclusion cache was not updated');
    expect((int)scalar($pdo, 'SELECT COUNT(*) FROM weknora_pub_audit') === 1,
        'republish did not append exactly one audit row');

    $service->withdraw('binding-a', 7, 'admin');
    $second = $service->getDecision('binding-a', 7);
    expect($second['state'] === 'withdrawn' &&
        $second['decision_audit_id'] > $first['decision_audit_id'],
        'withdrawal did not advance the local decision revision');
    expect($service->isExcluded('binding-a', 7), 'withdrawal cache did not close access');

    $beforeCount = (int)scalar($pdo, 'SELECT COUNT(*) FROM weknora_pub_audit');
    $db->failSqlContaining = 'UPDATE weknora_pub_state SET';
    try {
        $service->republish('binding-a', 7, 'admin');
        throw new \RuntimeException('Expected state-pointer failure');
    } catch (\RuntimeException $exception) {
        expect($exception->getMessage() === 'Injected SQL failure', 'Unexpected pointer failure');
    }
    expect($service->getDecision('binding-a', 7) === $second &&
        (int)scalar($pdo, 'SELECT COUNT(*) FROM weknora_pub_audit') === $beforeCount &&
        $service->isExcluded('binding-a', 7),
        'failed pointer update committed an unlinked audit or changed visibility');

    $db->failSqlContaining = 'INSERT INTO weknora_pub_audit';
    try {
        $service->withdraw('binding-a', 9, 'admin');
        throw new \RuntimeException('Expected audit insert failure');
    } catch (\RuntimeException $exception) {
        expect($exception->getMessage() === 'Injected SQL failure', 'Unexpected audit failure');
    }
    expect((int)scalar($pdo, 'SELECT COUNT(*) FROM weknora_pub_state WHERE file_id = 9') === 0 &&
        (int)scalar($pdo, 'SELECT COUNT(*) FROM weknora_pub_audit') === $beforeCount,
        'failed audit insert left a current-state row');

    $pdo->exec("UPDATE weknora_pub_audit SET action = 'eligible' WHERE id = " . $second['decision_audit_id']);
    try {
        $service->getDecision('binding-a', 7);
        throw new \RuntimeException('Expected mismatched audit failure');
    } catch (\UnexpectedValueException $exception) {
        expect($exception->getMessage() === 'Current publication decision audit mismatch',
            'Unexpected mismatch result');
    }
    echo "publication decision audit contract passed\n";
}
