<?php

declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/RemoteFileStatusService.php';

use OCA\IntegrationWeknora\Service\RemoteFileStatusService;

$secret = '0123456789abcdef0123456789abcdef';
$connection = 'conn_nextcloud_synthetic_001';
$nonce = str_repeat('a', 32);
$query = 'connection_id=' . $connection . '&file_id=77&source_etag=etag-77';
$request = RemoteFileStatusService::canonicalRequest($query, '1700000000', $nonce,
    $connection, 'current');
$requestSignature = hash_hmac('sha256', $request, $secret);
if ($requestSignature !== 'c5821c22ff797cbfd4d3a34196b4fac6f79fa9c978b482c8bbfbcdca43829989' ||
    substr_count($request, "\n") !== 8 || str_ends_with($request, "\n")) {
    throw new RuntimeException('Nextcloud file-status request HMAC changed');
}
$body = '{"connection_id":"conn_nextcloud_synthetic_001","knowledge_state":"ready"}';
$response = RemoteFileStatusService::canonicalResponse($requestSignature, $body,
    $connection, 'current', $nonce);
if (hash_hmac('sha256', $response, $secret) !==
    '915576f8c17e69a726da905e88838a6d8a6d4559b47ea9994a063e8b8b77103b') {
    throw new RuntimeException('WeKnora file-status response HMAC changed');
}
$changedRequest = RemoteFileStatusService::canonicalRequest(
    str_replace('etag-77', 'etag-78', $query), '1700000000', $nonce, $connection, 'current');
if (hash_equals($requestSignature, hash_hmac('sha256', $changedRequest, $secret))) {
    throw new RuntimeException('Source ETag is not bound to the request');
}
$changedBody = RemoteFileStatusService::canonicalResponse($requestSignature,
    str_replace('"ready"', '"updating"', $body), $connection, 'current', $nonce);
if (hash_equals(hash_hmac('sha256', $response, $secret),
    hash_hmac('sha256', $changedBody, $secret))) {
    throw new RuntimeException('Response body is not bound to the signature');
}

$pair = ['tenant_id' => '7', 'knowledge_base_id' => 'kb-1', 'data_source_id' => 'ds-1'];
$ready = [
    'connection_id' => $connection,
    'nextcloud_instance_id' => 'nc-1',
    'binding_id' => 'binding-1',
    'tenant_id' => '7',
    'knowledge_base_id' => 'kb-1',
    'data_source_id' => 'ds-1',
    'file_id' => '77',
    'source_etag' => 'etag-77',
    'knowledge_state' => 'ready',
    'published_source_etag' => 'etag-77',
    'knowledge_ready_at' => 1700000000,
    'qa_available' => false,
];
$sign = fn (string $raw): string => hash_hmac('sha256',
    RemoteFileStatusService::canonicalResponse($requestSignature, $raw,
        $connection, 'current', $nonce), $secret);
$verify = fn (string $raw, string $signature): ?array =>
    RemoteFileStatusService::validatedResponse($raw, $signature, $requestSignature,
        $secret, $connection, 'current', $nonce, 'nc-1', 'binding-1', $pair,
        77, 'etag-77');
$readyRaw = json_encode($ready, JSON_THROW_ON_ERROR);
if (($verify($readyRaw, $sign($readyRaw))['knowledge_state'] ?? null) !== 'ready') {
    throw new RuntimeException('Current signed publication was not accepted');
}
if ($verify(str_replace('etag-77', 'etag-78', $readyRaw), $sign($readyRaw)) !== null) {
    throw new RuntimeException('Tampered response was accepted');
}
foreach ([
    ['source_etag' => 'etag-78'],
    ['binding_id' => 'other'],
    ['data_source_id' => 'other'],
    ['qa_available' => true],
    ['published_source_etag' => 'etag-76'],
    ['knowledge_ready_at' => null],
] as $change) {
    $wrongRaw = json_encode(array_replace($ready, $change), JSON_THROW_ON_ERROR);
    if ($verify($wrongRaw, $sign($wrongRaw)) !== null) {
        throw new RuntimeException('Signed status with wrong scope or ready proof was accepted');
    }
}
$updatingRaw = json_encode(array_replace($ready, [
    'knowledge_state' => 'updating',
    'published_source_etag' => 'etag-76',
    'knowledge_ready_at' => null,
]), JSON_THROW_ON_ERROR);
if (($verify($updatingRaw, $sign($updatingRaw))['knowledge_state'] ?? null) !== 'updating') {
    throw new RuntimeException('Older published version was not reported as updating');
}
echo "remote file status signature contract passed\n";
