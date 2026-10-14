<?php

declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/SourcePairingRegistryService.php';
require_once __DIR__ . '/../lib/Service/StatusProtocol.php';
require_once __DIR__ . '/../lib/Service/StatusProjectionService.php';

use OCA\IntegrationWeknora\Service\StatusProtocol;
use OCA\IntegrationWeknora\Service\StatusProjectionService;

function check(bool $condition, string $message): void {
    if (!$condition) { throw new RuntimeException($message); }
}
function rejected(array $state, int $now): void {
    try { StatusProtocol::decode(json_encode($state, JSON_THROW_ON_ERROR), $now); }
    catch (InvalidArgumentException $e) { return; }
    throw new RuntimeException('Invalid metadata was accepted');
}

$now = 1791600000;
$state = ['schema_version' => 2, 'event_id' => 'event-1', 'connection_id' => 'connection-0000001',
    'instance_id' => 'nc-1', 'binding_id' => 'binding-1',
    'pair_operation_id' => 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'tenant_id' => '7',
    'knowledge_base_id' => 'kb-1', 'data_source_id' => 'source-1', 'file_id' => '77',
    'source_etag' => 'etag-2', 'publication_epoch' => '3', 'head_revision' => '6',
    'status_revision' => '20', 'knowledge_id' => 'candidate-2', 'build_attempt' => '2',
    'processing_config_revision' => '1', 'sync_state' => 'ingested',
    'stages' => array_fill_keys(['parse', 'chunk', 'embedding', 'index'],
        ['state' => 'succeeded', 'started_at' => $now - 10, 'completed_at' => $now - 1]),
    'graph' => ['enabled' => true, 'required' => false, 'state' => 'partial_failed',
        'expected' => 2, 'succeeded' => 1, 'failed' => 1, 'skipped' => 0, 'cancelled' => 0,
        'pending' => 0, 'persisted' => false, 'completed_at' => $now - 1, 'error_code' => 'graph_extract_failed'],
    'publication_state' => 'published', 'published_source_etag' => 'etag-2',
    'permission_revision' => '4', 'last_permission_sync_at' => $now - 1,
    'occurred_at' => $now - 1, 'last_verified_at' => $now - 1,
    'withdrawal_state' => 'none', 'permission_reclaim_state' => 'none', 'gc_state' => 'unverified',
    'error_code' => null, 'qa_available' => false];
$decoded = StatusProtocol::decode(json_encode($state, JSON_THROW_ON_ERROR), $now);
$projection = StatusProtocol::projection($decoded, $now - 1, $now);
check($projection['knowledge_state'] === 'ready' && $projection['graph']['state'] === 'partial_failed',
    'Optional graph failure hid the real document/graph outcomes');
check($projection['qa_available'] === false, 'Metadata granted access');
$recoveryClosed = array_replace($state, ['publication_state' => 'unverified',
    'published_source_etag' => null, 'error_code' => 'publication_recovery_closed']);
$recoveryProjection = StatusProtocol::projection(StatusProtocol::decode(
    json_encode($recoveryClosed, JSON_THROW_ON_ERROR), $now), $now - 1, $now);
check($recoveryProjection['knowledge_state'] === 'unverified' &&
    $recoveryProjection['publication_state'] === 'unverified' &&
    $recoveryProjection['failure_code'] === 'publication_recovery_closed',
    'Completed document stages reopened a source during control recovery');
check(StatusProtocol::decode(json_encode($recoveryClosed, JSON_THROW_ON_ERROR), $now)['error_code'] ===
    'publication_recovery_closed', 'Recovery closure reason was discarded');
$expired = StatusProtocol::projection($decoded, $now - 301, $now);
check($expired['knowledge_state'] === 'unverified' && $expired['graph']['state'] === 'unverified' &&
    $expired['graph']['last_verified_state'] === 'partial_failed', 'Old delivery renewed freshness');

