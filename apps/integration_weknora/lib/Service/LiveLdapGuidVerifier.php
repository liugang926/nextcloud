<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IConfig;
use OCP\IUser;
use OCP\LDAP\ILDAPProviderFactory;

/** Prove a Nextcloud LDAP account's current AD objectGUID before granting access. */
final class LiveLdapGuidVerifier {
    public function __construct(
        private ILDAPProviderFactory $ldapFactory,
        private IConfig $config,
    ) {
    }

    /**
     * A false result is a known mismatch. An unavailable directory throws so
     * the machine authorization endpoint returns a fail-closed 503.
     */
    public function matches(IUser $user, string $directoryId, string $objectGuid): bool {
        // Synthetic accounts are useful for the isolated HTTP permission
        // probes. This opt-in must never be set in an enterprise deployment.
        if (getenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY') === '1' &&
            getenv('WEKNORA_LOCAL_COMPOSE') === '1' &&
            getenv('WEKNORA_EVENT_DEV_HTTP') === '1') {
            return true;
        }

        if ($user->getBackendClassName() !== 'LDAP') {
            return false;
        }

        $expectedDirectoryId = $this->config->getAppValue('integration_weknora', 'ad_directory_id', '');
        if ($expectedDirectoryId === '') {
            throw new \RuntimeException('AD directory identity is not configured');
        }
        if (!hash_equals($expectedDirectoryId, $directoryId)) {
            return false;
        }

        if (!function_exists('ldap_read') || !$this->ldapFactory->isAvailable()) {
            throw new \RuntimeException('LDAP provider is unavailable');
        }

        $connection = null;
        try {
            $provider = $this->ldapFactory->getLDAPProvider();
            $uid = $user->getUID();
            $dn = $provider->getUserDN($uid);
            $connection = $provider->getLDAPConnection($uid);
            // getUserAttribute() is unsuitable here: user_ldap may return a
            // TTL-cached GUID after an AD object was removed or recreated.
            $result = @ldap_read($connection, $dn, '(objectClass=*)', ['objectGUID'],
                0, 1, 5, LDAP_DEREF_NEVER);
            if ($result === false) {
                throw new \RuntimeException('Live AD lookup failed');
            }
            $entryCount = ldap_count_entries($connection, $result);
            if ($entryCount === false) {
                throw new \RuntimeException('Live AD entry count failed');
            }
            if ($entryCount !== 1) {
                return false;
            }
            $entry = ldap_first_entry($connection, $result);
            if ($entry === false) {
                throw new \RuntimeException('Live AD lookup returned no entry');
            }
            $values = @ldap_get_values_len($connection, $entry, 'objectGUID');
            if ($values === false || !isset($values['count']) || $values['count'] !== 1 ||
                !isset($values[0]) || !is_string($values[0])) {
                throw new \RuntimeException('AD objectGUID is missing or ambiguous');
            }
            $actualGuid = self::canonicalGuidFromBinary($values[0]);
            if ($actualGuid === null) {
                throw new \RuntimeException('AD objectGUID has invalid length');
            }
            return hash_equals($actualGuid, strtolower($objectGuid));
        } catch (\Throwable $exception) {
            throw new \RuntimeException('Live AD identity proof unavailable', 0, $exception);
        } finally {
            if ($connection instanceof \LDAP\Connection) {
                @ldap_unbind($connection);
            }
        }
    }

    /** AD stores the first three UUID fields in little-endian byte order. */
    public static function canonicalGuidFromBinary(string $binary): ?string {
        if (strlen($binary) !== 16) {
            return null;
        }
        $hex = bin2hex($binary);
        return substr($hex, 6, 2) . substr($hex, 4, 2) . substr($hex, 2, 2) . substr($hex, 0, 2)
            . '-' . substr($hex, 10, 2) . substr($hex, 8, 2)
            . '-' . substr($hex, 14, 2) . substr($hex, 12, 2)
            . '-' . substr($hex, 16, 4)
            . '-' . substr($hex, 20, 12);
    }
}
