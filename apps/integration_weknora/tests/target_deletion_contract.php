<?php
declare(strict_types=1);
require_once __DIR__ . '/../lib/Service/SourcePairingRegistryService.php';
require_once __DIR__ . '/../lib/Service/TargetDeletionProtocol.php';
require_once __DIR__ . '/../lib/Service/PublicationRecoveryLedger.php';
use OCA\IntegrationWeknora\Service\TargetDeletionProtocol as Protocol;
use OCA\IntegrationWeknora\Service\PublicationRecoveryLedger as Ledger;
function targetCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
function targetReject(callable $fn): void { try { $fn(); } catch (UnexpectedValueException $e) { return; } throw new RuntimeException('Invalid target control was accepted'); }
$op = '00112233-4455-4677-8899-aabbccddeeff';
$target = ['schema' => 'weknora-nextcloud-target-v1', 'state' => 'target_deleted', 'reason' => 'knowledge_base_deleted',
    'operation_id' => $op, 'instance_id' => 'instance', 'binding_id' => 'binding', 'tenant_id' => '7', 'knowledge_base_id' => 'kb.with-dot',
    'operation_revision' => '4', 'closure_id' => '', 'deletion_time_ms' => (string)((time() - 1) * 1000), 'closed_at' => (string)time()];
$target['closure_id'] = hash('sha256', implode("\n", ['weknora-target-deleted-v1', $op, '7', 'kb.with-dot', 'instance', 'binding', '4', 'knowledge_base_deleted', $target['deletion_time_ms']]));
$intent = ['operation_id' => $op, 'binding_id' => 'binding'];
$request = ['operation_id' => $op, 'binding_id' => 'binding', 'instance_id' => 'instance', 'tenant_id' => 7, 'knowledge_base_id' => 'kb.with-dot'];
$operation = $request + ['revision' => 4];
$pair = array_replace($request, ['tenant_id' => '7']);
$response = ['operation' => $operation, 'target' => $target];
targetCheck(Protocol::observation($response, $intent, $request, $pair, 'instance', 3) === $target, 'Exact typed control rejected');
foreach (['schema', 'operation_id', 'instance_id', 'binding_id', 'tenant_id', 'knowledge_base_id', 'operation_revision', 'closure_id', 'deletion_time_ms', 'closed_at'] as $field) {
    $bad = $response; $bad['target'][$field] = $field === 'tenant_id' ? '8' : 'invalid';
    targetReject(fn() => Protocol::observation($bad, $intent, $request, $pair, 'instance', 3));
}
$bad = $response; unset($bad['target']['closure_id']); targetReject(fn() => Protocol::observation($bad, $intent, $request, $pair, 'instance', 3));
$bad = $response; $bad['target']['body'] = 'unrecognized'; targetReject(fn() => Protocol::observation($bad, $intent, $request, $pair, 'instance', 3));
targetReject(fn() => Protocol::observation($response, $intent, $request, $pair, 'instance', 5));
$bad = $response; $bad['operation']['revision'] = 3; targetReject(fn() => Protocol::observation($bad, $intent, $request, $pair, 'instance', 0));
foreach (['0', '01', '+1', '1.0', '9223372036854775808', true, 1] as $integer) { targetReject(fn() => Protocol::decimal($integer)); }
$present = $target; $present['state'] = 'present'; $present['reason'] = ''; $present['closure_id'] = ''; $present['deletion_time_ms'] = ''; $present['closed_at'] = '';
targetCheck(Protocol::observation(['operation' => $operation, 'target' => $present], $intent, $request, $pair, 'instance', 3)['state'] === 'present', 'Present contract rejected');
$unknown = $present; $unknown['state'] = 'unverified'; $unknown['reason'] = 'target_unverified';
targetReject(fn() => Protocol::observation(['operation' => $operation, 'target' => $unknown], $intent, $request, $pair, 'instance', 3));
$fact = ['target' => $target, 'root_file_id' => '77', 'root_key' => hash('sha256', "nextcloud-filecache-root-v1\0" . '77')];
targetCheck(Protocol::fact($fact) === $fact && count(Protocol::journalFields($fact)) === 14, 'Permanent root control lost exact fields');
$bad = $fact; $bad['root_file_id'] = '78'; targetReject(fn() => Protocol::fact($bad));
// Only controlled transport callbacks here. The actual NC runtime regression
// below uses DirectoryOnboardingClient, exact HTTP 200, and a WK ledger.
$unpairedRequest = $request; $unpairedRequest['knowledge_base_id'] = '';
$readCount = 0;
$reserveFailure = new DomainException('Reserve rejected', 409);
$readExact = function() use (&$readCount, $response): array { $readCount++; return $response; };
targetCheck(Protocol::deletedAfterFailedReservation($reserveFailure, $readExact, $intent, $unpairedRequest, null, 'instance', 0) === $target && $readCount === 1,
    'Unpaired original target deletion did not converge through one status read');