// A signed published flag cannot hide a contradictory document stage.
foreach (['parse', 'chunk', 'embedding', 'index'] as $stageName) {
    foreach (['pending', 'running', 'failed', 'blocked', 'cancelled', 'superseded', 'unverified'] as $result) {
        $contradiction = $state;
        $contradiction['stages'][$stageName]['state'] = $result;
        rejected($contradiction, $now);
    }
}
foreach (['discovered', 'downloading', 'failed', 'withdrawn'] as $syncState) {
    rejected(array_replace($state, ['sync_state' => $syncState]), $now);
}
foreach ([['build_attempt' => '0'], ['processing_config_revision' => '0'],
    ['processing_config_revision' => null]] as $identityChange) {
    rejected(array_replace($state, $identityChange), $now);
}
$withoutEmbedding = $state;
$withoutEmbedding['stages']['embedding'] = ['state' => 'skipped', 'started_at' => null, 'completed_at' => null];
check(StatusProtocol::projection(StatusProtocol::decode(json_encode($withoutEmbedding, JSON_THROW_ON_ERROR), $now),
    $now - 1, $now)['knowledge_state'] === 'ready', 'Disabled embedding blocked an otherwise completed document');
$badDocument = $decoded;
$badDocument['stages']['parse']['state'] = 'failed';
check(StatusProtocol::projection($badDocument, $now - 1, $now)['knowledge_state'] === 'failed',
    'Published metadata outranked an actual document failure');

$hash = StatusProtocol::businessHash($decoded);
$old = ['publication_epoch' => '3', 'head_revision' => '6', 'status_revision' => '20',
    'source_etag' => 'etag-2', 'pair_operation_id' => $state['pair_operation_id'], 'state_sha256' => $hash];
check(StatusProjectionService::decision(null, $decoded, $hash) === 'new', 'Terminal-first snapshot could not establish head');
check(StatusProjectionService::decision($old, $decoded, $hash) === 'same', 'Duplicate revision was not idempotent');
$legacy = $decoded;
unset($legacy['source_content_revision']);
$legacyRow = array_replace($old, ['body_json' => json_encode($legacy, JSON_THROW_ON_ERROR),
    'state_sha256' => StatusProtocol::businessHash($legacy)]);
check(StatusProjectionService::decision($legacyRow, $decoded, $hash) === 'same',
    'Default-zero schema upgrade blocked a fresh signed equal-revision snapshot');
$changedGeneration = array_replace($decoded, ['source_content_revision' => '1']);
check(StatusProjectionService::decision($legacyRow, $changedGeneration, StatusProtocol::businessHash($changedGeneration)) === 'conflict',
    'Legacy compatibility admitted a changed content generation');
$changedOutcome = $decoded;
$changedOutcome['graph']['error_code'] = 'graph_persist_failed';
check(StatusProjectionService::decision($legacyRow, $changedOutcome, StatusProtocol::businessHash($changedOutcome)) === 'conflict',
    'Legacy compatibility admitted different business metadata');
$brokenLegacyRow = array_replace($legacyRow, ['state_sha256' => str_repeat('0', 64)]);
check(StatusProjectionService::decision($brokenLegacyRow, $decoded, $hash) === 'conflict',
    'Legacy compatibility trusted a damaged persisted state hash');
$snapshot = array_replace($decoded, ['event_id' => 'snapshot-2', 'last_verified_at' => $now, 'occurred_at' => $now]);
check(StatusProtocol::businessHash($snapshot) === $hash &&
    StatusProjectionService::decision($old, $snapshot, StatusProtocol::businessHash($snapshot)) === 'same',
    'Same current-head verification rewrote business state');
foreach ([['status_revision' => '19'], ['head_revision' => '5', 'status_revision' => '21'],
    ['publication_epoch' => '2', 'status_revision' => '1000']] as $change) {
    $late = array_replace($decoded, $change);
    check(StatusProjectionService::decision($old, $late, StatusProtocol::businessHash($late)) === 'stale',
        'Late old worker could overwrite current head');
}
$new = array_replace($decoded, ['head_revision' => '7', 'status_revision' => '21', 'source_etag' => 'etag-3']);
check(StatusProjectionService::decision($old, $new, StatusProtocol::businessHash($new)) === 'new',
    'New ETag head was rejected');
