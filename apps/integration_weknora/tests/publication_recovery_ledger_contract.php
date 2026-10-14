<?php

declare(strict_types=1);

require_once __DIR__ . '/publication_decision_audit_contract.php';

use OCA\IntegrationWeknora\Service\PublicationRecoveryLedger;
use function PublicationDecisionContract\expect;
use function PublicationDecisionContract\scalar;

$pdo->exec("UPDATE weknora_pub_audit SET action = 'withdrawn' WHERE id = " . $second['decision_audit_id']);

$config = new class implements \OCP\IConfig {
    public function getSystemValueString(string $key): string { return 'isolated-instance'; }
};
$ledger = new PublicationRecoveryLedger($db, $config);
$page = $ledger->page(0);
expect(count($page['items']) > 0 && !$page['has_more'], 'actual publication decisions were not journaled');
expect($page['next_sequence'] === $page['head_sequence'], 'journal did not reach current head');
expect($page['items'][0]['kind'] === 'eligible', 'actual republish was not captured');
foreach ($page['items'] as $item) {
    expect(array_keys($item) === ['sequence','binding_id','file_id','kind','source_revision','created_at'], 'body/path/credentials entered recovery journal');
}
$head = (int)$page['head_sequence'];
$db->failSqlContaining = 'INSERT INTO weknora_recovery_log';
$before = $service->getDecision('binding-a', 7);
try { $service->republish('binding-a', 7, 'admin'); throw new \RuntimeException('Expected journal failure'); }
catch (\RuntimeException $e) { expect($e->getMessage() === 'Injected SQL failure', 'Wrong journal rollback failure'); }
expect($service->getDecision('binding-a', 7) === $before, 'journal failure committed business publication');
expect((int)scalar($pdo, 'SELECT sequence FROM weknora_recovery_head') === $head, 'failed commit consumed sequence');
try { PublicationRecoveryLedger::appendInTransaction($db, 'binding-a', 7, 'delete', 1); throw new \RuntimeException('Expected missing transaction'); }
catch (\InvalidArgumentException $e) {}
for ($i=0; $i<205; $i++) {
    $db->beginTransaction();
    PublicationRecoveryLedger::appendInTransaction($db, 'binding-b', $i+1, 'delete', $i+1);
    $db->commit();
}
$first = $ledger->page($head);
expect(count($first['items']) === 200 && $first['has_more'], 'bounded first page failed');
$last = $ledger->page((int)$first['next_sequence']);
expect(count($last['items']) === 5 && !$last['has_more'], 'continuous terminal page failed');
$pdo->exec('DELETE FROM weknora_recovery_log WHERE sequence=' . ($head+2));
try { $ledger->page($head); throw new \RuntimeException('Expected missing sequence'); }
catch (\UnexpectedValueException $e) { expect(str_contains($e->getMessage(), 'continuity'), 'Wrong missing-sequence refusal'); }
try { $ledger->page((int)$last['head_sequence']+1); throw new \RuntimeException('Expected future cursor'); }
catch (\DomainException $e) {}
echo "Publication recovery ledger contract passed\n";
