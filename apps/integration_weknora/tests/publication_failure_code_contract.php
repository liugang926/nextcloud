<?php
declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/UnsupportedPublicationAclException.php';
require_once __DIR__ . '/../lib/Service/DirectoryOnboardingRequestException.php';
require_once __DIR__ . '/../lib/Service/DirectoryOnboardingRemoteRejectedException.php';
require_once __DIR__ . '/../lib/Service/DirectoryOnboardingClient.php';
require_once __DIR__ . '/../lib/Service/DirectoryPublicationService.php';

use OCA\IntegrationWeknora\Service\DirectoryOnboardingClient;
use OCA\IntegrationWeknora\Service\DirectoryOnboardingRemoteRejectedException;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\DirectoryOnboardingRequestException;
use OCA\IntegrationWeknora\Service\UnsupportedPublicationAclException;

$method = new ReflectionMethod(DirectoryPublicationService::class, 'failureCode');

$checks = [
    ['target_status', new RuntimeException('remote'), 'target_status_unverified'],
    ['target_status', new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::SERVER_ERROR, 'onboarding_unavailable'), 'target_status_server_error_onboarding'],
    ['target_status', new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::SERVER_ERROR, 'onboarding_unavailable_target_observation'), 'target_status_server_error_onboarding_target_observation'],
    ['target_status', new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::RATE_LIMITED), 'target_status_rate_limited'],
    ['target_status', new DirectoryOnboardingRemoteRejectedException(409, 'nextcloud_target_closed'), 'target_status_rejected_target_closed'],
    ['target_reservation', new RuntimeException('remote'), 'target_reservation_unverified'],
    ['pairing_prepare', new RuntimeException('local'), 'pairing_prepare_unverified'],
    ['pairing_remote', new RuntimeException('remote'), 'pairing_remote_unverified'],
    ['pairing_remote', new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::TRANSPORT), 'pairing_remote_transport'],
    ['pairing_remote', new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::SERVER_ERROR), 'pairing_remote_server_error'],
    ['pairing_remote', new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::SERVER_ERROR, 'source_pairing_unavailable'), 'pairing_remote_server_error_source_prepare'],
    ['pairing_remote', new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::SERVER_ERROR, 'onboarding_unavailable'), 'pairing_remote_server_error_onboarding'],
    ['pairing_remote', new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::RATE_LIMITED), 'pairing_remote_rate_limited'],
    ['pairing_remote', new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::INVALID_RESPONSE), 'pairing_remote_invalid_response'],
    ['pairing_ack', new RuntimeException('ack'), 'pairing_ack_unverified'],
    ['event_connection', new RuntimeException('event'), 'event_connection_pending'],
    ['grant_snapshot', new RuntimeException('snapshot'), 'grant_snapshot_unavailable'],
    ['grant_remote', new RuntimeException('remote'), 'grant_sync_unverified'],
    ['reconcile', new RuntimeException('queue'), 'reconcile_pending'],
    ['intent', new RuntimeException('unknown'), 'connection_unverified'],
    ['event_connection', new DomainException('conflict'), 'event_connection_conflict'],
    ['pairing_prepare', new DomainException('conflict'), 'pairing_prepare_conflict'],
    ['pairing_remote', new DomainException('conflict'), 'pairing_remote_rejected'],
    ['pairing_remote', new DirectoryOnboardingRemoteRejectedException(409, 'source_pairing_conflict'), 'pairing_remote_rejected_source_conflict'],
    ['pairing_remote', new DirectoryOnboardingRemoteRejectedException(409, 'nextcloud_prepared_binding_unavailable'), 'pairing_remote_rejected_binding_unavailable'],
    ['pairing_remote', new DirectoryOnboardingRemoteRejectedException(409), 'pairing_remote_rejected_http_409'],
    ['grant_snapshot', new UnsupportedPublicationAclException('acl'), 'unsupported_source_acl'],
];

foreach ($checks as [$stage, $error, $expected]) {
    $actual = $method->invoke(null, $stage, $error);
    if ($actual !== $expected) {
        throw new RuntimeException("$stage mapped to $actual instead of $expected");
    }
}

$serverError = new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::SERVER_ERROR);
$observed = $method->invoke(null, 'pairing_remote', $serverError, [
    'operation' => ['state' => 'active'],
    'pairing' => ['state' => 'active'],
]);
$reserved = $method->invoke(null, 'pairing_remote', $serverError, [
    'operation' => ['state' => 'reserved'],
    'pairing' => null,
]);
$unknown = $method->invoke(null, 'pairing_remote', $serverError, [
    'operation' => ['state' => 'untrusted remote value'],
    'pairing' => ['state' => 'active'],
]);
if ($observed !== 'pairing_remote_server_error_op_active_pair_active' ||
    $reserved !== 'pairing_remote_server_error_op_reserved_pair_missing' ||
    $unknown !== 'pairing_remote_server_error') {
    throw new RuntimeException('Remote pairing observations did not remain bounded and actionable');
}

$client = (new ReflectionClass(DirectoryOnboardingClient::class))->newInstanceWithoutConstructor();
$safeCode = new ReflectionMethod(DirectoryOnboardingClient::class, 'safeRemoteCode');
$known = $safeCode->invoke($client, '{"error":"source_pairing_unavailable","detail":"never-retained"}');
$unknown = $safeCode->invoke($client, '{"error":"arbitrary remote text"}');
$staged = $safeCode->invoke($client, '{"error":"onboarding_unavailable","failure_stage":"target_observation"}');
if ($known !== 'source_pairing_unavailable' || $staged !== 'onboarding_unavailable_target_observation' || $unknown !== null) {
    throw new RuntimeException('Remote onboarding diagnostics did not enforce the safe code allow-list');
}

echo "Directory publication failure stages remain actionable without exposing remote error text\n";