$collision = array_replace($decoded, ['source_etag' => 'etag-3']);
check(StatusProjectionService::decision($old, $collision, StatusProtocol::businessHash($collision)) === 'conflict',
    'Equal revision accepted a contradictory result');
$previousBody = $old + ['body_json' => json_encode($decoded, JSON_THROW_ON_ERROR)];
foreach (['knowledge_id' => 'another-candidate', 'build_attempt' => '3', 'processing_config_revision' => '2'] as $field => $value) {
    $wrongAttempt = array_replace($decoded, [$field => $value, 'status_revision' => '21']);
    check(StatusProjectionService::decision($previousBody, $wrongAttempt, StatusProtocol::businessHash($wrongAttempt)) === 'conflict',
        'A late candidate or configuration changed without a new current head');
}

foreach ([['qa_available' => true], ['file_id' => '077'], ['status_revision' => '9223372036854775808'],
    ['published_source_etag' => 'etag-1'], ['occurred_at' => $now + 60],
    ['graph' => array_replace($state['graph'], ['required' => true])],
    ['graph' => array_replace($state['graph'], ['state' => 'succeeded', 'persisted' => true])],
    ['graph' => array_replace($state['graph'], ['expected' => 3])],
    ['graph' => array_replace($state['graph'], ['enabled' => false])]] as $change) {
    rejected(array_replace($state, $change), $now);
}
$private = array_replace($state, ['error_code' => 'secret remote stack /bucket/key', 'internal' => 'private']);
$private['graph']['error_code'] = 'entity text';
$safe = StatusProtocol::decode(json_encode($private, JSON_THROW_ON_ERROR), $now);
check($safe['error_code'] === null && $safe['graph']['error_code'] === null && !isset($safe['internal']),
    'Free-form diagnostics escaped metadata allowlist');

$secret = '0123456789abcdef0123456789abcdef';
$key = StatusProtocol::key($secret);
$canonical = StatusProtocol::push('/index.php/apps/integration_weknora/api/v1/status', 'body',
    '1791600000', str_repeat('a', 32), 'connection-0000001', 'current');
check(substr_count($canonical, "\n") === 8 && !str_ends_with($canonical, "\n"), 'Canonical push layout changed');
$signature = hash_hmac('sha256', $canonical, $key);
check(!hash_equals($signature, hash_hmac('sha256', StatusProtocol::push(
    '/index.php/apps/integration_weknora/api/v1/status', 'tampered', '1791600000',
    str_repeat('a', 32), 'connection-0000001', 'current'), $key)), 'Tampered body retained its signature');
check(!hash_equals($signature, hash_hmac('sha256', $canonical, $secret)), 'Status reused event signing key purpose');
check(!hash_equals($signature, hash_hmac('sha256', str_replace('status', 'status/other', $canonical), $key)),
    'Status signature did not bind exact destination');
$vector = json_decode(file_get_contents(__DIR__ . '/fixtures/nextcloud-stage-status-v2-signatures.json'), true, 16, JSON_THROW_ON_ERROR);
$derived = StatusProtocol::key($vector['secret']);
check(bin2hex($derived) === $vector['derived_key_hex'], 'Cross-language key purpose mismatch');
$bodyState = StatusProtocol::decode($vector['body'], (int)$vector['timestamp']);
$request = hash_hmac('sha256', StatusProtocol::request($vector['query'], $vector['timestamp'],
    $vector['nonce'], $bodyState['connection_id'], $vector['key_id']), $derived);
check($request === $vector['request_signature'], 'Cross-language request signature mismatch');
check(hash_hmac('sha256', StatusProtocol::response($request, $vector['body'],
    $bodyState['connection_id'], $vector['key_id'], $vector['nonce']), $derived) === $vector['response_signature'],
    'Cross-language response signature mismatch');
check(hash_hmac('sha256', StatusProtocol::push($vector['push_path'], $vector['body'],
    $vector['timestamp'], $vector['nonce'], $bodyState['connection_id'], $vector['key_id']), $derived) === $vector['push_signature'],
    'Cross-language push signature mismatch');
echo "stage status ordering, graph proof, freshness and signature contract passed\n";
