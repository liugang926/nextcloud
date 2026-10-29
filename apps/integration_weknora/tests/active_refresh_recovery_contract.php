<?php
declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/BindingPublicationStoppedException.php';
require_once __DIR__ . '/../lib/Service/UnsupportedPublicationAclException.php';
require_once __DIR__ . '/../lib/Service/DirectoryOnboardingRequestException.php';
require_once __DIR__ . '/../lib/Service/DirectoryOnboardingRemoteRejectedException.php';
require_once __DIR__ . '/../lib/Service/DirectoryPublicationService.php';

use OCA\IntegrationWeknora\Service\BindingPublicationStoppedException;
use OCA\IntegrationWeknora\Service\DirectoryOnboardingRemoteRejectedException;
use OCA\IntegrationWeknora\Service\DirectoryOnboardingRequestException;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCA\IntegrationWeknora\Service\UnsupportedPublicationAclException;

function activeRefreshCheck(bool $condition, string $message): void {
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

$permanent = new ReflectionMethod(DirectoryPublicationService::class, 'activeRefreshFailureIsPermanent');
$retryCode = new ReflectionMethod(DirectoryPublicationService::class, 'activeRefreshRetryCode');
$retryDelay = new ReflectionMethod(DirectoryPublicationService::class, 'activeRefreshRetryDelay');

$transient = [
    DirectoryOnboardingRequestException::TRANSPORT => 'permission_refresh_transport',
    DirectoryOnboardingRequestException::INVALID_RESPONSE => 'permission_refresh_invalid_response',
    DirectoryOnboardingRequestException::SERVER_ERROR => 'permission_refresh_server_error',
    DirectoryOnboardingRequestException::RATE_LIMITED => 'permission_refresh_rate_limited',
    DirectoryOnboardingRequestException::CREDENTIAL_UNAVAILABLE => 'permission_refresh_credential_unavailable',
];
foreach ($transient as $reason => $expectedCode) {
    $error = new DirectoryOnboardingRequestException($reason);
    activeRefreshCheck($permanent->invoke(null, $error) === false,
        "$reason must remain retryable instead of stopping publication");
    $code = $retryCode->invoke(null, $error);
    activeRefreshCheck($code === $expectedCode, "$reason produced an unstable retry code");
    activeRefreshCheck(strlen($code) <= 64, "$reason produced an overlong retry code");
}

$unknown = $retryCode->invoke(null, new RuntimeException('unexpected runtime failure'));
activeRefreshCheck($unknown === 'permission_refresh_retry', 'Unknown transient failure lacked a stable retry code');
activeRefreshCheck($permanent->invoke(null, new RuntimeException('unexpected runtime failure')) === false,
    'An unknown runtime failure stopped publication without authoritative closure evidence');

$hardFailures = [
    new DirectoryOnboardingRequestException(DirectoryOnboardingRequestException::HTTP_ERROR, null, 405),
    new DirectoryOnboardingRemoteRejectedException(403, 'nextcloud_onboarding_denied'),
    new BindingPublicationStoppedException('publication stopped'),
    new UnsupportedPublicationAclException('unsupported acl'),
    new DomainException('target conflict'),
    new JsonException('invalid retained intent'),
];
foreach ($hardFailures as $error) {
    activeRefreshCheck($permanent->invoke(null, $error) === true,
        get_class($error) . ' was not treated as an authoritative stop');
}

$delays = [1 => 15, 2 => 30, 3 => 60, 4 => 120, 5 => 240, 6 => 240];
foreach ($delays as $attempt => $expected) {
    activeRefreshCheck($retryDelay->invoke(null, $attempt) === $expected,
        "Attempt $attempt used an unexpected retry delay");
}

echo "PASS: active permission refresh retries transient failures with bounded exponential backoff and stops only on authoritative closure\n";
