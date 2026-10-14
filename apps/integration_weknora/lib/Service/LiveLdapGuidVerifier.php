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

        $identity = $this->identity($user);
        return $identity !== null && hash_equals($identity['object_guid'], strtolower($objectGuid));
    }

    /** A fresh lookup: names, email and cached LDAP attributes are not proof. */
    public function identity(IUser $user): ?array {
        if (!$user->isEnabled() || $user->getBackendClassName() !== 'LDAP') {
            return null;
        }
        $directoryId = $this->config->getAppValue('integration_weknora', 'ad_directory_id', '');
        if ($directoryId === '') {
            throw new \RuntimeException('AD directory identity is not configured');
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
            $result = @ldap_read($connection, $dn, '(objectClass=user)', ['objectGUID', 'userAccountControl'],
                0, 1, 5, LDAP_DEREF_NEVER);
            if ($result === false) {
                throw new \RuntimeException('Live AD lookup failed');
            }
            $entryCount = ldap_count_entries($connection, $result);
            if ($entryCount === false) {
                throw new \RuntimeException('Live AD entry count failed');
            }
            if ($entryCount !== 1) {
                return null;
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
            $account = @ldap_get_values($connection, $entry, 'userAccountControl');
            if ($account === false || ($account['count'] ?? null) !== 1 ||
                !ctype_digit((string)($account[0] ?? ''))) {
                throw new \RuntimeException('AD account status is unavailable');
            }
            if (((int)$account[0] & 2) !== 0) {
                return null;
            }
            return ['directory_id' => $directoryId, 'object_guid' => $actualGuid];
        } catch (\Throwable $exception) {
            throw new \RuntimeException('Live AD identity proof unavailable', 0, $exception);
        } finally {
            if ($connection instanceof \LDAP\Connection) {
                @ldap_unbind($connection);
            }
        }
    }

    /** Resolve the stable identity of a real Nextcloud LDAP group. */
    public function groupIdentity(string $gid, IUser $context): array {
        if (!$this->ldapFactory->isAvailable() || !function_exists('ldap_read')) {
            throw new \RuntimeException('LDAP provider is unavailable');
        }
        $directoryId = $this->config->getAppValue('integration_weknora', 'ad_directory_id', '');
        if ($directoryId === '' || !$context->isEnabled()) {
            throw new \RuntimeException('Trusted LDAP group context is unavailable');
        }
        $connection = null;
        try {
            $provider = $this->ldapFactory->getLDAPProvider();
            $dn = $provider->getGroupDN($gid);
            if (!is_string($dn) || $dn === '') {
                throw new \RuntimeException('LDAP group DN is unavailable');
            }
            $connection = $provider->getGroupLDAPConnection($gid);
            $result = @ldap_read($connection, $dn, '(objectClass=group)', ['objectGUID'], 0, 1, 5, LDAP_DEREF_NEVER);
            if ($result === false || ldap_count_entries($connection, $result) !== 1 ||
                ($entry = ldap_first_entry($connection, $result)) === false) {
                throw new \RuntimeException('Live LDAP group lookup is unavailable');
            }
            $values = @ldap_get_values_len($connection, $entry, 'objectGUID');
            $guid = ($values !== false && ($values['count'] ?? 0) === 1 && is_string($values[0] ?? null))
                ? self::canonicalGuidFromBinary($values[0]) : null;
            if ($guid === null) {
                throw new \RuntimeException('Stable LDAP group identity is unavailable');
            }
            return ['directory_id' => $directoryId, 'object_guid' => $guid];
        } finally {
            if ($connection instanceof \LDAP\Connection) { @ldap_unbind($connection); }
        }
    }

    /** AD tokenGroups is computed from direct, nested and primary security groups. */
    public function isEffectiveGroupMember(IUser $user, string $gid, string $directoryId,
        string $objectGuid, string $groupGuid): bool {
        if ($user->getBackendClassName() !== 'LDAP' || !$user->isEnabled() ||
            $this->config->getAppValue('integration_weknora', 'ad_directory_id', '') !== $directoryId ||
            !$this->ldapFactory->isAvailable() || !function_exists('ldap_read')) {
            throw new \RuntimeException('Current directory membership is unavailable');
        }
        $connection = null;
        try {
            $provider = $this->ldapFactory->getLDAPProvider();
            $userDn = $provider->getUserDN($user->getUID());
            $groupDn = $provider->getGroupDN($gid);
            $connection = $provider->getLDAPConnection($user->getUID());
            if ($userDn === '' || $groupDn === '') { throw new \RuntimeException('Directory DN is unavailable'); }
            $userResult = @ldap_read($connection, $userDn, '(objectClass=user)',
                ['objectGUID', 'userAccountControl', 'tokenGroups'], 0, 1, 5, LDAP_DEREF_NEVER);
            $groupResult = @ldap_read($connection, $groupDn, '(objectClass=group)',
                ['objectGUID', 'objectSID', 'groupType'], 0, 1, 5, LDAP_DEREF_NEVER);
            if ($userResult === false || $groupResult === false ||
                ldap_count_entries($connection, $userResult) !== 1 || ldap_count_entries($connection, $groupResult) !== 1 ||
                ($userEntry = ldap_first_entry($connection, $userResult)) === false ||
                ($groupEntry = ldap_first_entry($connection, $groupResult)) === false) {
                throw new \RuntimeException('Current group membership lookup failed');
            }
            $readGuid = static function ($connection, $entry): ?string {
                $values = @ldap_get_values_len($connection, $entry, 'objectGUID');
                return $values !== false && ($values['count'] ?? 0) === 1
                    ? self::canonicalGuidFromBinary($values[0]) : null;
            };
            $actualUser = $readGuid($connection, $userEntry);
            $actualGroup = $readGuid($connection, $groupEntry);
            if ($actualUser !== $objectGuid || $actualGroup !== $groupGuid) { return false; }
            $account = @ldap_get_values($connection, $userEntry, 'userAccountControl');
            if ($account === false || ($account['count'] ?? 0) !== 1 || !ctype_digit($account[0])) {
                throw new \RuntimeException('Directory account status is unavailable');
            }
            if (((int)$account[0] & 2) !== 0) { return false; }
            $type = @ldap_get_values($connection, $groupEntry, 'groupType');
            if ($type === false || ($type['count'] ?? 0) !== 1 || !preg_match('/\A-?[0-9]+\z/D', $type[0]) ||
                (((int)$type[0] & 0x80000000) === 0)) {
                throw new UnsupportedPublicationAclException('Distribution groups require an effective membership adapter');
            }
            $sid = @ldap_get_values_len($connection, $groupEntry, 'objectSID');
            $tokens = @ldap_get_values_len($connection, $userEntry, 'tokenGroups');
            if ($sid === false || ($sid['count'] ?? 0) !== 1 || $tokens === false || !isset($tokens['count'])) {
                throw new \RuntimeException('Computed AD group membership is unavailable');
            }
            return self::containsSid($sid[0], $tokens);
        } finally {
            if ($connection instanceof \LDAP\Connection) { @ldap_unbind($connection); }
        }
    }

    /** Binary SID equality, never names; tokenGroups includes the primary group. */
    public static function containsSid(string $groupSid, array $tokens): bool {
        $valid = static fn (mixed $sid): bool => is_string($sid) && strlen($sid) >= 8 &&
            ord($sid[0]) === 1 && ord($sid[1]) <= 15 && strlen($sid) === 8 + 4 * ord($sid[1]);
        if (!$valid($groupSid) || !is_int($tokens['count'] ?? null) || $tokens['count'] < 0 || $tokens['count'] > 100000) {
            throw new \RuntimeException('Invalid directory security identifier');
        }
        $found = false;
        for ($i = 0; $i < $tokens['count']; $i++) {
            if (!$valid($tokens[$i] ?? null)) { throw new \RuntimeException('Incomplete directory membership'); }
            $found = $found || hash_equals($groupSid, $tokens[$i]);
        }
        return $found;
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