function targetPreserveReserve(Throwable $failure, callable $read, array $intent, array $request, int $floor = 0): void {
    try { Protocol::deletedAfterFailedReservation($failure, $read, $intent, $request, null, 'instance', $floor); }
    catch (Throwable $actual) { targetCheck($actual === $failure, 'A status outcome replaced the original reserve failure'); return; }
    throw new RuntimeException('Non-deletion status authorized post-reserve continuation');
}
foreach ([400, 401, 403, 404, 409, 422, 500] as $status) {
    targetPreserveReserve($reserveFailure, fn() => throw new DomainException('Status unavailable', $status), $intent, $unpairedRequest);
}
targetPreserveReserve($reserveFailure, fn() => throw new RuntimeException('TLS unavailable'), $intent, $unpairedRequest);
targetPreserveReserve($reserveFailure, fn() => throw new RuntimeException('Timeout'), $intent, $unpairedRequest);
targetPreserveReserve($reserveFailure, fn() => ['operation' => $operation, 'target' => $present], $intent, $unpairedRequest);
targetPreserveReserve($reserveFailure, fn() => ['operation' => $operation, 'target' => $unknown], $intent, $unpairedRequest);
targetPreserveReserve($reserveFailure, fn() => [], $intent, $unpairedRequest);
foreach (['operation_id', 'instance_id', 'binding_id', 'tenant_id', 'knowledge_base_id', 'operation_revision', 'closure_id'] as $field) {
    $mismatch = $response; $mismatch['target'][$field] = 'invalid';
    targetPreserveReserve($reserveFailure, fn() => $mismatch, $intent, $unpairedRequest);
}
$mismatch = $response; $mismatch['operation']['revision'] = 3;
targetPreserveReserve($reserveFailure, fn() => $mismatch, $intent, $unpairedRequest);
targetPreserveReserve($reserveFailure, fn() => $response, $intent, $unpairedRequest, 5);
// The POST exception itself never determines state, even if it was a 404 or
// authentication failure. A subsequently valid GET still needs all proofs.
foreach ([401, 404, 409, 500] as $status) {
    targetCheck(Protocol::deletedAfterFailedReservation(new DomainException('Reserve unavailable', $status), fn() => $response,
        $intent, $unpairedRequest, null, 'instance', 0) === $target, 'Reserve error code became target evidence');
}
$future = $target; $future['deletion_time_ms'] = (string)((time() + 299) * 1000); $future['closed_at'] = (string)(time() + 299);
$future['closure_id'] = hash('sha256', implode("\n", ['weknora-target-deleted-v1', $op, '7', 'kb.with-dot', 'instance', 'binding', '4', 'knowledge_base_deleted', $future['deletion_time_ms']]));
targetCheck(Protocol::target($future) === $future, 'Permitted external clock skew rejected');
$futureFact = $fact; $futureFact['target'] = $future;
$event = ['sequence' => '1', 'binding_id' => 'binding', 'file_id' => null, 'kind' => 'target_deleted',
    'source_revision' => '4', 'created_at' => time(), 'target_closure' => $futureFact];
targetCheck(strlen(Ledger::eventHash(Ledger::ZERO_HASH, $event)) === 64, 'Permitted closure/journal clock skew rejected');
$event['created_at'] -= 100; targetReject(fn() => Ledger::eventHash(Ledger::ZERO_HASH, $event));
$future['closed_at'] = (string)(time() + 360); targetReject(fn() => Protocol::target($future));
targetCheck(Protocol::MAX_RECOVERY_CLOSURES === 10000 && Protocol::CLOCK_SKEW_SECONDS === 300, 'Recovery contract resource bound changed');
echo "Typed target scope, canonical revisions/hash, failed Reserve one-status deletion-only convergence, original failure preservation and root journal contract passed; controlled metadata fixtures only\n";
