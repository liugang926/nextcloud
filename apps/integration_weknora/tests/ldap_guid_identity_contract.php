<?php

declare(strict_types=1);

// Minimal public-interface doubles keep this contract runnable without a
// Nextcloud installation or an AD server. The live LDAP path needs an AD
// integration test with two real test accounts.
namespace OCP {
    interface IUser {
        public function getUID(): string;
        public function getBackendClassName(): string;
    }
    interface IConfig {
        public function getAppValue(string $appName, string $key, string $default = ''): string;
    }
}

namespace OCP\LDAP {
    interface ILDAPProviderFactory {
        public function isAvailable(): bool;
        public function getLDAPProvider(): object;
    }
}

namespace {
    use OCA\IntegrationWeknora\Service\LiveLdapGuidVerifier;
    use OCP\IConfig;
    use OCP\IUser;
    use OCP\LDAP\ILDAPProviderFactory;

    require_once __DIR__ . '/../lib/Service/LiveLdapGuidVerifier.php';

    function check(bool $condition, string $message): void {
        if (!$condition) {
            throw new \RuntimeException($message);
        }
    }

    function mustFail(callable $operation, string $message): void {
        try {
            $operation();
        } catch (\RuntimeException $exception) {
            return;
        }
        throw new \RuntimeException($message);
    }

    $raw = hex2bin('33221100554477668899aabbccddeeff');
    check($raw !== false, 'invalid fixture');
    check(LiveLdapGuidVerifier::canonicalGuidFromBinary($raw) ===
        '00112233-4455-6677-8899-aabbccddeeff', 'AD byte order changed');
    foreach (['', substr($raw, 0, 15), $raw . "\0", '00112233-4455-6677-8899-aabbccddeeff'] as $invalid) {
        check(LiveLdapGuidVerifier::canonicalGuidFromBinary($invalid) === null,
            'non-binary or wrong-length GUID accepted');
    }

    $factory = new class implements ILDAPProviderFactory {
        public function isAvailable(): bool { return false; }
        public function getLDAPProvider(): object { throw new \RuntimeException('must not open LDAP'); }
    };
    $configured = new class implements IConfig {
        public function getAppValue(string $appName, string $key, string $default = ''): string {
            return 'corp-ad';
        }
    };
    $missingConfig = new class implements IConfig {
        public function getAppValue(string $appName, string $key, string $default = ''): string {
            return $default;
        }
    };
    $ldapUser = new class implements IUser {
        public function getUID(): string { return 'employee'; }
        public function getBackendClassName(): string { return 'LDAP'; }
    };
    $localUser = new class implements IUser {
        public function getUID(): string { return 'employee'; }
        public function getBackendClassName(): string { return 'Database'; }
    };
    $guid = '00112233-4455-6677-8899-aabbccddeeff';

    $originalOverride = getenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY');
    $originalLocalCompose = getenv('WEKNORA_LOCAL_COMPOSE');
    $originalDevHttp = getenv('WEKNORA_EVENT_DEV_HTTP');
    try {
        putenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=0');
        putenv('WEKNORA_LOCAL_COMPOSE=1');
        putenv('WEKNORA_EVENT_DEV_HTTP=1');
        $verifier = new LiveLdapGuidVerifier($factory, $configured);
        check(!$verifier->matches($localUser, 'corp-ad', $guid), 'local account passed strict proof');
        check(!$verifier->matches($ldapUser, 'other-ad', $guid), 'wrong directory passed');
        mustFail(fn () => $verifier->matches($ldapUser, 'corp-ad', $guid),
            'unavailable LDAP provider was accepted');
        mustFail(fn () => (new LiveLdapGuidVerifier($factory, $missingConfig))
            ->matches($ldapUser, 'corp-ad', $guid), 'unconfigured directory was accepted');

        putenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=1');
        putenv('WEKNORA_LOCAL_COMPOSE=0');
        check(!$verifier->matches($localUser, 'corp-ad', $guid),
            'unverified account bypassed outside the local Compose stack');
        putenv('WEKNORA_LOCAL_COMPOSE=1');
        putenv('WEKNORA_EVENT_DEV_HTTP=0');
        check(!$verifier->matches($localUser, 'corp-ad', $guid),
            'unverified account bypassed without HTTP dev mode');
        putenv('WEKNORA_EVENT_DEV_HTTP=1');
        check($verifier->matches($localUser, 'corp-ad', $guid),
            'explicit synthetic smoke opt-in did not work');
    } finally {
        $originalOverride === false ? putenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY')
            : putenv('WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=' . $originalOverride);
        $originalLocalCompose === false ? putenv('WEKNORA_LOCAL_COMPOSE')
            : putenv('WEKNORA_LOCAL_COMPOSE=' . $originalLocalCompose);
        $originalDevHttp === false ? putenv('WEKNORA_EVENT_DEV_HTTP')
            : putenv('WEKNORA_EVENT_DEV_HTTP=' . $originalDevHttp);
    }
    echo "LDAP GUID identity contract passed\n";
}
